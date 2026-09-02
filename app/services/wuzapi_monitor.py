"""Monitor de sessão do wuzapi — alerta quando a conexão com o WhatsApp cai.

Task periódica de fundo (mesmo padrão de `app/ia/triagem.py::iniciar_reconciliacao`),
iniciada pelo lifespan de `app/main.py`. Roda sempre que o wuzapi estiver
configurado (`WUZAPI_BASE_URL`/`WUZAPI_TOKEN`), independente de
`WHATSAPP_PROVIDER` — durante o aquecimento do número (Fase 1 da migração,
ver `docs/pesquisa_wuzapi_migracao.md`) o provider ainda é "meta", mas
queremos saber se a sessão pareada cair MESMO ANTES de qualquer automação
depender dela.

`GET /session/status` é a única chamada feita — não temos como confirmar
entrega de mensagem (isso exigiria mandar mensagens de teste periódicas, o
que é o tipo de tráfego artificial que a pesquisa recomenda evitar num número
novo). Sessão conectada e logada é o sinal disponível mais barato de "o
número ainda está vivo".
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

import httpx

from app.config import Settings, get_settings
from app.notification import enviar_email

log = logging.getLogger("app.services.wuzapi_monitor")

_TIMEOUT_S = 8.0


@dataclass
class _StatusWuzapi:
    ok: bool
    motivo: str  # descrição curta pro e-mail de alerta — só preenchido se not ok


async def _verificar_status(settings: Settings) -> _StatusWuzapi:
    """Uma checagem de `/session/status`. Nunca lança — falha de rede é só
    mais um jeito de "não ok", tratado igual a sessão desconectada."""
    url = f"{settings.wuzapi_base_url.rstrip('/')}/session/status"
    headers = {"Token": settings.wuzapi_token}
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_S) as client:
            resp = await client.get(url, headers=headers)
    except httpx.HTTPError as exc:
        return _StatusWuzapi(False, f"erro de rede ao consultar o wuzapi ({type(exc).__name__})")

    if resp.status_code == 401:
        return _StatusWuzapi(False, "WUZAPI_TOKEN rejeitado (401) — token errado ou usuário removido")
    if resp.status_code >= 400:
        return _StatusWuzapi(False, f"wuzapi respondeu HTTP {resp.status_code}")

    try:
        dados = (resp.json() or {}).get("data") or {}
    except ValueError:
        return _StatusWuzapi(False, "resposta do wuzapi não é JSON válido")

    if not dados.get("Connected"):
        return _StatusWuzapi(False, "sessão desconectada do WhatsApp (Connected=false)")
    if not dados.get("LoggedIn"):
        return _StatusWuzapi(False, "pareamento perdido (LoggedIn=false) — precisa de novo QR Code")
    return _StatusWuzapi(True, "")


async def _alertar(settings: Settings, *, recuperou: bool, motivo: str) -> None:
    destino = settings.wuzapi_monitor_alerta_email
    if not destino:
        return
    if recuperou:
        assunto = "[wuzapi] Sessão do WhatsApp voltou ao normal"
        corpo = (
            "A sessão do wuzapi (WhatsApp) voltou a responder normalmente após um período "
            "de falha. Nenhuma ação necessária."
        )
    else:
        assunto = "[wuzapi] Sessão do WhatsApp fora do ar"
        corpo = (
            f"O monitor do wuzapi detectou falha consecutiva na sessão do WhatsApp:\n\n"
            f"{motivo}\n\n"
            "Se for perda de pareamento, é preciso escanear um QR Code novo. "
            "Enquanto a sessão estiver fora do ar, mensagens recebidas ficam represadas "
            "no lado do WhatsApp (o protocolo é offline-first) — não há mensagem perdida "
            "por conta disso, mas o atendimento fica parado até a reconexão."
        )
    try:
        await enviar_email(destino, assunto, corpo)
    except Exception as exc:  # noqa: BLE001 — alerta nunca pode derrubar o monitor
        log.warning("[WUZAPI MONITOR] Falha ao enviar e-mail de alerta: %s", type(exc).__name__)


async def _loop_monitor(settings: Settings) -> None:
    """Roda até a task ser cancelada no shutdown do app.

    Só alerta depois de `wuzapi_monitor_falhas_para_alertar` falhas SEGUIDAS
    (evita e-mail por causa de uma instabilidade de rede de um ciclo só) e só
    manda o "voltou ao normal" se já tinha alertado antes (sem isso, toda
    reconexão normal do dia a dia geraria e-mail)."""
    falhas_seguidas = 0
    ja_alertou = False
    while True:
        await asyncio.sleep(settings.wuzapi_monitor_intervalo_s)
        try:
            status = await _verificar_status(settings)
        except Exception as exc:  # noqa: BLE001 — loop de fundo nunca morre por erro de uma volta
            log.warning("[WUZAPI MONITOR] Ciclo de verificação falhou: %s", exc)
            continue

        if status.ok:
            if ja_alertou:
                log.info("[WUZAPI MONITOR] Sessão recuperada após %s falha(s).", falhas_seguidas)
                await _alertar(settings, recuperou=True, motivo="")
            falhas_seguidas = 0
            ja_alertou = False
            continue

        falhas_seguidas += 1
        log.warning(
            "[WUZAPI MONITOR] Falha %s/%s: %s",
            falhas_seguidas, settings.wuzapi_monitor_falhas_para_alertar, status.motivo,
        )
        if falhas_seguidas >= settings.wuzapi_monitor_falhas_para_alertar and not ja_alertou:
            ja_alertou = True
            await _alertar(settings, recuperou=False, motivo=status.motivo)


def iniciar_monitor(settings: Settings | None = None) -> asyncio.Task | None:
    """Inicia o loop de monitoramento (chamado pelo lifespan do app).

    ``None`` sem `WUZAPI_BASE_URL`/`WUZAPI_TOKEN` configurados (nada a
    monitorar) ou com o intervalo `<= 0` (kill switch, mesmo padrão do
    resto do projeto)."""
    settings = settings or get_settings()
    if not settings.wuzapi_base_url or not settings.wuzapi_token:
        return None
    if settings.wuzapi_monitor_intervalo_s <= 0:
        return None
    return asyncio.create_task(_loop_monitor(settings))
