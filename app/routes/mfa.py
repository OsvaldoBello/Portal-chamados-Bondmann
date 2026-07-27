"""Rotas de MFA (TOTP) — enrollment e verificação/step-up (item 3.3, Fase 1).

Fluxo (tudo delegado ao GoTrue, ver ``app/auth/mfa.py``):

- ``GET  /mfa``                 — hub: estado do MFA (ativo/inativo) + botão de ativar.
- ``POST /mfa/enroll``          — cria o fator e exibe QR + segredo **uma única vez**.
- ``POST /mfa/enroll/confirmar``— challenge+verify do código ⇒ ativa o fator e sobe a aal2.
- ``GET/POST /mfa/verify``      — step-up de uma sessão aal1 de quem já tem MFA.

Estas rotas usam ``get_current_user`` (não o ``admin_context``), então **não**
passam pelo enforcement de aal2 — do contrário o redirect para ``/mfa/verify``
entraria em laço infinito.

Rate limit (Seção 2.4) nos endpoints que consomem código: o GoTrue já limita
tentativas do lado dele (decisão do gestor: janela/lockout = padrões do GoTrue);
aqui é só a barreira de borda contra força bruta no nosso endpoint.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Form, Request, status
from fastapi.responses import RedirectResponse
from slowapi import Limiter

from app.auth import mfa, mfa_remember
from app.auth.dependencies import AAL_MFA, CurrentUser, aal, get_current_user, mfa_habilitado
from app.auth.routes import home_for
from app.auth.session import REFRESH_COOKIE, SessionTokens, current_access_token, set_session
from app.config import get_settings
from app.security.csrf import get_csrf
from app.templating import render

log = logging.getLogger("app.routes.mfa")

router = APIRouter(prefix="/mfa", tags=["mfa"])


async def _csrf_guard(request: Request) -> None:
    await get_csrf().validate(request)


def _tokens(request: Request) -> SessionTokens | None:
    """Par de tokens da sessão corrente (para as chamadas MFA do GoTrue).

    Usa ``current_access_token`` para pegar o token recém-renovado quando o
    ``get_current_user`` acabou de fazer refresh nesta request (Seção 3.4) — o
    cookie ainda tem o antigo."""
    access = current_access_token(request)
    refresh = request.cookies.get(REFRESH_COOKIE)
    if not access or not refresh:
        return None
    return SessionTokens(access, refresh)


async def _mfa_ativo(request: Request, user: CurrentUser) -> bool:
    """Estado real do MFA. Consulta o GoTrue (fonte da verdade) e só cai no
    espelho de claim se a consulta falhar — o claim ``mfa_enabled`` do token
    corrente fica defasado logo após o enroll (o token foi emitido antes)."""
    tokens = _tokens(request)
    if tokens is not None:
        try:
            return await mfa.fator_verificado_id(tokens) is not None
        except mfa.MfaErro as exc:
            log.warning("MFA: consulta de fatores falhou, usando claim: %s", exc)
    return mfa_habilitado(user.claims)


def register_mfa_routes(app, limiter: Limiter) -> None:
    """Inclui o router aplicando rate limit nos endpoints que consomem código."""

    @router.get("")
    async def hub(request: Request, ok: str = "", user: CurrentUser = Depends(get_current_user)):
        return render(
            request,
            "mfa/setup.html",
            {
                "ativo": await _mfa_ativo(request, user),
                "verificado": aal(user.claims) == AAL_MFA,
                "eh_admin": user.role == "ADMIN",
                "ok": ok,
            },
        )

    @router.post("/enroll")
    async def enroll(
        request: Request,
        user: CurrentUser = Depends(get_current_user),
        _: None = Depends(_csrf_guard),
    ):
        """Cria o fator TOTP (não verificado) e mostra QR/segredo uma única vez."""
        tokens = _tokens(request)
        if tokens is None:
            return RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)
        try:
            dados = await mfa.iniciar_enroll(tokens)
        except mfa.MfaErro as exc:
            log.warning("MFA enroll falhou (user=%s): %s", user.id, exc)
            return render(
                request,
                "mfa/setup.html",
                {
                    "ativo": False,
                    "verificado": False,
                    "eh_admin": user.role == "ADMIN",
                    "erro": "Não foi possível iniciar a ativação do MFA. "
                            "Verifique se o TOTP está habilitado no projeto Supabase "
                            "e tente novamente.",
                },
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        return render(request, "mfa/enroll.html", {"enroll": dados})

    @router.post("/enroll/confirmar")
    @limiter.limit("10/minute")
    async def enroll_confirmar(
        request: Request,
        factor_id: str = Form(...),
        codigo: str = Form(...),
        lembrar_dispositivo: bool = Form(False),
        user: CurrentUser = Depends(get_current_user),
        _: None = Depends(_csrf_guard),
    ):
        """Verifica o 1º código: ativa o fator, sobe a sessão para aal2 e espelha
        ``app_metadata.mfa_enabled`` (leitura local do enforcement)."""
        tokens = _tokens(request)
        if tokens is None:
            return RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)
        codigo = (codigo or "").strip().replace(" ", "")
        try:
            novos = await mfa.confirmar(tokens, factor_id.strip(), codigo)
        except mfa.MfaErro as exc:
            log.info("MFA enroll/confirmar recusado (user=%s): %s", user.id, exc)
            return render(
                request,
                "mfa/enroll.html",
                {
                    # Sem o QR de novo: o fator já existe no GoTrue; o usuário só
                    # precisa reenviar um código válido do app autenticador.
                    "factor_id": factor_id,
                    "erro": "Código inválido ou expirado. Gere um novo código no "
                            "aplicativo autenticador e tente de novo.",
                },
                status_code=status.HTTP_400_BAD_REQUEST,
            )
        await mfa.marcar_mfa_habilitado(user.id, True)
        resposta = RedirectResponse("/mfa?ok=1", status_code=status.HTTP_303_SEE_OTHER)
        set_session(resposta, novos)
        if lembrar_dispositivo:
            mfa_remember.lembrar_dispositivo(resposta, get_settings(), user.id)
        return resposta

    @router.get("/verify")
    async def verify_form(request: Request, user: CurrentUser = Depends(get_current_user)):
        """Step-up: pede o código de quem já tem MFA e está numa sessão aal1."""
        if aal(user.claims) == AAL_MFA:
            return RedirectResponse(home_for(user.role), status_code=status.HTTP_303_SEE_OTHER)
        return render(request, "mfa/verify.html", {})

    @router.post("/verify")
    @limiter.limit("10/minute")
    async def verify_submit(
        request: Request,
        codigo: str = Form(...),
        lembrar_dispositivo: bool = Form(False),
        user: CurrentUser = Depends(get_current_user),
        _: None = Depends(_csrf_guard),
    ):
        tokens = _tokens(request)
        if tokens is None:
            return RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)

        def _erro(msg: str):
            return render(
                request, "mfa/verify.html", {"erro": msg},
                status_code=status.HTTP_400_BAD_REQUEST,
            )

        try:
            factor_id = await mfa.fator_verificado_id(tokens)
        except mfa.MfaErro as exc:
            log.warning("MFA verify: list_factors falhou (user=%s): %s", user.id, exc)
            return _erro("Serviço de verificação indisponível. Tente novamente.")
        if factor_id is None:
            # Não tem fator para verificar (ex.: reset pelo TI no meio do fluxo).
            return RedirectResponse("/mfa", status_code=status.HTTP_303_SEE_OTHER)

        try:
            novos = await mfa.confirmar(tokens, factor_id, (codigo or "").strip().replace(" ", ""))
        except mfa.MfaErro as exc:
            log.info("MFA verify recusado (user=%s): %s", user.id, exc)
            return _erro("Código inválido ou expirado. Tente novamente.")

        resposta = RedirectResponse(home_for(user.role), status_code=status.HTTP_303_SEE_OTHER)
        set_session(resposta, novos)
        if lembrar_dispositivo:
            mfa_remember.lembrar_dispositivo(resposta, get_settings(), user.id)
        return resposta

    app.include_router(router)
