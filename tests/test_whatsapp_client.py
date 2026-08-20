"""Cliente de envio do WhatsApp Cloud API (app/whatsapp_client.py)."""

import json

import httpx
import pytest

from app.config import Settings
from app.whatsapp_client import (
    WhatsAppEnvioFalhou,
    WhatsAppNaoConfigurado,
    baixar_midia,
    enviar_documento,
    enviar_mensagem_texto,
)


def _settings(**overrides) -> Settings:
    base = dict(
        session_secret="segredo-real-de-teste-nao-default",
        csrf_secret="outro-segredo-real-de-teste-nao-default",
        whatsapp_access_token="",
        whatsapp_phone_number_id="",
    )
    base.update(overrides)
    return Settings(**base)


async def test_sem_config_recusa_envio(monkeypatch):
    monkeypatch.setattr("app.whatsapp_client.get_settings", lambda: _settings())
    with pytest.raises(WhatsAppNaoConfigurado):
        await enviar_mensagem_texto("5551994105691", "oi")


async def test_envio_ok(monkeypatch):
    settings = _settings(whatsapp_access_token="token-teste", whatsapp_phone_number_id="123")
    monkeypatch.setattr("app.whatsapp_client.get_settings", lambda: settings)

    def _handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer token-teste"
        assert request.url.path == "/v21.0/123/messages"
        return httpx.Response(200, json={"messages": [{"id": "wamid.ABC"}]})

    transport = httpx.MockTransport(_handler)
    original_async_client = httpx.AsyncClient
    monkeypatch.setattr(
        "httpx.AsyncClient",
        lambda **kw: original_async_client(transport=transport, **{k: v for k, v in kw.items() if k != "transport"}),
    )

    resultado = await enviar_mensagem_texto("5551994105691", "oi")
    assert resultado["messages"][0]["id"] == "wamid.ABC"


async def test_envio_recusado_pela_graph_api(monkeypatch):
    settings = _settings(whatsapp_access_token="token-teste", whatsapp_phone_number_id="123")
    monkeypatch.setattr("app.whatsapp_client.get_settings", lambda: settings)

    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": {"message": "Recipient invalid"}})

    transport = httpx.MockTransport(_handler)
    original_async_client = httpx.AsyncClient
    monkeypatch.setattr(
        "httpx.AsyncClient",
        lambda **kw: original_async_client(transport=transport, **{k: v for k, v in kw.items() if k != "transport"}),
    )

    with pytest.raises(WhatsAppEnvioFalhou):
        await enviar_mensagem_texto("5551994105691", "oi")


# --- enviar_documento (formulário obrigatório do RH, por exemplo) ----------


async def test_enviar_documento_sem_config_recusa_envio(monkeypatch):
    monkeypatch.setattr("app.whatsapp_client.get_settings", lambda: _settings())
    with pytest.raises(WhatsAppNaoConfigurado):
        await enviar_documento("5551994105691", "https://portal.example/form.docx")


async def test_enviar_documento_ok_manda_link_nome_e_legenda(monkeypatch):
    settings = _settings(whatsapp_access_token="token-teste", whatsapp_phone_number_id="123")
    monkeypatch.setattr("app.whatsapp_client.get_settings", lambda: settings)

    capturado: dict = {}

    def _handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer token-teste"
        assert request.url.path == "/v21.0/123/messages"
        capturado["payload"] = json.loads(request.content)
        return httpx.Response(200, json={"messages": [{"id": "wamid.DOC"}]})

    transport = httpx.MockTransport(_handler)
    original_async_client = httpx.AsyncClient
    monkeypatch.setattr(
        "httpx.AsyncClient",
        lambda **kw: original_async_client(transport=transport, **{k: v for k, v in kw.items() if k != "transport"}),
    )

    resultado = await enviar_documento(
        "5551994105691",
        "https://portal.example/fb031.docx",
        nome_arquivo="FB031.docx",
        legenda="FB031 — Solicitação de contratação",
    )

    assert resultado["messages"][0]["id"] == "wamid.DOC"
    payload = capturado["payload"]
    assert payload["type"] == "document"
    assert payload["document"] == {
        "link": "https://portal.example/fb031.docx",
        "filename": "FB031.docx",
        "caption": "FB031 — Solicitação de contratação",
    }


async def test_enviar_documento_recusado_pela_graph_api(monkeypatch):
    settings = _settings(whatsapp_access_token="token-teste", whatsapp_phone_number_id="123")
    monkeypatch.setattr("app.whatsapp_client.get_settings", lambda: settings)

    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": {"message": "Media link inválido"}})

    transport = httpx.MockTransport(_handler)
    original_async_client = httpx.AsyncClient
    monkeypatch.setattr(
        "httpx.AsyncClient",
        lambda **kw: original_async_client(transport=transport, **{k: v for k, v in kw.items() if k != "transport"}),
    )

    with pytest.raises(WhatsAppEnvioFalhou):
        await enviar_documento("5551994105691", "https://portal.example/form.docx")


# --- baixar_midia (foto recebida no intake) --------------------------------


def _mock_transport(monkeypatch, handler) -> None:
    transport = httpx.MockTransport(handler)
    original_async_client = httpx.AsyncClient
    monkeypatch.setattr(
        "httpx.AsyncClient",
        lambda **kw: original_async_client(
            transport=transport, **{k: v for k, v in kw.items() if k != "transport"}
        ),
    )


async def test_baixar_midia_sem_token_devolve_none(monkeypatch):
    """Kill switch: sem token configurado nem tenta a rede."""
    monkeypatch.setattr("app.whatsapp_client.get_settings", lambda: _settings())
    assert await baixar_midia("midia-1") is None


async def test_baixar_midia_sucesso(monkeypatch):
    settings = _settings(whatsapp_access_token="token-teste")
    monkeypatch.setattr("app.whatsapp_client.get_settings", lambda: settings)

    def _handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer token-teste"
        if request.url.host == "graph.facebook.com":
            return httpx.Response(
                200,
                json={
                    "url": "https://lookaside.example/midia-1",
                    "mime_type": "image/jpeg",
                    "file_size": 4,
                },
            )
        return httpx.Response(200, content=b"\xff\xd8\xff\xd9")

    _mock_transport(monkeypatch, _handler)
    resultado = await baixar_midia("midia-1")
    assert resultado == (b"\xff\xd8\xff\xd9", "image/jpeg")


async def test_baixar_midia_acima_do_teto_declarado_devolve_none(monkeypatch):
    settings = _settings(whatsapp_access_token="token-teste", whatsapp_intake_midia_max_bytes=10)
    monkeypatch.setattr("app.whatsapp_client.get_settings", lambda: settings)

    def _handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "url": "https://lookaside.example/midia-1",
                "mime_type": "image/jpeg",
                "file_size": 999_999,
            },
        )

    _mock_transport(monkeypatch, _handler)
    assert await baixar_midia("midia-1") is None


async def test_baixar_midia_excede_teto_durante_download_devolve_none(monkeypatch):
    """Sem `file_size` declarado, o teto ainda é aplicado ao streamar."""
    settings = _settings(whatsapp_access_token="token-teste", whatsapp_intake_midia_max_bytes=4)
    monkeypatch.setattr("app.whatsapp_client.get_settings", lambda: settings)

    def _handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "graph.facebook.com":
            return httpx.Response(
                200, json={"url": "https://lookaside.example/midia-1", "mime_type": "image/jpeg"}
            )
        return httpx.Response(200, content=b"x" * 100)

    _mock_transport(monkeypatch, _handler)
    assert await baixar_midia("midia-1") is None


async def test_baixar_midia_erro_da_api_devolve_none(monkeypatch):
    """Falha nunca lança: a foto é contexto opcional do chamado."""
    settings = _settings(whatsapp_access_token="token-teste")
    monkeypatch.setattr("app.whatsapp_client.get_settings", lambda: settings)
    _mock_transport(monkeypatch, lambda request: httpx.Response(404, json={"error": {}}))
    assert await baixar_midia("midia-1") is None
