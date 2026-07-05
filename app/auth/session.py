"""Cookies de sessão (Seção 3.4).

- Access token JWT (curto) em cookie ``httpOnly + Secure + SameSite=Lax``.
- Refresh token em cookie separado ``httpOnly + Secure + SameSite=Strict``.
Nunca em localStorage nem header Bearer no browser.
"""

from __future__ import annotations

from dataclasses import dataclass

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

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


class SessionRefreshMiddleware(BaseHTTPMiddleware):
    """Persiste nos cookies uma sessão renovada por dependency (Seção 3.4).

    Quando ``get_current_user`` renova o access token via refresh, guarda os
    novos tokens em ``request.state.refreshed_session``. Como as rotas retornam
    suas próprias ``Response`` (ex.: ``TemplateResponse``), os cookies precisam
    ser gravados aqui, na resposta que efetivamente sai.
    """

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        tokens = getattr(request.state, "refreshed_session", None)
        if tokens is not None:
            set_session(response, tokens)
        return response
