"""Rotas de MFA — TOTP (app autenticador) e código por e-mail (item 3.3, Fase 1
+ extensão "MFA por e-mail").

Fluxo TOTP (delegado ao GoTrue, ver ``app/auth/mfa.py``):

- ``GET  /mfa``                 — hub: estado dos dois métodos + botões de ativar.
- ``POST /mfa/enroll``          — cria o fator e exibe QR + segredo **uma única vez**.
- ``POST /mfa/enroll/confirmar``— challenge+verify do código ⇒ ativa o fator e sobe a aal2.
- ``GET/POST /mfa/verify``      — step-up de uma sessão aal1 de quem já tem MFA.

Fluxo e-mail (todo local, ver ``app/auth/mfa_email.py`` — GoTrue não tem fator
"email"; a "sessão verificada" é um cookie próprio, não a claim ``aal`` real):

- ``POST /mfa/enroll-email``           — gera e envia o 1º código.
- ``POST /mfa/enroll-email/confirmar`` — confere o código ⇒ ativa o método.
- ``POST /mfa/verify/email/enviar``    — envia o código de verificação (step-up).
- ``POST /mfa/verify/email``           — confere o código ⇒ marca a sessão como verificada.

Estas rotas usam ``get_current_user`` (não o ``admin_context``), então **não**
passam pelo enforcement de aal2 — do contrário o redirect para ``/mfa/verify``
entraria em laço infinito.

Rate limit (Seção 2.4) nos endpoints que consomem/enviam código: o GoTrue já
limita tentativas do TOTP do lado dele; para e-mail não há essa rede de
segurança externa, então o limiter + o cooldown/tentativas de
``app/auth/mfa_email.py`` são a única barreira.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Form, Request, status
from fastapi.responses import RedirectResponse
from slowapi import Limiter

from app.auth import mfa, mfa_email, mfa_email_stepup, mfa_remember
from app.auth.dependencies import (
    CurrentUser,
    get_current_user,
    mfa_email_habilitado,
    mfa_habilitado,
    sessao_mfa_satisfeita,
)
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


async def _metodos_mfa(request: Request, user: CurrentUser) -> dict:
    """Estado real dos dois métodos.

    TOTP consulta o GoTrue (fonte da verdade) e só cai no espelho de claim se
    a consulta falhar — o claim ``mfa_enabled`` do token corrente fica
    defasado logo após o enroll (o token foi emitido antes). E-mail não tem
    fator no GoTrue (``app/auth/mfa_email.py``): o espelho de claim JÁ é a
    única fonte de verdade, sem chamada de rede extra."""
    tokens = _tokens(request)
    totp = mfa_habilitado(user.claims)
    if tokens is not None:
        try:
            totp = await mfa.fator_verificado_id(tokens) is not None
        except mfa.MfaErro as exc:
            log.warning("MFA: consulta de fatores TOTP falhou, usando claim: %s", exc)
    return {"totp": totp, "email": mfa_email_habilitado(user.claims)}


def register_mfa_routes(app, limiter: Limiter) -> None:
    """Inclui o router aplicando rate limit nos endpoints que consomem/enviam código."""

    @router.get("")
    async def hub(request: Request, ok: str = "", user: CurrentUser = Depends(get_current_user)):
        metodos = await _metodos_mfa(request, user)
        return render(
            request,
            "mfa/setup.html",
            {
                "totp_ativo": metodos["totp"],
                "email_ativo": metodos["email"],
                "verificado": sessao_mfa_satisfeita(request, user),
                "eh_admin": user.role == "ADMIN",
                "ok": ok,
            },
        )

    # ------------------------------------------------------------------
    # TOTP (app autenticador)
    # ------------------------------------------------------------------

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
                    "totp_ativo": False,
                    "email_ativo": mfa_email_habilitado(user.claims),
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

    # ------------------------------------------------------------------
    # E-mail (código de 6 dígitos) — enrollment
    # ------------------------------------------------------------------

    @router.post("/enroll-email")
    @limiter.limit("5/minute")
    async def enroll_email(
        request: Request,
        user: CurrentUser = Depends(get_current_user),
        _: None = Depends(_csrf_guard),
    ):
        """Gera e envia o 1º código de verificação por e-mail."""
        try:
            await mfa_email.enviar_codigo(user.id, user.email or "")
        except mfa_email.MfaErro as exc:
            log.warning("MFA enroll-email falhou (user=%s): %s", user.id, exc)
            metodos = await _metodos_mfa(request, user)
            return render(
                request,
                "mfa/setup.html",
                {
                    "totp_ativo": metodos["totp"],
                    "email_ativo": metodos["email"],
                    "verificado": sessao_mfa_satisfeita(request, user),
                    "eh_admin": user.role == "ADMIN",
                    "erro": "Não foi possível enviar o código por e-mail. "
                            "Aguarde um instante e tente novamente.",
                },
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            )
        return render(request, "mfa/enroll_email.html", {"email": user.email})

    @router.post("/enroll-email/confirmar")
    @limiter.limit("10/minute")
    async def enroll_email_confirmar(
        request: Request,
        codigo: str = Form(...),
        lembrar_dispositivo: bool = Form(False),
        user: CurrentUser = Depends(get_current_user),
        _: None = Depends(_csrf_guard),
    ):
        codigo = (codigo or "").strip().replace(" ", "")
        try:
            await mfa_email.verificar_codigo(user.id, codigo)
        except mfa_email.MfaErro as exc:
            log.info("MFA enroll-email/confirmar recusado (user=%s): %s", user.id, exc)
            return render(
                request,
                "mfa/enroll_email.html",
                {
                    "email": user.email,
                    "erro": "Código inválido ou expirado. Peça um novo código e tente de novo.",
                },
                status_code=status.HTTP_400_BAD_REQUEST,
            )
        await mfa.marcar_email_mfa_habilitado(user.id, True)
        resposta = RedirectResponse("/mfa?ok=1", status_code=status.HTTP_303_SEE_OTHER)
        # Sem fator GoTrue para elevar a aal2 de verdade — o cookie de step-up
        # marca esta sessão como verificada (mesma ideia da 1ª confirmação do TOTP).
        mfa_email_stepup.marcar_email_verificado(resposta, get_settings(), user.id)
        if lembrar_dispositivo:
            mfa_remember.lembrar_dispositivo(resposta, get_settings(), user.id)
        return resposta

    # ------------------------------------------------------------------
    # Verificação (step-up de sessão não verificada) — TOTP ou e-mail
    # ------------------------------------------------------------------

    @router.get("/verify")
    async def verify_form(request: Request, user: CurrentUser = Depends(get_current_user)):
        """Step-up: pede o código de quem já tem algum método ativo."""
        if sessao_mfa_satisfeita(request, user):
            return RedirectResponse(home_for(user.role), status_code=status.HTTP_303_SEE_OTHER)
        metodos = await _metodos_mfa(request, user)
        return render(
            request,
            "mfa/verify.html",
            {"totp_ativo": metodos["totp"], "email_ativo": metodos["email"]},
        )

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

        async def _erro(msg: str):
            metodos = await _metodos_mfa(request, user)
            return render(
                request,
                "mfa/verify.html",
                {"totp_ativo": metodos["totp"], "email_ativo": metodos["email"], "erro": msg},
                status_code=status.HTTP_400_BAD_REQUEST,
            )

        try:
            factor_id = await mfa.fator_verificado_id(tokens)
        except mfa.MfaErro as exc:
            log.warning("MFA verify: list_factors falhou (user=%s): %s", user.id, exc)
            return await _erro("Serviço de verificação indisponível. Tente novamente.")
        if factor_id is None:
            # Não tem fator para verificar (ex.: reset pelo TI no meio do fluxo).
            return RedirectResponse("/mfa", status_code=status.HTTP_303_SEE_OTHER)

        try:
            novos = await mfa.confirmar(tokens, factor_id, (codigo or "").strip().replace(" ", ""))
        except mfa.MfaErro as exc:
            log.info("MFA verify recusado (user=%s): %s", user.id, exc)
            return await _erro("Código inválido ou expirado. Tente novamente.")

        resposta = RedirectResponse(home_for(user.role), status_code=status.HTTP_303_SEE_OTHER)
        set_session(resposta, novos)
        if lembrar_dispositivo:
            mfa_remember.lembrar_dispositivo(resposta, get_settings(), user.id)
        return resposta

    @router.post("/verify/email/enviar")
    @limiter.limit("10/minute")
    async def verify_email_enviar(
        request: Request,
        user: CurrentUser = Depends(get_current_user),
        _: None = Depends(_csrf_guard),
    ):
        metodos = await _metodos_mfa(request, user)
        try:
            await mfa_email.enviar_codigo(user.id, user.email or "")
        except mfa_email.MfaErro as exc:
            log.warning("MFA verify/email/enviar falhou (user=%s): %s", user.id, exc)
            return render(
                request,
                "mfa/verify.html",
                {
                    "totp_ativo": metodos["totp"],
                    "email_ativo": metodos["email"],
                    "erro": "Não foi possível enviar o código. Aguarde um instante e tente novamente.",
                },
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            )
        return render(
            request,
            "mfa/verify.html",
            {
                "totp_ativo": metodos["totp"],
                "email_ativo": metodos["email"],
                "email_codigo_enviado": True,
                "email": user.email,
            },
        )

    @router.post("/verify/email")
    @limiter.limit("10/minute")
    async def verify_email_submit(
        request: Request,
        codigo: str = Form(...),
        lembrar_dispositivo: bool = Form(False),
        user: CurrentUser = Depends(get_current_user),
        _: None = Depends(_csrf_guard),
    ):
        try:
            await mfa_email.verificar_codigo(user.id, (codigo or "").strip().replace(" ", ""))
        except mfa_email.MfaErro as exc:
            log.info("MFA verify/email recusado (user=%s): %s", user.id, exc)
            metodos = await _metodos_mfa(request, user)
            return render(
                request,
                "mfa/verify.html",
                {
                    "totp_ativo": metodos["totp"],
                    "email_ativo": metodos["email"],
                    "email_codigo_enviado": True,
                    "erro": "Código inválido ou expirado. Tente novamente ou peça um novo código.",
                },
                status_code=status.HTTP_400_BAD_REQUEST,
            )
        resposta = RedirectResponse(home_for(user.role), status_code=status.HTTP_303_SEE_OTHER)
        mfa_email_stepup.marcar_email_verificado(resposta, get_settings(), user.id)
        if lembrar_dispositivo:
            mfa_remember.lembrar_dispositivo(resposta, get_settings(), user.id)
        return resposta

    app.include_router(router)
