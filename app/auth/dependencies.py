"""Dependências de autenticação e autorização (Seções 3.2 / 3.4).

``CurrentUser`` carrega os claims verificados do JWT. ``require_role`` impõe a
matriz de permissões por papel. O ``empresa_id`` do perfil é a fonte do
isolamento multi-tenant e é resolvido a partir do banco sob RLS.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from fastapi import Depends, HTTPException, Request, status

from app.auth import mfa_remember
from app.auth.session import ACCESS_COOKIE, REFRESH_COOKIE, SessionTokens
from app.config import get_settings
from app.db import rls_connection
from app.security.jwt_verifier import TokenInvalido, get_verifier

log = logging.getLogger("app.auth")

ROLES = {"ADMIN", "OPERADOR", "CLIENTE"}

# Níveis de garantia de autenticação do GoTrue (claim `aal`): `aal1` = só senha;
# `aal2` = senha + fator MFA verificado nesta sessão (Seção 3.4.1).
AAL_MFA = "aal2"


class MfaChallengeRequired(Exception):
    """ADMIN com MFA habilitado, porém a sessão ainda está em ``aal1``.

    Sinaliza que falta o passo de verificação (step-up). O handler registrado em
    ``app/main.py`` traduz isso num redirect para ``/mfa/verify`` — uma
    ``HTTPException`` não serve aqui porque o que queremos é navegar o usuário
    até o fluxo, não devolver um erro.
    """


def aal(claims: dict) -> str:
    """Nível de garantia da sessão. Token sem o claim ⇒ trata como ``aal1``
    (fail-safe: a ausência nunca vale como 'MFA verificado')."""
    return str(claims.get("aal") or "aal1")


def mfa_habilitado(claims: dict) -> bool:
    """Se o usuário tem MFA (TOTP) ativo, lido do espelho ``app_metadata.mfa_enabled``.

    O espelho é gravado por ``app/auth/mfa.py`` no enroll/reset (mesmo dual-write
    do papel, item 1.5) justamente para esta leitura ser **local** — sem uma
    chamada ao GoTrue por request. Ausente ⇒ ``False`` (comportamento de
    transição: quem nunca enrolou não é bloqueado).
    """
    return bool((claims.get("app_metadata") or {}).get("mfa_enabled"))


def mfa_email_habilitado(claims: dict) -> bool:
    """Se o usuário tem MFA por e-mail ativo, lido de ``app_metadata.mfa_email_enabled``
    (mesmo espelho local de ``mfa_habilitado``, gravado por
    ``app/auth/mfa.py::marcar_email_mfa_habilitado``). Método sem fator no
    GoTrue — este claim é a ÚNICA fonte de verdade de que o método existe."""
    return bool((claims.get("app_metadata") or {}).get("mfa_email_enabled"))


def sessao_mfa_satisfeita(request: Request | None, user: CurrentUser) -> bool:
    """Se esta sessão já passou pelo segundo fator — por QUALQUER método.

    TOTP eleva a claim ``aal`` de verdade (GoTrue). E-mail não tem fator no
    GoTrue, então usa um cookie de sessão próprio
    (``app/auth/mfa_email_stepup.py``) como sinal equivalente — mesma ideia já
    aceita do cookie de "lembrar dispositivo", só que escopado a esta sessão de
    login (não sobrevive ao logout)."""
    if aal(user.claims) == AAL_MFA:
        return True
    if request is not None:
        from app.auth import mfa_email_stepup

        if mfa_email_stepup.email_verificado(request, get_settings(), user.id):
            return True
    return False


@dataclass(frozen=True)
class CurrentUser:
    id: str               # auth.uid() (claim sub)
    email: str | None
    role: str             # ADMIN | OPERADOR | CLIENTE (claim ou perfil)
    claims: dict          # claims completos, repassados ao RLS via SET LOCAL


def enforce_admin_mfa(user: CurrentUser, request: Request | None = None) -> bool:
    """Impõe ``aal2`` para o papel ADMIN (item 3.3 — Fase 1, Seção 3.4.1).

    Retorna se a UI deve exibir o **nudge** (ADMIN que ainda não habilitou MFA).
    Levanta :class:`MfaChallengeRequired` quando o ADMIN **tem** MFA habilitado
    mas a sessão está em ``aal1`` (precisa verificar) — **exceto** se este
    navegador tem o cookie de "dispositivo confiável" válido (pedido do
    usuário, 2026-07-27: lembrar o dispositivo por 30 dias e não pedir o
    código de novo nele; ver ``app/auth/mfa_remember.py``).

    ``request`` é opcional para preservar o enforcement como função **pura**
    nos testes unitários (sem app/rede) — ausente, equivale a "sem cookie".

    Fase 1 = *opcional com aviso* (decisão do gestor, 2026-07-16): ADMIN sem MFA
    continua entrando, só é avisado — tornar obrigatório é a Fase 2 (Sprint 4).
    ``OPERADOR``/``CLIENTE`` ficam **fora** do enforcement nesta fatia.
    """
    if user.role != "ADMIN":
        return False
    if sessao_mfa_satisfeita(request, user):
        return False
    if mfa_habilitado(user.claims) or mfa_email_habilitado(user.claims):
        if request is not None and mfa_remember.dispositivo_confiavel(
            request, get_settings(), user.id
        ):
            return False
        raise MfaChallengeRequired()
    return True


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
