"""Testes do serviço de resumo por IA — httpx e banco mockados.

A geração de resumo é tolerante a falha por contrato: sem chave, ou diante de
erro HTTP, retorna ``None`` e nunca levanta; a persistência só ocorre quando há
texto. Estes testes travam esse comportamento sem tocar em rede/banco reais.

F0 da frente de IA (C2): a chamada HTTP vive em ``app/ia/cliente.py`` — os
mocks de rede são aplicados lá; o comportamento observável de ``ia_resumo``
permanece o mesmo de antes do refactor.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

from app.config import Settings
from app.ia import cliente
from app.services import ia_resumo

_DADOS = {"cidade": "Canoas", "tipo_ocorrencia": "Vazamento"}


def _settings(**overrides) -> Settings:
    base = dict(
        session_secret="segredo-real-de-teste-nao-default",
        csrf_secret="outro-segredo-real-de-teste-nao-default",
    )
    base.update(overrides)
    return Settings(**base)


class _FakeResp:
    def __init__(self, payload: dict):
        self._payload = payload

    def raise_for_status(self) -> None:  # noqa: D401 - stub
        return None

    def json(self) -> dict:
        return self._payload


class _FakeClient:
    """Substituto de httpx.AsyncClient: captura o POST e devolve um payload fixo."""

    def __init__(self, payload: dict, capture: dict):
        self._payload = payload
        self._capture = capture

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, json=None, headers=None):
        self._capture["url"] = url
        self._capture["json"] = json
        self._capture["headers"] = headers
        return _FakeResp(self._payload)


async def test_resumir_desligado_sem_chave():
    with patch.object(ia_resumo, "get_settings", return_value=_settings(ia_triagem_api_key="")):
        texto = await ia_resumo.resumir_chamado("t", "d", "Registro de Ocorrência", _DADOS)
    assert texto is None


async def test_resumir_retorna_texto_e_monta_prompt():
    payload = {"choices": [{"message": {"content": "  Resumo do chamado.  "}}]}
    capture: dict = {}
    fake = _FakeClient(payload, capture)
    client_factory = MagicMock(return_value=fake)
    with (
        patch.object(
            ia_resumo, "get_settings", return_value=_settings(ia_triagem_api_key="k-123")
        ),
        patch.object(cliente.httpx, "AsyncClient", client_factory),
    ):
        texto = await ia_resumo.resumir_chamado(
            "Assunto X", "Descrição Y", "Registro de Ocorrência", _DADOS
        )
    assert texto == "Resumo do chamado."  # strip aplicado
    # O conteúdo enviado à IA inclui assunto, descrição e os campos rotulados.
    conteudo = capture["json"]["messages"][1]["content"]
    assert "Assunto X" in conteudo and "Descrição Y" in conteudo
    assert "Cidade: Canoas" in conteudo
    assert capture["headers"]["Authorization"] == "Bearer k-123"
    # C6: o timeout unificado (30 s, settings.ia_triagem_timeout_s) chega ao httpx.
    assert client_factory.call_args.kwargs["timeout"] == 30.0


async def test_resumir_engole_erro_http():
    def _boom(*a, **k):
        raise ia_resumo.httpx.HTTPError("timeout")

    fake = _FakeClient({}, {})
    fake.post = AsyncMock(side_effect=_boom)
    with (
        patch.object(ia_resumo, "get_settings", return_value=_settings(ia_triagem_api_key="k")),
        patch.object(cliente.httpx, "AsyncClient", MagicMock(return_value=fake)),
    ):
        texto = await ia_resumo.resumir_chamado("t", "d", "Registro de Ocorrência", _DADOS)
    assert texto is None


async def test_gerar_e_salvar_persiste_quando_ha_texto():
    execute = AsyncMock()

    @asynccontextmanager
    async def _fake_conn():
        yield type("C", (), {"execute": execute})()

    with (
        patch.object(ia_resumo, "resumir_chamado", AsyncMock(return_value="Resumo.")),
        patch.object(ia_resumo, "admin_connection", _fake_conn),
    ):
        await ia_resumo.gerar_e_salvar_resumo("cid", "t", "d", "Registro de Ocorrência", _DADOS)

    execute.assert_awaited_once()
    args = execute.await_args.args
    assert "UPDATE chamados" in args[0]
    assert args[1] == "cid" and args[2] == "Resumo."


async def test_gerar_e_salvar_nao_persiste_sem_texto():
    execute = AsyncMock()

    @asynccontextmanager
    async def _fake_conn():
        yield type("C", (), {"execute": execute})()

    with (
        patch.object(ia_resumo, "resumir_chamado", AsyncMock(return_value=None)),
        patch.object(ia_resumo, "admin_connection", _fake_conn),
    ):
        await ia_resumo.gerar_e_salvar_resumo("cid", "t", "d", "Registro de Ocorrência", _DADOS)

    execute.assert_not_awaited()
