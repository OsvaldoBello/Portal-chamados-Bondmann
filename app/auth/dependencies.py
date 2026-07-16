"""Dependências de autenticação e autorização (Seções 3.2 / 3.4).

``CurrentUser`` carrega os claims verificados do JWT. ``require_role`` impõe a
matriz de permissões por papel. O ``empresa_id`` do perfil é a fonte do
isolamento multi-tenant e é resolvido a partir do banco sob RLS.
"""

from __future__ import annotations

from dataclasses import dataclass

import logging

from fastapi import Depends, HTTPException, Request, status

from app.auth.session import ACCESS_COOKIE, REFRESH_COOKIE, SessionTokens, set_session
from app.db import rls_connection
from app.security.jwt_verifier import TokenInvalido, get_verifier

log = logging.getLogger("app.auth")

ROLES = {"ADMIN", "OPERADOR", "CLIENTE"}


@dataclass(frozen=True)
class CurrentUser:
    id: str               # auth.uid() (claim sub)
    email: str | None
    role: str             # ADMIN | OPERADOR | CLIENTE (claim ou perfil)
    claims: dict          # claims completos, repassados ao RLS via SET LOCAL


def _extract_token(request: Request) -> str | None:
    return request.cookies.get(ACCESS_COOKIE)


async def get_current_user(request: Request) -> CurrentUser:
    """Exige usuário autenticado; 401 se ausente/expirado/inválido.

    Se o access token estiver expirado/inválido mas houver refresh token válido,
    **renova a sessão** via GoTrue (Seção 3.4), evitando forçar re-login a cada
    expiração do access token (1h). Os novos tokens são guardados em
    ``request.state.refreshed_session`` e gravados como cookies pelo
    ``SessionRefreshMiddleware`` na resposta que sai.
    """
    token = _extract_token(request)
    claims: dict | None = None
    if token:
        try:
            claims = get_verifier().verify(token)
        except TokenInvalido:
            claims = None

    if claims is None:
        claims = await _try_refresh(request)

    if claims is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Não autenticado.")

    role = _resolve_role(claims)
    return CurrentUser(
        id=claims["sub"],
        email=claims.get("email"),
        role=role,
        claims=claims,
    )


async def _try_refresh(request: Request) -> dict | None:
    """Tenta renovar a sessão pelo refresh token.

    Retorna os claims do novo access token, ou ``None`` se não houver refresh
    token ou a renovação falhar. Guarda os novos tokens em
    ``request.state.refreshed_session`` para o middleware persistir nos cookies.
    Import tardio de ``ensure_supabase`` para não acoplar à init do cliente.
    """
    refresh = request.cookies.get(REFRESH_COOKIE)
    if not refresh:
        return None
    try:
        from app.auth.supabase_client import ensure_supabase

        supabase = await ensure_supabase()
        result = await supabase.auth.refresh_session(refresh)
        session = getattr(result, "session", None)
        if session is None:
            return None
        claims = get_verifier().verify(session.access_token)
        request.state.refreshed_session = SessionTokens(
            session.access_token, session.refresh_token
        )
        return claims
    except Exception as exc:  # noqa: BLE001 — refresh falhou → tratado como não autenticado
        log.info("refresh_session falhou: %s", type(exc).__name__)
        return None


async def get_optional_user(request: Request) -> CurrentUser | None:
    """Versão não-bloqueante (para páginas públicas que variam se logado)."""
    try:
        return await get_current_user(request)
    except HTTPException:
        return None


def _resolve_role(claims: dict) -> str:
    """Papel do usuário a partir dos claims (app_metadata.role) com default CLIENTE."""
    app_meta = claims.get("app_metadata") or {}
    role = (app_meta.get("role") or claims.get("role") or "").upper()
    return role if role in ROLES else "CLIENTE"


def require_role(*allowed: str):
    """Factory de dependência que exige um dos papéis informados."""
    allowed_set = {r.upper() for r in allowed}

    async def _checker(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
        if user.role not in allowed_set:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="Acesso negado."
            )
        return user

    return _checker


async def load_empresa_id(user: CurrentUser) -> str | None:
    """Resolve o ``empresa_id`` do perfil do usuário sob RLS."""
    async with rls_connection(user.claims) as conn:
        return await conn.fetchval("SELECT empresa_id FROM perfis WHERE id = $1::uuid", user.id)
