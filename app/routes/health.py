"""Healthchecks e diagnóstico (Fase 1 + deploy).

- ``/health``: liveness — 200 sem tocar dependências.
- ``/health/ready``: readiness — tenta conectar no banco (via _ensure_pool) e
  reporta o erro real se falhar (diagnóstico de deploy).
- ``/health/config``: diagnóstico **sem segredos** — apenas booleanos de quais
  variáveis de ambiente chegaram (útil p/ conferir env vars na Vercel/Railway).
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app import __version__
from app.config import get_settings
from app.db import _ensure_pool

router = APIRouter(tags=["health"])


@router.get("/health")
async def health() -> dict:
    return {"status": "ok", "version": __version__}


@router.get("/health/ready")
async def ready() -> JSONResponse:
    """Tenta abrir uma conexão real e rodar SELECT 1. Reporta o erro exato."""
    try:
        pool = await _ensure_pool()
        async with pool.acquire() as conn:
            await conn.execute("SELECT 1")
    except Exception as exc:  # noqa: BLE001 — readiness expõe a causa p/ diagnóstico
        return JSONResponse(
            {"status": "degraded", "db_error": f"{type(exc).__name__}: {exc}"},
            status_code=503,
        )
    return JSONResponse({"status": "ready"})


@router.get("/health/config")
async def config() -> dict:
    """Booleanos de presença de config — NUNCA expõe valores/segredos."""
    s = get_settings()
    return {
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
