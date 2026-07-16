"""Cookies de sessão (Seção 3.4).

- Access token JWT (curto) em cookie ``httpOnly + Secure + SameSite=Lax``.
- Refresh token em cookie separado ``httpOnly + Secure + SameSite=Strict``.
Nunca em localStorage nem header Bearer no browser.
"""

from __future__ import annotations

from dataclasses import dataclass

from starlette.datastructures import MutableHeaders
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.config import get_settings

ACCESS_COOKIE = "sb_access"
REFRESH_COOKIE = "sb_refresh"

# O access token expira pela própria validade do JWT; o cookie acompanha.
_ACCESS_MAX_AGE = 60 * 60          # 1h
_REFRESH_MAX_AGE = 60 * 60 * 24 * 30  # 30 dias


@dataclass(frozen=True)
class SessionTokens:
    access_token: str
    refresh_token: str


def set_session(response: Response, tokens: SessionTokens) -> None:
    secure = get_settings().cookie_secure
    response.set_cookie(
        ACCESS_COOKIE,
        tokens.access_token,
        max_age=_ACCESS_MAX_AGE,
        httponly=True,
        secure=secure,
        samesite="lax",
        path="/",
    )
    response.set_cookie(
        REFRESH_COOKIE,
        tokens.refresh_token,
        max_age=_REFRESH_MAX_AGE,
        httponly=True,
        secure=secure,
        samesite="strict",
        path="/",
    )


def clear_session(response: Response) -> None:
    response.delete_cookie(ACCESS_COOKIE, path="/")
    response.delete_cookie(REFRESH_COOKIE, path="/")


def current_access_token(request: Request) -> str | None:
    """JWT válido para chamadas ao Supabase (Storage/Realtime) nesta requisição.

    Quando ``get_current_user`` renova a sessão (access token expirado + refresh
    válido, Seção 3.4), o token novo só chega ao cookie no ``Set-Cookie`` da
    resposta (``SessionRefreshMiddleware``) — ``request.cookies`` ainda reflete o
    token velho/expirado durante toda a requisição corrente. Preferir o token
    recém-renovado (``request.state.refreshed_session``) evita mandar um JWT
    expirado ao Storage (ex.: upload de avatar/anexo logo após o refresh)."""
    refreshed: SessionTokens | None = getattr(request.state, "refreshed_session", None)
    if refreshed is not None:
        return refreshed.access_token
    return request.cookies.get(ACCESS_COOKIE)


class SessionRefreshMiddleware:
    """Persiste nos cookies uma sessão renovada por dependency (Seção 3.4).

    Quando ``get_current_user`` renova o access token via refresh, guarda os
    novos tokens em ``request.state.refreshed_session``. Como as rotas retornam
    suas próprias ``Response`` (ex.: ``TemplateResponse``), os cookies precisam
    ser gravados aqui, na resposta que efetivamente sai.

    Middleware ASGI puro (Seção 2.3/M5 do plano de melhorias) — sem
    ``BaseHTTPMiddleware``. A dependency roda (e popula
    ``scope["state"]["refreshed_session"]``) antes da rota emitir
    ``http.response.start``, então o valor já está disponível quando
    interceptamos esse evento para acrescentar os ``Set-Cookie``.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                tokens = scope.get("state", {}).get("refreshed_session")
                if tokens is not None:
                    cookie_carrier = Response()
                    set_session(cookie_carrier, tokens)
                    headers = MutableHeaders(scope=message)
                    for key, value in cookie_carrier.raw_headers:
                        if key == b"set-cookie":
                            headers.append("set-cookie", value.decode("latin-1"))
            await send(message)

        await self.app(scope, receive, send_wrapper)
