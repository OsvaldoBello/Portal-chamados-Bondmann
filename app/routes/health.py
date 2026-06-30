"""Healthchecks (Fase 1).

- ``/health``: liveness — responde 200 sem tocar dependências (Railway).
- ``/health/ready``: readiness — verifica o pool de banco.
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app import __version__
from app.db import get_pool

router = APIRouter(tags=["health"])


@router.get("/health")
async def health() -> dict:
    return {"status": "ok", "version": __version__}


@router.get("/health/ready")
async def ready() -> JSONResponse:
    try:
        pool = get_pool()
        async with pool.acquire() as conn:
            await conn.execute("SELECT 1")
    except Exception as exc:  # noqa: BLE001 — readiness reporta degradação
        return JSONResponse({"status": "degraded", "db": str(exc)}, status_code=503)
    return JSONResponse({"status": "ready"})
