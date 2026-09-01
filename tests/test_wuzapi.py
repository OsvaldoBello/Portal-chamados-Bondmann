"""Provedor wuzapi: cliente de envio (app/whatsapp_client.py) e webhook
(app/routes/wuzapi.py) — Fase 0 da migração (ver docs/pesquisa_wuzapi_migracao.md).

O que estes testes protegem, em uma frase cada: o contrato REST do wuzapi é
diferente do da Meta em todo detalhe que importa (header de auth, nomes de
campo, 200 com `success: false`), e o webhook entrega a mensagem embrulhada de
um jeito que a extração ingênua perde justamente os casos mais comuns em
produção (PDF com legenda, JID com sufixo de device).
"""

import base64
import json

import httpx
import pytest

from app.config import Settings
from app.routes.wuzapi import (
    assinatura_valida,
    extrair_mensagens_wuzapi,
    telefone_do_jid,
)
from app.whatsapp_client import (
    WhatsAppEnvioFalhou,
    WhatsAppNaoConfigurado,
    baixar_midia,
    digitando,
    enviar_mensagem_texto,
    get_client,
    token_midia,
)


def _settings(**overrides) -> Settings:
    base = dict(
        session_secret="segredo-real-de-teste-nao-default",
        csrf_secret="outro-segredo-real-de-teste-nao-default",
        whatsapp_provider="wuzapi",
        wuzapi_base_url="http://wuzapi:8080",
        wuzapi_token="token-do-usuario",
    )
    base.update(overrides)
    return Settings(**base)


def _mock_http(monkeypatch, handler) -> None:
    """Troca o pool HTTP compartilhado por um client com MockTransport."""
    cliente = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    async def _fake():
        return cliente

    monkeypatch.setattr("app.whatsapp_client._cliente_http", _fake)


# --------------------------------------------------------------------------
# Seleção de provedor
# --------------------------------------------------------------------------


def test_provider_decide_o_cliente(monkeypatch):
    monkeypatch.setattr("app.whatsapp_client.get_settings", lambda: _settings())
    assert type(get_client()).__name__ == "WuzapiClient"

    monkeypatch.setattr(
        "app.whatsapp_client.get_settings", lambda: _settings(whatsapp_provider="meta")
    )
    assert type(get_client()).__name__ == "MetaClient"


def test_provider_invalido_recusa(monkeypatch):
    monkeypatch.setattr(
        "app.whatsapp_client.get_settings", lambda: _settings(whatsapp_provider="evolution")
    )
    with pytest.raises(WhatsAppNaoConfigurado):
        get_client()


async def test_sem_credenciais_do_wuzapi_recusa_envio(monkeypatch):
    monkeypatch.setattr(
        "app.whatsapp_client.get_settings", lambda: _settings(wuzapi_token="")
    )
    with pytest.raises(WhatsAppNaoConfigurado):
        await enviar_mensagem_texto("5551994105691", "oi")


# --------------------------------------------------------------------------
# Envio
# --------------------------------------------------------------------------


async def test_envio_usa_header_token_e_payload_do_wuzapi(monkeypatch):
    monkeypatch.setattr("app.whatsapp_client.get_settings", lambda: _settings())
    visto = {}

    def _handler(request: httpx.Request) -> httpx.Response:
        visto["path"] = request.url.path
        visto["token"] = request.headers.get("token")
        visto["corpo"] = json.loads(request.content)
        return httpx.Response(200, json={"code": 200, "success": True, "data": {"Id": "3EB0"}})

    _mock_http(monkeypatch, _handler)

    await enviar_mensagem_texto("5551994105691", "oi")
    assert visto["path"] == "/chat/send/text"
    assert visto["token"] == "token-do-usuario"
    assert visto["corpo"] == {"Phone": "5551994105691", "Body": "oi"}


async def test_200_com_success_false_e_falha(monkeypatch):
    """O wuzapi devolve 200 quando o número não existe no WhatsApp — sem esta
    checagem, o intake acharia que a resposta saiu."""
    monkeypatch.setattr("app.whatsapp_client.get_settings", lambda: _settings())
    _mock_http(
        monkeypatch,
        lambda request: httpx.Response(
            200, json={"code": 200, "success": False, "error": "no session"}
        ),
    )
    with pytest.raises(WhatsAppEnvioFalhou):
        await enviar_mensagem_texto("5551994105691", "oi")


# --------------------------------------------------------------------------
# Mídia
# --------------------------------------------------------------------------


DESCRITOR = {
    "Url": "https://mmg.whatsapp.net/d/f/abc",
    "Mimetype": "image/jpeg",
    "MediaKey": "chave",
    "FileSHA256": "sha",
    "FileEncSHA256": "shaenc",
    "FileLength": 2039,
}


def test_token_de_midia_faz_ida_e_volta():
    from app.whatsapp_client import _descritor_do_token

    token = token_midia(DESCRITOR)
    assert token.startswith("wuz:")
    assert _descritor_do_token(token) == DESCRITOR


async def test_baixar_midia_escolhe_endpoint_por_mimetype(monkeypatch):
    monkeypatch.setattr("app.whatsapp_client.get_settings", lambda: _settings())
    visto = {}
    conteudo = b"\xff\xd8\xff-binario"

    def _handler(request: httpx.Request) -> httpx.Response:
        visto["path"] = request.url.path
        visto["corpo"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "code": 200,
                "success": True,
                "data": {
                    "Data": "data:image/jpeg;base64," + base64.b64encode(conteudo).decode(),
                    "Mimetype": "image/jpeg",
                },
            },
        )

    _mock_http(monkeypatch, _handler)

    resultado = await baixar_midia(token_midia(DESCRITOR))
    assert resultado == (conteudo, "image/jpeg")
    assert visto["path"] == "/chat/downloadimage"
    # O corpo enviado é o descritor cru — é o que o endpoint espera decriptar.
    assert visto["corpo"]["MediaKey"] == "chave"


async def test_baixar_midia_respeita_o_teto_sem_baixar(monkeypatch):
    monkeypatch.setattr(
        "app.whatsapp_client.get_settings",
        lambda: _settings(whatsapp_intake_midia_max_bytes=1024),
    )

    def _handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("não deveria chegar a baixar")

    _mock_http(monkeypatch, _handler)

    assert await baixar_midia(token_midia({**DESCRITOR, "FileLength": 9_000_000})) is None


async def test_baixar_midia_com_referencia_estranha_nao_lanca(monkeypatch):
    monkeypatch.setattr("app.whatsapp_client.get_settings", lambda: _settings())
    assert await baixar_midia("media-id-da-meta") is None


# --------------------------------------------------------------------------
# Presença
# --------------------------------------------------------------------------


async def test_digitando_sai_em_paused_mesmo_com_erro(monkeypatch):
    monkeypatch.setattr("app.whatsapp_client.get_settings", lambda: _settings())
    estados: list[str] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        estados.append(json.loads(request.content)["State"])
        return httpx.Response(200, json={"code": 200, "success": True, "data": {}})

    _mock_http(monkeypatch, _handler)

    with pytest.raises(RuntimeError):
        async with digitando("5551994105691"):
            raise RuntimeError("modelo caiu")

    assert estados == ["composing", "paused"]


async def test_presenca_nao_derruba_o_fluxo(monkeypatch):
    """Presença é cosmética: wuzapi fora do ar não pode impedir a resposta."""
    monkeypatch.setattr("app.whatsapp_client.get_settings", lambda: _settings())

    def _handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("sem rota")

    _mock_http(monkeypatch, _handler)

    async with digitando("5551994105691"):
        pass  # não levanta


# --------------------------------------------------------------------------
# Webhook — extração
# --------------------------------------------------------------------------


def _evento(mensagem: dict, **info) -> dict:
    base_info = {
        "ID": "3EB0ABCDEF",
        "Sender": "5551994105691:12@s.whatsapp.net",
        "Chat": "5551994105691@s.whatsapp.net",
        "PushName": "Fulano",
        "IsFromMe": False,
        "IsGroup": False,
    }
    base_info.update(info)
    return {"type": "Message", "event": {"Info": base_info, "Message": mensagem}}


def test_telefone_do_jid():
    assert telefone_do_jid("5551994105691@s.whatsapp.net") == "5551994105691"
    assert telefone_do_jid("5551994105691:12@s.whatsapp.net") == "5551994105691"
    assert telefone_do_jid("120363012345678901@g.us") is None
    assert telefone_do_jid("70072425046185@lid") is None
    assert telefone_do_jid(None) is None


def test_texto_simples_e_estendido():
    (msg,) = extrair_mensagens_wuzapi(_evento({"conversation": "a impressora parou"}))
    assert msg["tipo"] == "text"
    assert msg["corpo"] == "a impressora parou"
    assert msg["telefone"] == "5551994105691"
    # wamid prefixado: `Info.ID` é único por chat, não globalmente.
    assert msg["wamid"] == "wuz:5551994105691:3EB0ABCDEF"

    (msg,) = extrair_mensagens_wuzapi(
        _evento({"extendedTextMessage": {"text": "olha lá https://x.com"}})
    )
    assert msg["tipo"] == "text"
    assert msg["corpo"].startswith("olha lá")


def test_imagem_vira_token_de_midia():
    from app.whatsapp_client import _descritor_do_token

    (msg,) = extrair_mensagens_wuzapi(
        _evento(
            {
                "imageMessage": {
                    "url": "https://mmg.whatsapp.net/d/f/abc",
                    "mimetype": "image/jpeg",
                    "mediaKey": "chave",
                    "fileSHA256": "sha",
                    "fileEncSHA256": "shaenc",
                    "fileLength": 2039,
                    "caption": "olha o erro",
                }
            }
        )
    )
    assert msg["tipo"] == "image"
    assert msg["corpo"] == "olha o erro"
    # camelCase do webhook -> PascalCase que /chat/downloadX espera.
    assert _descritor_do_token(msg["midia_id"])["MediaKey"] == "chave"


def test_documento_com_legenda_e_desembrulhado():
    """PDF com legenda chega dentro de `documentWithCaptionMessage` — é o
    anexo mais comum do RH e sumiria sem o desembrulho."""
    (msg,) = extrair_mensagens_wuzapi(
        _evento(
            {
                "documentWithCaptionMessage": {
                    "message": {
                        "documentMessage": {
                            "url": "https://mmg.whatsapp.net/d/f/xyz",
                            "mimetype": "application/pdf",
                            "mediaKey": "k",
                            "fileSHA256": "s",
                            "fileEncSHA256": "e",
                            "fileLength": 4096,
                            "fileName": "formulario.pdf",
                            "caption": "segue preenchido",
                        }
                    }
                }
            }
        )
    )
    assert msg["tipo"] == "document"
    assert msg["midia_nome"] == "formulario.pdf"
    assert msg["corpo"] == "segue preenchido"


def test_audio_entra_sem_corpo_para_o_modelo_pedir_texto():
    (msg,) = extrair_mensagens_wuzapi(_evento({"audioMessage": {"seconds": 7}}))
    assert msg["tipo"] == "audio"
    assert msg["corpo"] == ""


def test_ignora_proprias_grupos_reacoes_e_outros_eventos():
    assert extrair_mensagens_wuzapi(_evento({"conversation": "eco"}, IsFromMe=True)) == []
    assert extrair_mensagens_wuzapi(_evento({"conversation": "grupo"}, IsGroup=True)) == []
    assert extrair_mensagens_wuzapi(_evento({"reactionMessage": {"text": "👍"}})) == []
    assert extrair_mensagens_wuzapi({"type": "ReadReceipt", "event": {}}) == []
    assert extrair_mensagens_wuzapi(None) == []


def test_lid_cai_para_o_chat():
    """Com a migração do WhatsApp para identificadores `@lid`, o número real
    pode não estar em `Sender` — sem o fallback, a mensagem seria descartada."""
    (msg,) = extrair_mensagens_wuzapi(
        _evento({"conversation": "oi"}, Sender="70072425046185@lid")
    )
    assert msg["telefone"] == "5551994105691"


# --------------------------------------------------------------------------
# Webhook — assinatura
# --------------------------------------------------------------------------


def test_assinatura_hmac_hex_e_base64():
    import hashlib
    import hmac

    corpo = b'{"type":"Message"}'
    chave = "x" * 32
    esperado = hmac.new(chave.encode(), corpo, hashlib.sha256).digest()

    assert assinatura_valida(corpo, esperado.hex(), chave)
    assert assinatura_valida(corpo, "sha256=" + esperado.hex(), chave)
    assert assinatura_valida(corpo, base64.b64encode(esperado).decode(), chave)
    assert not assinatura_valida(corpo, "assinatura-errada", chave)
    assert not assinatura_valida(corpo, None, chave)
