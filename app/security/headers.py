"""Security headers + CSP estrita (Seção 3.8).

CSP sem ``unsafe-eval``/``unsafe-inline`` em ``script-src`` — por isso Alpine
usa o CSP build e o HTMX usa atributos declarativos. ``connect-src`` libera o
projeto Supabase (REST e Realtime wss) para o ``supabase-js`` do chat.
"""

from __future__ import annotations

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.config import Settings


def build_csp(settings: Settings) -> str:
    connect = "'self'"
    # Avatares vêm do bucket público do Storage via <img src> direto (Fase 7) —
    # diferente dos anexos (link/signed URL, não embutidos como imagem), este é
    # o 1º caso de carregar imagem de fora de 'self'; sem o host aqui o
    # navegador bloqueia o <img> por CSP (broken image, sem erro visível pro
    # usuário além do ícone quebrado).
    img = "'self' data:"
    if settings.supabase_url:
        connect = f"'self' {settings.supabase_url} {settings.supabase_ws_url}"
        img = f"'self' data: {settings.supabase_url}"
    directives = [
        "default-src 'self'",
        "script-src 'self'",
        "style-src 'self'",
        f"img-src {img}",
        f"connect-src {connect}",
        "font-src 'self'",
        "frame-ancestors 'none'",
        "object-src 'none'",
        "base-uri 'self'",
        "form-action 'self'",
    ]
    return "; ".join(directives)


class SecurityHeadersMiddleware:
    """Middleware ASGI puro (Seção 2.3/M5 do plano de melhorias — sem
    ``BaseHTTPMiddleware``, que serializa a resposta inteira em memória antes
    de repassar; aqui só interceptamos o evento ``http.response.start``)."""

    def __init__(self, app: ASGIApp, settings: Settings) -> None:
        self.app = app
        self._csp = build_csp(settings)
        self._is_prod = settings.is_production

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers.setdefault("Content-Security-Policy", self._csp)
                headers.setdefault("X-Frame-Options", "DENY")
                headers.setdefault("X-Content-Type-Options", "nosniff")
                headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
                headers.setdefault(
                    "Permissions-Policy", "geolocation=(), microphone=(), camera=()"
                )
                # HSTS só faz sentido sob HTTPS (produção).
                if self._is_prod:
                    headers.setdefault(
                        "Strict-Transport-Security",
                        "max-age=63072000; includeSubDomains; preload",
                    )
            await send(message)

        await self.app(scope, receive, send_wrapper)
