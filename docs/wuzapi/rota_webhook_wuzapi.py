"""Webhook do WUZAPI (whatsmeow) — equivalente de `app/routes/whatsapp.py`.

PROPOSTA (docs/wuzapi/): copiar para `app/routes/wuzapi.py` e registrar em
`app/main.py` ao lado de `register_whatsapp_routes`. As duas rotas podem
coexistir durante o corte (a da Meta continua respondendo enquanto o número
antigo estiver ativo) — quem decide qual está valendo é
`WHATSAPP_PROVIDER`, que o webhook checa antes de processar.

O contrato para dentro é o mesmo: `extrair_mensagens_wuzapi()` devolve
exatamente a mesma lista de dicts que
`app.ia.whatsapp_intake.extrair_mensagens()` devolve para a Meta
(`wamid`, `telefone`, `tipo`, `corpo`, `midia_id`, `midia_nome`), então
`processar_mensagens_whatsapp()` roda sem alteração.

Duas decisões de operação que valem justificar:

1. **`-skipmedia` ligado no wuzapi.** Sem ele, o wuzapi baixa a mídia e manda
   o binário em base64 dentro do próprio webhook (`base64`, `mimeType`,
   `fileName` no nível de cima do payload). Uma foto de 5 MB vira ~6,7 MB de
   JSON por evento e, pior, teria que ser persistida na hora — o intake é
   assíncrono e só busca a foto quando o LLM decide que precisa dela. Com
   `-skipmedia`, guardamos só o descritor de criptografia (token opaco em
   `midia_id`) e baixamos sob demanda em `whatsapp_client.baixar_midia()`.
   Se um dia o base64 vier no evento, `_descritor_midia()` continua válido —
   o campo extra é simplesmente ignorado.
2. **`wamid` prefixado.** O `Info.ID` do whatsmeow é único por chat, não
   globalmente (é o remetente que o gera). Como
   `whatsapp_mensagens_recebidas.wamid` é UNIQUE global, gravamos
   `wuz:<telefone>:<Info.ID>`: mantém a idempotência que a coluna promete,
   não colide com os `wamid` da Meta já gravados e ainda diz de qual provedor
   a linha veio.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.config import get_settings

log = logging.getLogger("app.routes.wuzapi")

router = APIRouter(tags=["whatsapp"])

# Tipos de nó de mensagem que embrulham outra mensagem dentro de si.
_ENVELOPES = (
    "ephemeralMessage",
    "viewOnceMessage",
    "viewOnceMessageV2",
    "viewOnceMessageV2Extension",
    "documentWithCaptionMessage",
)


# --------------------------------------------------------------------------
# Assinatura
# --------------------------------------------------------------------------


def assinatura_valida(body: bytes, header_sig: str | None, chave: str) -> bool:
    """Confere o `x-hmac-signature` (SHA-256 sobre o corpo cru).

    Aceita hex e base64 porque a representação mudou entre versões do wuzapi;
    conferir qual a sua instância manda é um `curl` só (ver runbook). Sem
    header ou com chave errada, recusa."""
    if not header_sig:
        return False
    esperado = hmac.new(chave.encode("utf-8"), body, hashlib.sha256).digest()
    recebido = header_sig.strip()
    if recebido.lower().startswith("sha256="):
        recebido = recebido[7:]
    candidatos = [esperado.hex(), base64.b64encode(esperado).decode("ascii")]
    return any(hmac.compare_digest(c, recebido) for c in candidatos)


# --------------------------------------------------------------------------
# Extração (funções puras — testáveis sem rede e sem banco)
# --------------------------------------------------------------------------


def telefone_do_jid(jid: str | None) -> str | None:
    """`5551994105691@s.whatsapp.net` / `...:12@s.whatsapp.net` → `5551994105691`.

    Devolve `None` para JID de grupo (`@g.us`), de canal (`@newsletter`) e
    para o identificador anônimo novo do WhatsApp (`@lid`), que não é um
    telefone — nesses casos quem chama tenta o `SenderAlt`/`Chat`. A
    normalização de fato (nono dígito, DDI) segue sendo da função SQL
    `normalizar_telefone_br()`, fonte única do repo."""
    if not jid or "@" not in jid:
        return None
    numero, _, dominio = jid.partition("@")
    if dominio not in ("s.whatsapp.net", "c.us"):
        return None
    numero = numero.split(":", 1)[0]  # sufixo de device (":12")
    return numero if numero.isdigit() else None


def _desembrulhar(mensagem: dict[str, Any]) -> dict[str, Any]:
    """Abre `ephemeralMessage`/`viewOnce`/`documentWithCaption` até o nó real.

    PDF enviado com legenda chega como `documentWithCaptionMessage.message.
    documentMessage` — sem isso, o anexo mais comum do RH sumiria."""
    atual = mensagem
    for _ in range(4):  # profundidade real é 1–2; o teto evita laço infinito
        for envelope in _ENVELOPES:
            interno = (atual.get(envelope) or {}).get("message")
            if isinstance(interno, dict):
                atual = interno
                break
        else:
            return atual
    return atual


def _campo(no: dict[str, Any], *nomes: str) -> Any:
    """Primeiro campo presente entre variações de caixa (`url`/`Url`)."""
    for nome in nomes:
        if no.get(nome) not in (None, ""):
            return no[nome]
    return None


def descritor_midia(no: dict[str, Any]) -> dict[str, Any]:
    """Descritor de decriptação no formato que `POST /chat/downloadX` espera.

    O webhook serializa o protobuf em camelCase (`mediaKey`, `fileEncSHA256`)
    e os endpoints de download esperam PascalCase (`MediaKey`,
    `FileEncSHA256`) — a conversão mora aqui, num lugar só."""
    return {
        "Url": _campo(no, "url", "URL", "Url"),
        "Mimetype": _campo(no, "mimetype", "Mimetype", "mimeType"),
        "MediaKey": _campo(no, "mediaKey", "MediaKey"),
        "FileSHA256": _campo(no, "fileSHA256", "fileSha256", "FileSHA256"),
        "FileEncSHA256": _campo(no, "fileEncSHA256", "fileEncSha256", "FileEncSHA256"),
        "FileLength": int(_campo(no, "fileLength", "FileLength") or 0),
        "DirectPath": _campo(no, "directPath", "DirectPath"),
    }


def extrair_mensagens_wuzapi(payload: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Mensagens recebidas do webhook do wuzapi (função pura).

    Mesmo formato de saída de `whatsapp_intake.extrair_mensagens()`. Ignora:
    evento que não é `Message`, mensagem enviada pelo próprio número
    (`IsFromMe`), grupo, canal e status/broadcast — o intake é 1:1 com um
    perfil cadastrado. Tipos sem suporte entram com `corpo` vazio, igual à
    Meta, para o modelo pedir o relato por texto em vez de a mensagem sumir."""
    from app.whatsapp_client import token_midia

    payload = payload or {}
    if str(payload.get("type") or "") != "Message":
        return []

    evento = payload.get("event") or {}
    info = evento.get("Info") or {}
    if info.get("IsFromMe") or info.get("IsGroup"):
        return []

    telefone = telefone_do_jid(info.get("Sender")) or telefone_do_jid(info.get("SenderAlt"))
    if telefone is None:
        # `@lid`: o número real ainda pode estar no Chat (conversa 1:1).
        telefone = telefone_do_jid(info.get("Chat"))
    stanza_id = info.get("ID")
    if not telefone or not stanza_id:
        return []

    mensagem = _desembrulhar(evento.get("Message") or {})

    tipo = "desconhecido"
    corpo = ""
    midia_id = None
    midia_nome = None

    if mensagem.get("conversation"):
        tipo, corpo = "text", str(mensagem["conversation"])
    elif mensagem.get("extendedTextMessage"):
        tipo = "text"
        corpo = str((mensagem["extendedTextMessage"] or {}).get("text") or "")
    elif mensagem.get("imageMessage"):
        no = mensagem["imageMessage"] or {}
        tipo = "image"
        corpo = str(no.get("caption") or "")
        midia_id = token_midia(descritor_midia(no))
    elif mensagem.get("documentMessage"):
        no = mensagem["documentMessage"] or {}
        tipo = "document"
        corpo = str(no.get("caption") or "")
        midia_nome = no.get("fileName") or no.get("FileName")
        midia_id = token_midia(descritor_midia(no))
    elif mensagem.get("audioMessage"):
        tipo = "audio"
    elif mensagem.get("videoMessage"):
        tipo = "video"
    elif mensagem.get("stickerMessage"):
        tipo = "sticker"
    elif mensagem.get("locationMessage"):
        tipo = "location"
    elif mensagem.get("contactMessage") or mensagem.get("contactsArrayMessage"):
        tipo = "contacts"
    elif mensagem.get("reactionMessage") or mensagem.get("protocolMessage"):
        # Reação e recibo de protocolo (editar/apagar) não são conteúdo de
        # chamado — descarta antes de gerar linha no banco.
        return []

    return [
        {
            "wamid": f"wuz:{telefone}:{stanza_id}",
            "telefone": telefone,
            "tipo": tipo,
            "corpo": corpo,
            "midia_id": midia_id,
            "midia_nome": midia_nome,
        }
    ]


# --------------------------------------------------------------------------
# Rota
# --------------------------------------------------------------------------


async def _corpo_json(request: Request, body: bytes) -> dict[str, Any] | None:
    """Aceita os dois formatos de entrega do wuzapi.

    `WEBHOOK_FORMAT=json` manda o evento cru (recomendado);
    `form` manda `application/x-www-form-urlencoded` com o campo `jsonData`."""
    tipo = request.headers.get("content-type", "")
    try:
        if tipo.startswith("application/json"):
            return json.loads(body)
        formulario = await request.form()
        bruto = formulario.get("jsonData")
        return json.loads(bruto) if isinstance(bruto, str) else None
    except (ValueError, json.JSONDecodeError):
        return None


@router.post("/api/webhooks/wuzapi")
async def wuzapi_receive(request: Request):
    """Recebe eventos do wuzapi.

    Mesma disciplina da rota da Meta: valida HMAC quando há chave; em
    produção sem chave, recusa (503); responde 200 rápido e joga o trabalho
    pesado para a task do intake; nunca loga corpo/telefone (PII)."""
    settings = get_settings()
    body = await request.body()

    if settings.wuzapi_webhook_hmac_key:
        if not assinatura_valida(body, request.headers.get("x-hmac-signature"), settings.wuzapi_webhook_hmac_key):
            log.warning("wuzapi webhook: assinatura inválida ou ausente.")
            return JSONResponse({"error": "Assinatura inválida"}, status_code=403)
    elif settings.is_production:
        log.error("wuzapi webhook: WUZAPI_WEBHOOK_HMAC_KEY ausente em produção — POST rejeitado.")
        return JSONResponse({"error": "Webhook não configurado"}, status_code=503)

    payload = await _corpo_json(request, body)
    tipo_evento = str((payload or {}).get("type") or "desconhecido")

    if (settings.whatsapp_provider or "meta").lower() != "wuzapi":
        # Provedor desligado: aceita e descarta (evita retry infinito do
        # wuzapi durante um rollback para a Meta).
        log.info("wuzapi webhook: evento=%s descartado (provedor ativo != wuzapi)", tipo_evento)
        return JSONResponse({"success": True})

    mensagens = extrair_mensagens_wuzapi(payload)
    log.info("wuzapi webhook: evento=%s mensagens=%d", tipo_evento, len(mensagens))

    if mensagens and settings.whatsapp_intake_ativo:
        from app.ia import whatsapp_intake

        # `processar_mensagens_whatsapp` espera o formato da Meta; aqui já
        # temos a lista pronta, então chamamos o recebimento por mensagem.
        for msg in mensagens:
            await whatsapp_intake.receber_mensagem_normalizada(msg)

    return JSONResponse({"success": True})


def register_wuzapi_routes(app) -> None:
    app.include_router(router)
