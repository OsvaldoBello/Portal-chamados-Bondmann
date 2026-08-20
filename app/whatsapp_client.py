"""Cliente de envio para o WhatsApp Cloud API (Meta Graph API).

Complementa app/routes/whatsapp.py (que só recebe webhooks). Usa o token
permanente de System User (WHATSAPP_ACCESS_TOKEN) para enviar mensagens via
POST /{phone_number_id}/messages. Sem token ou phone_number_id configurados,
o envio é recusado (kill switch implícito, mesmo padrão de Mailgun/IA neste
repo: campo de config vazio = integração desligada).
"""

from __future__ import annotations

import logging

import httpx

from app.config import get_settings

log = logging.getLogger("app.whatsapp_client")


class WhatsAppNaoConfigurado(RuntimeError):
    """WHATSAPP_ACCESS_TOKEN ou WHATSAPP_PHONE_NUMBER_ID ausentes."""


class WhatsAppEnvioFalhou(RuntimeError):
    """A Graph API recusou o envio (erro 4xx/5xx)."""

    def __init__(self, status_code: int, detalhe: str):
        super().__init__(f"Envio WhatsApp falhou ({status_code}): {detalhe}")
        self.status_code = status_code
        self.detalhe = detalhe


async def enviar_mensagem_texto(destinatario: str, corpo: str) -> dict:
    """Envia uma mensagem de texto livre.

    `destinatario` deve estar em formato E.164 sem "+" (ex.: "5551994105691").
    Mensagens de texto livre só são entregues dentro da janela de 24h após a
    última mensagem do cliente; fora da janela, a Meta exige um template
    aprovado (não implementado aqui ainda).
    """
    settings = get_settings()
    if not settings.whatsapp_access_token or not settings.whatsapp_phone_number_id:
        raise WhatsAppNaoConfigurado(
            "WHATSAPP_ACCESS_TOKEN e/ou WHATSAPP_PHONE_NUMBER_ID não configurados."
        )

    url = (
        f"https://graph.facebook.com/{settings.whatsapp_api_version}"
        f"/{settings.whatsapp_phone_number_id}/messages"
    )
    payload = {
        "messaging_product": "whatsapp",
        "to": destinatario,
        "type": "text",
        "text": {"body": corpo},
    }
    headers = {"Authorization": f"Bearer {settings.whatsapp_access_token}"}

    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(url, json=payload, headers=headers)

    if resp.status_code >= 400:
        detalhe = resp.text
        log.warning("WhatsApp: envio recusado pela Graph API (status=%s)", resp.status_code)
        raise WhatsAppEnvioFalhou(resp.status_code, detalhe)

    data = resp.json()
    log.info("WhatsApp: mensagem enviada (message_id=%s)", (data.get("messages") or [{}])[0].get("id"))
    return data


async def enviar_documento(
    destinatario: str, link: str, *, nome_arquivo: str | None = None, legenda: str | None = None
) -> dict:
    """Envia um documento por URL pública — o modelo em branco de um
    formulário obrigatório (ex.: RH, `app/domain/formularios_rh.py`), por
    exemplo.

    Usa o parâmetro `link` da Graph API (a própria Meta baixa o arquivo dessa
    URL e reenvia ao destinatário) em vez do fluxo de upload prévio
    (`POST /{phone_number_id}/media`): os modelos são estáticos e já públicos
    (`app/static/formularios/...`), então não há por que subir de novo a cada
    envio. Mesma janela de 24h de `enviar_mensagem_texto`.
    """
    settings = get_settings()
    if not settings.whatsapp_access_token or not settings.whatsapp_phone_number_id:
        raise WhatsAppNaoConfigurado(
            "WHATSAPP_ACCESS_TOKEN e/ou WHATSAPP_PHONE_NUMBER_ID não configurados."
        )

    url = (
        f"https://graph.facebook.com/{settings.whatsapp_api_version}"
        f"/{settings.whatsapp_phone_number_id}/messages"
    )
    documento: dict[str, str] = {"link": link}
    if nome_arquivo:
        documento["filename"] = nome_arquivo
    if legenda:
        documento["caption"] = legenda
    payload = {
        "messaging_product": "whatsapp",
        "to": destinatario,
        "type": "document",
        "document": documento,
    }
    headers = {"Authorization": f"Bearer {settings.whatsapp_access_token}"}

    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(url, json=payload, headers=headers)

    if resp.status_code >= 400:
        detalhe = resp.text
        log.warning("WhatsApp: envio de documento recusado pela Graph API (status=%s)", resp.status_code)
        raise WhatsAppEnvioFalhou(resp.status_code, detalhe)

    data = resp.json()
    log.info(
        "WhatsApp: documento enviado (message_id=%s)",
        (data.get("messages") or [{}])[0].get("id"),
    )
    return data


async def baixar_midia(media_id: str) -> tuple[bytes, str] | None:
    """Baixa uma mídia recebida (foto ou documento do intake) — ``(conteudo, mime)`` ou ``None``.

    São dois GETs no Graph API: o primeiro resolve ``{url, mime_type}`` a
    partir do ``media_id`` do webhook, o segundo baixa o binário (a URL de
    download também exige o Bearer). Nunca lança: token ausente, erro de
    rede, resposta 4xx/5xx ou arquivo acima de
    ``WHATSAPP_INTAKE_MIDIA_MAX_BYTES`` devolvem ``None`` — a foto é contexto
    opcional do chamado, nunca motivo para derrubar o intake (mesma tolerância
    a falha da leitura de anexos da triagem).
    """
    settings = get_settings()
    if not settings.whatsapp_access_token:
        return None

    headers = {"Authorization": f"Bearer {settings.whatsapp_access_token}"}
    base = f"https://graph.facebook.com/{settings.whatsapp_api_version}"
    limite = settings.whatsapp_intake_midia_max_bytes

    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            meta = await client.get(f"{base}/{media_id}", headers=headers)
            if meta.status_code >= 400:
                log.warning("WhatsApp: metadados de mídia recusados (status=%s)", meta.status_code)
                return None
            info = meta.json()
            url = info.get("url")
            mime = str(info.get("mime_type") or "application/octet-stream")
            if not url:
                return None

            declarado = int(info.get("file_size") or 0)
            if declarado and declarado > limite:
                log.info("WhatsApp: mídia ignorada (tamanho declarado acima do teto)")
                return None

            # `stream` para abortar assim que passar do teto, em vez de
            # carregar um arquivo grande inteiro na memória da task.
            async with client.stream("GET", url, headers=headers) as resp:
                if resp.status_code >= 400:
                    log.warning("WhatsApp: download de mídia recusado (status=%s)", resp.status_code)
                    return None
                partes: list[bytes] = []
                total = 0
                async for pedaco in resp.aiter_bytes():
                    total += len(pedaco)
                    if total > limite:
                        log.info("WhatsApp: mídia ignorada (excedeu o teto durante o download)")
                        return None
                    partes.append(pedaco)
    except (httpx.HTTPError, ValueError, KeyError) as exc:
        log.warning("WhatsApp: falha ao baixar mídia (%s)", type(exc).__name__)
        return None

    return b"".join(partes), mime
