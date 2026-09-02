"""Monitor de sessão do wuzapi (app/services/wuzapi_monitor.py) — Fase 1 da
migração (ver docs/pesquisa_wuzapi_migracao.md).
"""

import asyncio

import httpx
import pytest

from app.config import Settings
from app.services.wuzapi_monitor import (
    _alertar,
    _loop_monitor,
    _StatusWuzapi,
    _verificar_status,
    iniciar_monitor,
)


def _settings(**overrides) -> Settings:
    base = dict(
        session_secret="segredo-real-de-teste-nao-default",
        csrf_secret="outro-segredo-real-de-teste-nao-default",
        wuzapi_base_url="http://wuzapi:8080",
        wuzapi_token="token-do-usuario",
        wuzapi_monitor_alerta_email="gestor@bondmann.com.br",
        wuzapi_monitor_intervalo_s=0.01,
        wuzapi_monitor_falhas_para_alertar=2,
    )
    base.update(overrides)
    return Settings(**base)


def _mock_httpx(monkeypatch, handler) -> None:
    class _FakeAsyncClient:
        def __init__(self, *a, **kw):
            self._transport = httpx.MockTransport(handler)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, headers=None):
            req = httpx.Request("GET", url, headers=headers)
            return await self._transport.handle_async_request(req)

    monkeypatch.setattr(httpx, "AsyncClient", _FakeAsyncClient)


# --------------------------------------------------------------------------
# iniciar_monitor — kill switches
# --------------------------------------------------------------------------


def test_sem_wuzapi_configurado_nao_inicia():
    assert iniciar_monitor(_settings(wuzapi_base_url="", wuzapi_token="")) is None


def test_intervalo_zero_desliga():
    assert iniciar_monitor(_settings(wuzapi_monitor_intervalo_s=0)) is None


async def test_configurado_inicia_task():
    task = iniciar_monitor(_settings())
    assert task is not None
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


# --------------------------------------------------------------------------
# _verificar_status
# --------------------------------------------------------------------------


async def test_status_ok(monkeypatch):
    _mock_httpx(
        monkeypatch,
        lambda req: httpx.Response(200, json={"code": 200, "data": {"Connected": True, "LoggedIn": True}, "success": True}),
    )
    status = await _verificar_status(_settings())
    assert status.ok


async def test_status_desconectado(monkeypatch):
    _mock_httpx(
        monkeypatch,
        lambda req: httpx.Response(200, json={"code": 200, "data": {"Connected": False, "LoggedIn": False}, "success": True}),
    )
    status = await _verificar_status(_settings())
    assert not status.ok
    assert "desconectada" in status.motivo


async def test_status_pareamento_perdido(monkeypatch):
    """Connected=true mas LoggedIn=false: sessão viva mas sem pareamento —
    cenário real observado (celular desparelhado sem o processo cair)."""
    _mock_httpx(
        monkeypatch,
        lambda req: httpx.Response(200, json={"code": 200, "data": {"Connected": True, "LoggedIn": False}, "success": True}),
    )
    status = await _verificar_status(_settings())
    assert not status.ok
    assert "pareamento" in status.motivo


async def test_status_token_invalido(monkeypatch):
    _mock_httpx(monkeypatch, lambda req: httpx.Response(401, json={"code": 401, "error": "unauthorized"}))
    status = await _verificar_status(_settings())
    assert not status.ok
    assert "401" in status.motivo


async def test_status_erro_de_rede_nao_lanca(monkeypatch):
    def _handler(req):
        raise httpx.ConnectError("sem rota")

    _mock_httpx(monkeypatch, _handler)
    status = await _verificar_status(_settings())
    assert not status.ok
    assert "rede" in status.motivo


# --------------------------------------------------------------------------
# _alertar — usa enviar_email (mockado, sem credenciais reais de e-mail)
# --------------------------------------------------------------------------


async def test_alertar_sem_email_configurado_nao_envia(monkeypatch):
    chamadas = []
    monkeypatch.setattr("app.services.wuzapi_monitor.enviar_email", lambda *a, **kw: chamadas.append(a))
    await _alertar(_settings(wuzapi_monitor_alerta_email=""), recuperou=False, motivo="x")
    assert chamadas == []


async def test_alertar_manda_email_de_falha(monkeypatch):
    chamadas = []

    async def _fake_enviar(para, assunto, corpo, **kw):
        chamadas.append((para, assunto, corpo))
        return True

    monkeypatch.setattr("app.services.wuzapi_monitor.enviar_email", _fake_enviar)
    await _alertar(_settings(), recuperou=False, motivo="sessão desconectada do WhatsApp (Connected=false)")
    assert len(chamadas) == 1
    para, assunto, corpo = chamadas[0]
    assert para == "gestor@bondmann.com.br"
    assert "fora do ar" in assunto
    assert "desconectada" in corpo


async def test_alertar_manda_email_de_recuperacao(monkeypatch):
    chamadas = []

    async def _fake_enviar(para, assunto, corpo, **kw):
        chamadas.append((para, assunto, corpo))
        return True

    monkeypatch.setattr("app.services.wuzapi_monitor.enviar_email", _fake_enviar)
    await _alertar(_settings(), recuperou=True, motivo="")
    assert "voltou ao normal" in chamadas[0][1]


async def test_alertar_falha_de_envio_nao_lanca(monkeypatch):
    async def _fake_enviar(*a, **kw):
        raise RuntimeError("mailgun fora do ar")

    monkeypatch.setattr("app.services.wuzapi_monitor.enviar_email", _fake_enviar)
    await _alertar(_settings(), recuperou=False, motivo="x")  # não lança


# --------------------------------------------------------------------------
# _loop_monitor — limiar de falhas consecutivas e reset ao recuperar
# --------------------------------------------------------------------------


async def test_loop_so_alerta_apos_falhas_consecutivas(monkeypatch):
    """1 falha isolada não deve gerar e-mail; a 2ª consecutiva (limiar=2) sim."""
    respostas = iter(
        [
            {"Connected": False, "LoggedIn": False},  # falha 1 — ainda não alerta
            {"Connected": False, "LoggedIn": False},  # falha 2 — alerta aqui
        ]
    )
    alertas = []

    async def _fake_verificar(settings):
        dados = next(respostas, None)
        if dados is None:
            raise asyncio.CancelledError
        ok = dados["Connected"] and dados["LoggedIn"]
        return _StatusWuzapi(ok, "" if ok else "desconectada")

    async def _fake_alertar(settings, *, recuperou, motivo):
        alertas.append(recuperou)

    monkeypatch.setattr("app.services.wuzapi_monitor._verificar_status", _fake_verificar)
    monkeypatch.setattr("app.services.wuzapi_monitor._alertar", _fake_alertar)

    task = asyncio.create_task(_loop_monitor(_settings(wuzapi_monitor_falhas_para_alertar=2)))
    for _ in range(20):
        await asyncio.sleep(0)
        if alertas:
            break
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert alertas == [False]  # só o alerta de falha, uma vez


async def test_loop_alerta_recuperacao_depois_de_falha(monkeypatch):
    respostas = iter([False, False, True])
    alertas = []

    async def _fake_verificar(settings):
        ok = next(respostas)
        return _StatusWuzapi(ok, "" if ok else "desconectada")

    async def _fake_alertar(settings, *, recuperou, motivo):
        alertas.append(recuperou)

    monkeypatch.setattr("app.services.wuzapi_monitor._verificar_status", _fake_verificar)
    monkeypatch.setattr("app.services.wuzapi_monitor._alertar", _fake_alertar)

    task = asyncio.create_task(_loop_monitor(_settings(wuzapi_monitor_falhas_para_alertar=2)))
    for _ in range(20):
        await asyncio.sleep(0)
        if len(alertas) >= 2:
            break
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert alertas == [False, True]
