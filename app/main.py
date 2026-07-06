"""Fábrica da aplicação FastAPI (Fases 1–2).

Monta lifespan (pool asyncpg + cliente Supabase + verificador JWT), middleware
(request-id, security headers), rate limiting (slowapi), arquivos estáticos,
tratamento de erro centralizado e os routers.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, status
from starlette.exceptions import HTTPException as StarletteHTTPException
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from slowapi.errors import RateLimitExceeded

from app.auth.routes import register_auth_routes
from app.auth.session import SessionRefreshMiddleware
from app.auth.supabase_client import init_supabase
from app.config import get_settings
from app.db import close_pool, init_pool
from app.observability import RequestContextMiddleware, configure_logging
from app.ratelimit import limiter
from app.routes.health import router as health_router
from app.routes.admin import register_admin_routes
from app.routes.common import register_common_routes
from app.routes.portal import register_portal_routes
from app.routes.workspace import register_workspace_routes
from app.security.csrf import init_csrf
from app.storage import close_storage, init_storage
from app.security.headers import SecurityHeadersMiddleware
from app.security.jwt import init_verifier

log = logging.getLogger("app")

_STATIC_DIR = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    configure_logging(settings.log_level)

    init_csrf(settings)
    init_verifier(settings)

    if settings.database_url:
        # Falha de conexão no boot (DSN placeholder, banco fora do ar, DNS) NÃO
        # deve derrubar o servidor — senão o navegador só vê ERR_CONNECTION_REFUSED.
        # Sobe em "modo limitado": as páginas que dependem do banco mostram erro
        # tratado, mas o app fica de pé (login visível, diagnóstico no log).
        try:
            await init_pool(settings)
        except Exception:  # noqa: BLE001 — resiliência de boot; ver log para a causa
            log.exception(
                "Falha ao conectar no banco (DATABASE_URL). Subindo em modo limitado — "
                "preencha o .env com credenciais reais do Supabase (Supavisor 6543)."
            )
    else:
        log.warning("DATABASE_URL ausente: pool não inicializado (modo limitado).")

    if settings.supabase_url and settings.supabase_anon_key:
        try:
            await init_supabase(settings)
            await init_storage(settings)
        except Exception:  # noqa: BLE001 — idem: não derrubar o boot por Supabase
            log.exception(
                "Falha ao inicializar Supabase (auth/storage). Subindo em modo limitado."
            )
    else:
        log.warning("Supabase não configurado: rotas de auth/anexos indisponíveis.")

    try:
        yield
    finally:
        await close_pool()
        await close_storage()


def create_app() -> FastAPI:
    settings = get_settings()

    # Init idempotente e barato aqui (não só no lifespan): garante CSRF/JWT prontos
    # mesmo em runtimes que não executam o lifespan (ex.: serverless/Vercel).
    init_csrf(settings)
    init_verifier(settings)

    app = FastAPI(
        title="Portal de Chamados Bondmann",
        docs_url="/docs" if not settings.is_production else None,
        redoc_url=None,
        lifespan=lifespan,
    )

    # Rate limiting (slowapi)
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_handler)

    # Middleware (a ordem importa: contexto por fora, headers por dentro).
    # SessionRefresh grava cookies de sessão renovada por dependency (Seção 3.4).
    app.add_middleware(SecurityHeadersMiddleware, settings=settings)
    app.add_middleware(SessionRefreshMiddleware)
    app.add_middleware(RequestContextMiddleware)
    # GZip por fora: comprime HTML/CSS/JS servidos (ganho de latência percebida).
    app.add_middleware(GZipMiddleware, minimum_size=500)

    # Estáticos
    _STATIC_DIR.mkdir(parents=True, exist_ok=True)
    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

    # Routers
    app.include_router(health_router)
    register_auth_routes(app, limiter)
    register_portal_routes(app)
    register_workspace_routes(app)
    register_admin_routes(app)
    register_common_routes(app)

    # Tratamento de erro centralizado (Seção 6.3): sem vazar stack/segredos.
    # Registra na base do Starlette para também capturar o 404 de rota inexistente
    # (o router levanta a HTTPException base, não a subclasse do FastAPI).
    app.add_exception_handler(StarletteHTTPException, _http_exception_handler)
    app.add_exception_handler(Exception, _unhandled_exception_handler)

    return app


def _rate_limit_handler(request: Request, exc: RateLimitExceeded) -> JSONResponse:
    return JSONResponse({"detail": "Muitas requisições. Tente novamente em instantes."}, 429)


# Textos das páginas de erro amigáveis (Fase 5). Fallback genérico para outros 4xx.
_ERRO_PAGINAS = {
    status.HTTP_403_FORBIDDEN: (
        "403",
        "Acesso negado",
        "Você não tem permissão para acessar esta área. Se acredita que isto é um "
        "engano, fale com a administração (TI).",
    ),
    status.HTTP_404_NOT_FOUND: (
        "404",
        "Página não encontrada",
        "O endereço que você tentou abrir não existe ou o chamado pode ter sido "
        "movido. Verifique o link e tente novamente.",
    ),
}


def _quer_html(request: Request) -> bool:
    """Navegação de página (GET que aceita HTML) e não uma chamada HTMX/API."""
    return (
        request.method == "GET"
        and "text/html" in request.headers.get("accept", "")
        and not request.headers.get("HX-Request")
    )


def _http_exception_handler(request: Request, exc: HTTPException):
    # 401 numa navegação de página (browser ou HTMX) → manda para /login em vez
    # de devolver JSON cru (Seção 3.4: "Falha → redireciona a /login").
    if exc.status_code == status.HTTP_401_UNAUTHORIZED:
        if request.headers.get("HX-Request"):
            resp = Response(status_code=204)
            resp.headers["HX-Redirect"] = "/login"
            return resp
        if request.method == "GET" and "text/html" in request.headers.get("accept", ""):
            return RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)

    # 403/404 (e demais 4xx) numa navegação → página de erro estilizada (Fase 5).
    if _quer_html(request) and 400 <= exc.status_code < 500:
        from app.templating import render

        codigo, titulo, mensagem = _ERRO_PAGINAS.get(
            exc.status_code, (str(exc.status_code), "Ops, algo deu errado", exc.detail or "")
        )
        return render(
            request,
            "erro.html",
            {"codigo": codigo, "titulo": titulo, "mensagem": mensagem, "inicio": "/"},
            status_code=exc.status_code,
        )
    return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)


def _unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    request_id = getattr(request.state, "request_id", None)
    log.exception("unhandled_exception", extra={"request_id": request_id})
    return JSONResponse(
        {"detail": "Erro interno.", "request_id": request_id}, status_code=500
    )


app = create_app()
