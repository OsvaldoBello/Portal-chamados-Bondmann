"""Security headers + CSP estrita (Seção 3.8).

CSP sem ``unsafe-eval``/``unsafe-inline`` em ``script-src`` — por isso Alpine
usa o CSP build e o HTMX usa atributos declarativos. ``connect-src`` libera o
projeto Supabase (REST e Realtime wss) para o ``supabase-js`` do chat.
"""

from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.config import Settings


def build_csp(settings: Settings) -> str:
    connect = "'self'"
    if settings.supabase_url:
        connect = f"'self' {settings.supabase_url} {settings.supabase_ws_url}"
    directives = [
        "default-src 'self'",
        "script-src 'self'",
        "style-src 'self'",
        "img-src 'self' data:",
        f"connect-src {connect}",
        "font-src 'self'",
        "frame-ancestors 'none'",
        "object-src 'none'",
        "base-uri 'self'",
        "form-action 'self'",
    ]
    return "; ".join(directives)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, settings: Settings) -> None:
        super().__init__(app)
        self._csp = build_csp(settings)
        self._is_prod = settings.is_production

    async def dispatch(self, request: Request, call_next) -> Response:
        response = await call_next(request)
        headers = response.headers
        headers.setdefault("Content-Security-Policy", self._csp)
        headers.setdefault("X-Frame-Options", "DENY")
        headers.setdefault("X-Content-Type-Options", "nosniff")
        headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        headers.setdefault("Permissions-Policy", "geolocation=(), microphone=(), camera=()")
        # HSTS só faz sentido sob HTTPS (produção).
        if self._is_prod:
            headers.setdefault(
                "Strict-Transport-Security",
                "max-age=63072000; includeSubDomains; preload",
            )
        return response
