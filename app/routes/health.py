"""Healthchecks e diagnóstico (Fase 1 + deploy).

- ``/health``: liveness — 200 sem tocar dependências, sempre público.
- ``/health/ready``: readiness — tenta conectar no banco (via _ensure_pool).
  Fora de produção reporta o erro real (diagnóstico de deploy); em produção a
  resposta é genérica (o erro completo só vai pro log) e a rota exige o token
  de diagnóstico (Sprint 1 / item 1.2, M3).
- ``/health/config``: diagnóstico **sem segredos** — apenas booleanos de quais
  variáveis de ambiente chegaram. Mesma exigência de token em produção.
- ``/metrics``: métricas mínimas em memória do processo (Sprint 2 / item 2.6)
  — contagem de status/5xx, p95 de latência por rota, taxa de 304 do polling
  e saturação do pool asyncpg. Mesma exigência de token em produção.
"""

from __future__ import annotations

import hmac
import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app import __version__, metrics
from app.config import get_settings
from app.db import _ensure_pool, pool_stats

log = logging.getLogger("app.routes.health")

router = APIRouter(tags=["health"])


@router.get("/health")
async def health() -> dict:
    return {"status": "ok", "version": __version__}


def _diagnostico_autorizado(request: Request) -> bool:
    """Fora de produção, sempre libera (diagnóstico local/staging sem fricção).
    Em produção, exige o header ``X-Diagnostics-Token`` batendo com
    ``DIAGNOSTICS_TOKEN`` — sem o token configurado, a rota fica negada por
    padrão (fail-closed), nunca aberta por omissão."""
    settings = get_settings()
    if not settings.is_production:
        return True
    if not settings.diagnostics_token:
        return False
    enviado = request.headers.get("X-Diagnostics-Token", "")
    return hmac.compare_digest(enviado, settings.diagnostics_token)


@router.get("/health/ready")
async def ready(request: Request) -> JSONResponse:
    """Tenta abrir uma conexão real e rodar SELECT 1."""
    settings = get_settings()
    if not _diagnostico_autorizado(request):
        return JSONResponse({"error": "Não autorizado"}, status_code=403)
    try:
        pool = await _ensure_pool()
        async with pool.acquire() as conn:
            await conn.execute("SELECT 1")
    except Exception as exc:  # noqa: BLE001 — readiness expõe a causa (fora de produção) p/ diagnóstico
        log.error("Readiness check falhou: %s: %s", type(exc).__name__, exc)
        corpo = {"status": "degraded"}
        if not settings.is_production:
            corpo["db_error"] = f"{type(exc).__name__}: {exc}"
        return JSONResponse(corpo, status_code=503)
    return JSONResponse({"status": "ready"})


@router.get("/metrics")
async def metrics_endpoint(request: Request) -> JSONResponse:
    """Snapshot das métricas do processo — critério de go-live "48h sem 5xx"
    verificável aqui, sem grep manual de log (Sprint 2 / item 2.6)."""
    if not _diagnostico_autorizado(request):
        return JSONResponse({"error": "Não autorizado"}, status_code=403)
    corpo = metrics.snapshot()
    corpo["db_pool"] = pool_stats()
    corpo["sentry_enabled"] = bool(get_settings().sentry_dsn)
    return JSONResponse(corpo)


@router.get("/health/config")
async def config(request: Request) -> JSONResponse:
    """Booleanos de presença de config — NUNCA expõe valores/segredos."""
    if not _diagnostico_autorizado(request):
        return JSONResponse({"error": "Não autorizado"}, status_code=403)
    s = get_settings()
    return JSONResponse(
        {
            "environment": s.environment,
            "supabase_url_set": bool(s.supabase_url),
            "supabase_anon_key_set": bool(s.supabase_anon_key),
            "supabase_service_role_set": bool(s.supabase_service_role_key),
            "supabase_jwt_secret_set": bool(s.supabase_jwt_secret),
            "database_url_set": bool(s.database_url),
            "session_secret_custom": s.session_secret != "dev-insecure-session-secret-change-me",
            "csrf_secret_custom": s.csrf_secret != "dev-insecure-csrf-secret-change-me",
            "cookie_secure": s.cookie_secure,
        }
    )
