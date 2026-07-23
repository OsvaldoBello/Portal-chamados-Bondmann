"""Testes do cliente de modelo compatível-OpenAI (``app/ia/cliente.py`` — F0/C2).

O cliente é provedor-agnóstico e não lê settings: tudo chega por parâmetro.
Trava-se aqui o contrato HTTP (URL, payload, headers, timeout), o parsing da
resposta (texto com strip + tokens do ``usage``) e a propagação de erro — a
política de falha silenciosa é do CHAMADOR (Regra de Ouro #5), não do cliente.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.ia import cliente


class _FakeResp:
    def __init__(self, payload: dict):
        self._payload = payload

    def raise_for_status(self) -> None:  # noqa: D401 - stub
        return None

    def json(self) -> dict:
        return self._payload


class _FakeClient:
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


_MENSAGENS = [{"role": "user", "content": "olá"}]


async def test_completar_chat_monta_request_e_parseia_usage():
    payload = {
        "choices": [{"message": {"content": "  resposta  "}}],
        "usage": {"prompt_tokens": 120, "completion_tokens": 45},
    }
    capture: dict = {}
    factory = MagicMock(return_value=_FakeClient(payload, capture))
    with patch.object(cliente.httpx, "AsyncClient", factory):
        resposta = await cliente.completar_chat(
            mensagens=_MENSAGENS,
            model="gpt-5.4-mini",
            api_key="k-abc",
            base_url="https://api.openai.com/v1/",  # barra final é normalizada
            timeout_s=30.0,
            max_tokens=400,
        )
    assert resposta.conteudo == "resposta"
    assert resposta.tokens_entrada == 120 and resposta.tokens_saida == 45
    assert capture["url"] == "https://api.openai.com/v1/chat/completions"
    assert capture["json"]["model"] == "gpt-5.4-mini"
    assert capture["json"]["messages"] == _MENSAGENS
    assert capture["json"]["max_completion_tokens"] == 400
    assert "max_tokens" not in capture["json"]  # legado rejeitado pela família GPT-5
    assert capture["headers"]["Authorization"] == "Bearer k-abc"
    assert factory.call_args.kwargs["timeout"] == 30.0


async def test_completar_chat_sem_max_tokens_omite_o_campo():
    payload = {"choices": [{"message": {"content": "ok"}}]}
    capture: dict = {}
    with patch.object(cliente.httpx, "AsyncClient", MagicMock(return_value=_FakeClient(payload, capture))):
        resposta = await cliente.completar_chat(
            mensagens=_MENSAGENS,
            model="m",
            api_key="k",
            base_url="https://x.test/v1",
            timeout_s=5.0,
        )
    assert "max_completion_tokens" not in capture["json"]
    # Sem `usage` na resposta: tokens ficam None (não inventa números).
    assert resposta.tokens_entrada is None and resposta.tokens_saida is None


async def test_completar_chat_propaga_erro_http():
    fake = _FakeClient({}, {})
    fake.post = AsyncMock(side_effect=httpx.HTTPError("timeout"))
    with (
        patch.object(cliente.httpx, "AsyncClient", MagicMock(return_value=fake)),
        pytest.raises(httpx.HTTPError),
    ):
        await cliente.completar_chat(
            mensagens=_MENSAGENS,
            model="m",
            api_key="k",
            base_url="https://x.test/v1",
            timeout_s=5.0,
        )


async def test_json_mode_envia_response_format():
    payload = {"choices": [{"message": {"content": "{}"}}]}
    capture: dict = {}
    with patch.object(
        cliente.httpx, "AsyncClient", MagicMock(return_value=_FakeClient(payload, capture))
    ):
        await cliente.completar_chat(
            mensagens=_MENSAGENS,
            model="m",
            api_key="k",
            base_url="https://x.test/v1",
            timeout_s=5.0,
            json_mode=True,
        )
    assert capture["json"]["response_format"] == {"type": "json_object"}


def test_custo_usd_conhecido_e_desconhecido():
    assert cliente.custo_usd("gpt-5.4-mini", 1_000_000, 0) == 0.75
    assert cliente.custo_usd("gpt-5.4-mini", 0, 1_000_000) == 4.50
    assert cliente.custo_usd("modelo-fora-da-tabela", 100, 100) is None
    assert cliente.custo_usd("gpt-5.4-mini", None, 100) is None
