"""API do worker da automação de acessos — ``/api/automacao/*`` (F2, 2026-09-14).

Server-to-server, sem sessão de usuário: o worker (Railway + Tailscale, D1 do
`plano_md_mestre_automacao_acessos.md`) faz PULL da fila. Contrato completo em
`docs/automacao_api.md`.

Autenticação: header ``X-Automacao-Token`` comparado em tempo constante com
``AUTOMACAO_WORKER_TOKEN`` (fail-closed: sem env, tudo responde 404 — a rota
nem "existe"; mesmo padrão de `health._diagnostico_autorizado`). Sem cookie,
sem CSRF (não há browser), mesmo tratamento do inbound de e-mail. Escritas
pela conexão administrativa (`repositories/automacao.admin_*`).
"""

from __future__ import annotations

import hmac
import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import JSONResponse, Response
from starlette.background import BackgroundTask

from app.config import get_settings
from app.domain import automacao as dom
from app.ratelimit import limiter
from app.repositories import automacao as repo_admin
from app.services import automacao as svc

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/automacao", tags=["automacao"])


def _autorizar(request: Request) -> str:
    """Valida o token e devolve o ``worker_id`` informado no header
    ``X-Automacao-Worker`` (ou o IP, se ausente)."""
    settings = get_settings()
    if not svc.api_habilitada(settings):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    enviado = request.headers.get("X-Automacao-Token", "")
    if not enviado or not hmac.compare_digest(enviado, settings.automacao_worker_token):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="token inválido")
    worker_id = (request.headers.get("X-Automacao-Worker") or "").strip()[:80]
    if not worker_id:
        worker_id = (request.client.host if request.client else "worker")[:80]
    svc.registrar_contato_worker(worker_id)
    return worker_id


async def _json(request: Request) -> dict[str, Any]:
    try:
        corpo = await request.json()
    except Exception:  # noqa: BLE001 — corpo ausente/inválido
        return {}
    return corpo if isinstance(corpo, dict) else {}


def _job_publico(job: dict[str, Any]) -> dict[str, Any]:
    """Projeção do job para o worker — só o que ele precisa."""
    return {
        "id": str(job["id"]),
        "tipo": job["tipo"],
        "dry_run": bool(job.get("dry_run")),
        "tentativa": int(job.get("tentativas") or 0),
        "chamado": {"id": str(job["chamado_id"]), "codigo": job.get("chamado_codigo") or ""},
        "payload": job["payload"],
    }


@router.get("/saude")
async def saude(request: Request) -> JSONResponse:
    """Handshake do worker no boot: valida o token, confere a versão do
    contrato e devolve o estado da fila."""
    worker_id = _autorizar(request)
    settings = get_settings()
    fila = await repo_admin.admin_contagem_fila()
    return JSONResponse(
        {
            "ok": True,
            "versao_contrato": settings.automacao_contrato_versao,
            "ativa": settings.automacao_ativa,
            "tipos": sorted(svc.tipos_liberados(settings)),
            "fila": fila,
            "worker_id": worker_id,
        }
    )


@router.post("/jobs/proximo")
@limiter.limit("120/minute")
async def proximo(request: Request) -> Response:
    """Claim atômico de um job vencido da fila. 204 = nada para fazer (inclui
    kill switch desligado — o worker só volta a perguntar depois)."""
    worker_id = _autorizar(request)
    settings = get_settings()
    if not settings.automacao_ativa:
        return Response(status_code=status.HTTP_204_NO_CONTENT, headers={"X-Automacao-Pausada": "1"})
    corpo = await _json(request)
    pedidos = corpo.get("tipos") if isinstance(corpo.get("tipos"), list) else list(dom.TIPOS)
    tipos = sorted({str(t).upper() for t in pedidos} & svc.tipos_liberados(settings))
    job = await repo_admin.admin_claim_proximo(worker_id, tipos)
    if job is None:
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    log.info("[AUTOMACAO] job %s (%s, %s) entregue a %s", job["id"], job["tipo"], job.get("chamado_codigo"), worker_id)
    return JSONResponse(_job_publico(job))


@router.post("/jobs/{job_id}/heartbeat")
@limiter.limit("600/minute")
async def heartbeat(request: Request, job_id: str) -> JSONResponse:
    """Renova o heartbeat (a cada etapa). 409 = o job não é mais deste worker
    (foi dado como morto pela vigilância, ou já terminou): o worker deve
    ABORTAR o que estiver fazendo e não enviar resultado."""
    worker_id = _autorizar(request)
    corpo = await _json(request)
    ok = await repo_admin.admin_heartbeat(job_id, worker_id, str(corpo.get("etapa") or "") or None)
    if not ok:
        return JSONResponse({"ok": False, "motivo": "job não pertence a este worker"}, status_code=409)
    return JSONResponse({"ok": True})


@router.post("/jobs/{job_id}/resultado")
@limiter.limit("120/minute")
async def resultado(request: Request, job_id: str) -> JSONResponse:
    """Transição final. Corpo: ``{"etapas": [StepResult...], "erro": str|null,
    "credenciais": {...}, "licenca": {...}}``. Persiste (mascarando segredos) e
    agenda os efeitos (mensagens, status, licença, e-mails) em background —
    o worker recebe 200 assim que o job está gravado. Idempotente: segundo
    POST para o mesmo job devolve 409."""
    worker_id = _autorizar(request)
    corpo = await _json(request)
    etapas = dom.normalizar_etapas(corpo.get("etapas"))
    erro = str(corpo.get("erro") or "").strip() or None
    status_final = dom.classificar(etapas, erro_geral=erro)
    job = await repo_admin.admin_finalizar(
        job_id, worker_id, status=status_final, resultado=etapas, erro=erro
    )
    if job is None:
        return JSONResponse(
            {"ok": False, "motivo": "job não está EXECUTANDO com este worker"}, status_code=409
        )
    log.info("[AUTOMACAO] job %s finalizado: %s (%d etapas)", job_id, status_final, len(etapas))
    tarefa = BackgroundTask(
        svc.processar_resultado, job, etapas, corpo.get("credenciais"), corpo.get("licenca")
    )
    return JSONResponse({"ok": True, "status": status_final}, background=tarefa)


def register_automacao_api_routes(app) -> None:
    app.include_router(router)
