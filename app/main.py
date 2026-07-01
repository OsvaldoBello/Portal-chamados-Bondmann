"""Fábrica da aplicação FastAPI (Fases 1–2).

Monta lifespan (pool asyncpg + cliente Supabase + verificador JWT), middleware
(request-id, security headers), rate limiting (slowapi), arquivos estáticos,
tratamento de erro centralizado e os routers.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from app.auth.routes import register_auth_routes
from app.auth.supabase_client import init_supabase
from app.config import get_settings
from app.db import close_pool, init_pool
from app.observability import RequestContextMiddleware, configure_logging
from app.routes.health import router as health_router
from app.routes.portal import register_portal_routes
from app.security.csrf import init_csrf
from app.storage import close_storage, init_storage
from app.security.headers import SecurityHeadersMiddleware
from app.security.jwt import init_verifier

log = logging.getLogger("app")

_STATIC_DIR = Path(__file__).parent / "static"


def _client_ip(request: Request) -> str:
    """IP real atrás do proxy Railway via X-Forwarded-For (Seção 2.4)."""
    fwd = request.headers.get("X-Forwarded-For")
    if fwd:
        return fwd.split(",")[0].strip()
    return get_remote_address(request)


limiter = Limiter(key_func=_client_ip, default_limits=[])


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    configure_logging(settings.log_level)

    init_csrf(settings)
    init_verifier(settings)

    if settings.database_url:
        await init_pool(settings)
    else:
        log.warning("DATABASE_URL ausente: pool não inicializado (modo limitado).")

    if settings.supabase_url and settings.supabase_anon_key:
        await init_supabase(settings)
        await init_storage(settings)
    else:
        log.warning("Supabase não configurado: rotas de auth/anexos indisponíveis.")

    try:
        yield
    finally:
        await close_pool()
        await close_storage()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="Portal de Chamados Bondmann",
        docs_url="/docs" if not settings.is_production else None,
        redoc_url=None,
        lifespan=lifespan,
    )

    # Rate limiting (slowapi)
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_handler)

    # Middleware (a ordem importa: contexto por fora, headers por dentro)
    app.add_middleware(SecurityHeadersMiddleware, settings=settings)
    app.add_middleware(RequestContextMiddleware)

    # Estáticos
    _STATIC_DIR.mkdir(parents=True, exist_ok=True)
    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

    # Routers
    app.include_router(health_router)
    register_auth_routes(app, limiter)
    register_portal_routes(app)

    # Tratamento de erro centralizado (Seção 6.3): sem vazar stack/segredos.
    app.add_exception_handler(HTTPException, _http_exception_handler)
    app.add_exception_handler(Exception, _unhandled_exception_handler)

    return app


def _rate_limit_handler(request: Request, exc: RateLimitExceeded) -> JSONResponse:
    return JSONResponse({"detail": "Muitas requisições. Tente novamente em instantes."}, 429)


def _http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)


def _unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    request_id = getattr(request.state, "request_id", None)
    log.exception("unhandled_exception", extra={"request_id": request_id})
    return JSONResponse(
        {"detail": "Erro interno.", "request_id": request_id}, status_code=500
    )


app = create_app()
