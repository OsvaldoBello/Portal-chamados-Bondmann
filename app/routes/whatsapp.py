"""Webhook do WhatsApp Cloud API (Meta) — Etapa 2 do App Dashboard.

Escopo mínimo (fase de configuração/teste): responde ao handshake de
verificação da Meta e recebe/loga os eventos assinados, sem ainda integrar
com o sistema de chamados (isso é uma decisão de produto em separado).
"""

from __future__ import annotations

import hashlib
import hmac
import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, PlainTextResponse

from app.config import get_settings

log = logging.getLogger("app.routes.whatsapp")

router = APIRouter(tags=["whatsapp"])


@router.get("/api/webhooks/whatsapp")
async def whatsapp_verify(request: Request):
    """Handshake de assinatura do webhook (Meta faz um GET antes de ativar).

    Docs: https://developers.facebook.com/docs/graph-api/webhooks/getting-started
    """
    settings = get_settings()
    params = request.query_params
    mode = params.get("hub.mode")
    token = params.get("hub.verify_token")
    challenge = params.get("hub.challenge")

    if mode == "subscribe" and settings.whatsapp_verify_token and token == settings.whatsapp_verify_token:
        return PlainTextResponse(challenge or "", status_code=200)

    log.warning("WhatsApp webhook verification failed (mode=%s, token_match=%s)", mode, token == settings.whatsapp_verify_token)
    return JSONResponse({"error": "Verificação inválida"}, status_code=403)


def _assinatura_valida(body: bytes, header_sig: str | None, app_secret: str) -> bool:
    if not header_sig or not header_sig.startswith("sha256="):
        return False
    esperado = hmac.new(app_secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    recebido = header_sig.removeprefix("sha256=")
    return hmac.compare_digest(esperado, recebido)


def _metadados_evento(payload: dict | None) -> tuple[str, list[str]]:
    """Extrai só metadados não sensíveis (tipo de evento + ids de mensagem/status)
    para log — nunca telefone, nome de contato ou corpo da mensagem (PII)."""
    if not payload:
        return "desconhecido", []
    tipos: set[str] = set()
    ids: list[str] = []
    for entry in payload.get("entry") or []:
        for change in entry.get("changes") or []:
            if change.get("field"):
                tipos.add(change["field"])
            valor = change.get("value") or {}
            for msg in valor.get("messages") or []:
                if msg.get("id"):
                    ids.append(msg["id"])
            for status in valor.get("statuses") or []:
                if status.get("id"):
                    ids.append(status["id"])
    tipo = ",".join(sorted(tipos)) or payload.get("object") or "desconhecido"
    return tipo, ids


@router.post("/api/webhooks/whatsapp")
async def whatsapp_receive(request: Request):
    """Recebe notificações de mensagens/status do WhatsApp Cloud API.

    Valida a assinatura HMAC (X-Hub-Signature-256) quando WHATSAPP_APP_SECRET
    está configurado. Em produção, sem o secret configurado o POST é rejeitado
    (nunca processa webhook não assinável em produção) — em dev/staging sem o
    secret, segue sem validar (facilita testar o handshake antes de configurar
    o App Secret da Meta). A Meta exige 200 rápido, então nunca fazemos
    trabalho pesado aqui de forma síncrona. O log nunca grava o payload
    completo (pode conter telefone/nome/texto da mensagem — PII), só metadados.
    """
    settings = get_settings()
    body = await request.body()

    if settings.whatsapp_app_secret:
        header_sig = request.headers.get("x-hub-signature-256")
        if not _assinatura_valida(body, header_sig, settings.whatsapp_app_secret):
            log.warning("WhatsApp webhook: assinatura inválida ou ausente.")
            return JSONResponse({"error": "Assinatura inválida"}, status_code=403)
    elif settings.is_production:
        log.error("WhatsApp webhook: WHATSAPP_APP_SECRET ausente em produção — POST rejeitado.")
        return JSONResponse({"error": "Webhook não configurado"}, status_code=503)

    try:
        payload = await request.json()
    except Exception:
        payload = None

    tipo_evento, ids = _metadados_evento(payload)
    log.info("WhatsApp webhook: evento=%s ids=%s", tipo_evento, ids)

    return JSONResponse({"success": True})


def register_whatsapp_routes(app) -> None:
    app.include_router(router)
