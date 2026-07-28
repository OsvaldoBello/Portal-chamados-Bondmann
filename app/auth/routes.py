"""Rotas de autenticação: /login, /logout (Fase 2).

Auth via supabase-py async (GoTrue). Em sucesso, grava os tokens em cookies
de sessão e redireciona conforme o papel. Rate limiting aplicado em /login
(Seção 2.4). Não há signup público — o cadastro de colaboradores é feito
direto no Supabase (ver supabase/registro_usuarios.sql).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request, status
from fastapi.responses import RedirectResponse
from slowapi import Limiter

from app.auth import mfa_remember
from app.auth.dependencies import CurrentUser, get_optional_user
from app.auth.session import REFRESH_COOKIE, SessionTokens, clear_session, set_session
from app.auth.supabase_client import create_isolated_client, ensure_supabase
from app.config import get_settings
from app.security.csrf import get_csrf
from app.security.password_policy import SENHA_MIN_CHARS
from app.templating import render

router = APIRouter(tags=["auth"])

# Destino pós-login por papel. ADMIN/OPERADOR (staff) vão ao Workspace (Fase 4);
# o painel /admin (Fase 5) ainda não existe. CLIENTE (funcionário) vai ao Portal.
_HOME_BY_ROLE = {"ADMIN": "/workspace", "OPERADOR": "/workspace", "CLIENTE": "/portal"}


def home_for(role: str) -> str:
    return _HOME_BY_ROLE.get(role.upper(), "/portal")


def register_auth_routes(app, limiter: Limiter) -> None:
    """Inclui o router aplicando rate limit nas rotas sensíveis."""

    @router.get("/login")
    async def login_form(request: Request):
        return render(request, "login.html")

    @router.post("/login")
    @limiter.limit("5/minute")
    async def login_submit(
        request: Request,
        email: str = Form(...),
        password: str = Form(...),
        _: None = Depends(_csrf_guard),
    ):
        # ensure_supabase() estoura RuntimeError se o Supabase não estiver
        # configurado (.env incompleto). Sem este guard, viraria 500 "Erro interno"
        # na tela de login em vez de uma mensagem amigável.
        try:
            supabase = await ensure_supabase()
        except RuntimeError:
            return render(
                request,
                "login.html",
                {"erro": "Serviço de login indisponível: configuração do servidor "
                         "incompleta. Contate o administrador (TI)."},
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        try:
            result = await supabase.auth.sign_in_with_password(
                {"email": email, "password": password}
            )
        except Exception:
            return render(
                request,
                "login.html",
                {"erro": "Credenciais inválidas."},
                status_code=status.HTTP_401_UNAUTHORIZED,
            )

        session = result.session
        user = result.user
        if session is None or user is None:
            return render(
                request,
                "login.html",
                {"erro": "Falha ao autenticar."},
                status_code=status.HTTP_401_UNAUTHORIZED,
            )

        role = _role_from_user(user)
        # MFA (item 3.3): a sessão recém-criada por senha é aal1. Quem já tem fator
        # verificado vai direto ao step-up em vez da home — os fatores já vêm na
        # resposta do login, então isso não custa nenhuma chamada extra ao GoTrue.
        # 2026-07-27 (pedido do usuário): "lembrar este dispositivo" pula o
        # step-up aqui também — é ESTE redirect que mandava até quem marcou o
        # checkbox de volta para `/mfa/verify` a cada novo login (o cookie só
        # era checado no gate do painel `/admin`, tarde demais: o usuário nunca
        # chegava lá sem passar por aqui primeiro). A sessão segue aal1 (só o
        # código real eleva a aal2); é o gate do `/admin` que trata o cookie
        # como equivalente ao step-up.
        tem_fator = _tem_fator_verificado(user)
        dispositivo_ok = tem_fator and mfa_remember.dispositivo_confiavel(
            request, get_settings(), user.id
        )
        destino = "/mfa/verify" if (tem_fator and not dispositivo_ok) else home_for(role)
        response = RedirectResponse(destino, status_code=status.HTTP_303_SEE_OTHER)
        set_session(
            response,
            SessionTokens(session.access_token, session.refresh_token),
        )
        return response

    # Sem signup público: o cadastro de colaboradores é feito DIRETO no Supabase
    # (Authentication > Users) e a promoção de papel/departamento via SQL — ver
    # supabase/registro_usuarios.sql. O trigger handle_new_user cria o perfil
    # CLIENTE vinculado à org interna. Não há rota /cadastro.

    # ------------------------------------------------------------------
    # Redefinição de senha por CÓDIGO (OTP) enviado ao e-mail (Fase 5).
    # Fluxo (Supabase Auth): o funcionário clica em "Esqueci a senha", informa
    # o e-mail (já cadastrado em auth.users) → GoTrue envia um código de 6
    # dígitos → o funcionário informa o código + a nova senha. Evita consultar
    # a senha pessoal de cada colaborador.
    #   POST /esqueci-senha  -> reset_password_for_email (envia o código)
    #   POST /redefinir-senha -> verify_otp(type=recovery) + update_user(password)
    # ⚠️ CONFIG NECESSÁRIA no Supabase: o template de e-mail "Reset Password"
    #    deve conter {{ .Token }} (o código OTP), não só o link de confirmação.
    # ------------------------------------------------------------------
    @router.get("/esqueci-senha")
    async def esqueci_form(request: Request):
        return render(request, "esqueci_senha.html")

    @router.post("/esqueci-senha")
    @limiter.limit("5/minute")
    async def esqueci_submit(
        request: Request,
        email: str = Form(...),
        _: None = Depends(_csrf_guard),
    ):
        email = email.strip().lower()
        # Dispara o envio do código. Nunca revelamos se o e-mail existe (anti-enumeração):
        # o resultado da UI é sempre o mesmo.
        try:
            supabase = await ensure_supabase()
            # Nome do método varia entre versões do gotrue-py.
            enviar = getattr(supabase.auth, "reset_password_for_email", None) or getattr(
                supabase.auth, "reset_password_email", None
            )
            if enviar is not None:
                await enviar(email)
        except Exception:  # noqa: BLE001 — falha silenciosa por privacidade
            pass
        return render(
            request,
            "redefinir_senha.html",
            {
                "email": email,
                "info": "Se este e-mail estiver cadastrado, enviamos um código de "
                        "6 dígitos. Verifique sua caixa de entrada (e o spam).",
            },
        )

    @router.get("/redefinir-senha")
    async def redefinir_form(request: Request, email: str = ""):
        return render(request, "redefinir_senha.html", {"email": email.strip().lower()})

    @router.post("/redefinir-senha")
    @limiter.limit("5/minute")
    async def redefinir_submit(
        request: Request,
        email: str = Form(...),
        codigo: str = Form(...),
        senha: str = Form(...),
        senha2: str = Form(...),
        _: None = Depends(_csrf_guard),
    ):
        email = email.strip().lower()
        codigo = codigo.strip().replace(" ", "")

        def _erro(msg: str):
            return render(
                request,
                "redefinir_senha.html",
                {"email": email, "erro": msg},
                status_code=status.HTTP_400_BAD_REQUEST,
            )

        if len(senha) < SENHA_MIN_CHARS:
            return _erro(f"A nova senha deve ter ao menos {SENHA_MIN_CHARS} caracteres.")
        if senha != senha2:
            return _erro("As senhas não coincidem.")
        if not codigo:
            return _erro("Informe o código recebido por e-mail.")

        # Cliente isolado: verify_otp + update_user mutam a sessão do GoTrue e não
        # devem competir com o cliente global compartilhado.
        try:
            client = await create_isolated_client()
        except Exception:  # noqa: BLE001
            return _erro("Serviço de autenticação indisponível. Tente mais tarde.")
        try:
            resultado = await client.auth.verify_otp(
                {"email": email, "token": codigo, "type": "recovery"}
            )
        except Exception:  # noqa: BLE001 — código inválido/expirado
            return _erro("Código inválido ou expirado. Solicite um novo código.")

        if getattr(resultado, "session", None) is None:
            return _erro("Código inválido ou expirado. Solicite um novo código.")

        try:
            await client.auth.update_user({"password": senha})
            await client.auth.sign_out()
        except Exception:  # noqa: BLE001
            return _erro("Não foi possível atualizar a senha. Tente novamente.")

        return render(
            request,
            "login.html",
            {"info": "Senha redefinida com sucesso! Faça login com a nova senha."},
        )

    @router.post("/logout")
    async def logout(request: Request, _: None = Depends(_csrf_guard)):
        refresh = request.cookies.get(REFRESH_COOKIE)
        if refresh:
            try:
                await (await ensure_supabase()).auth.sign_out()
            except Exception:
                pass
        response = RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)
        clear_session(response)
        return response

    @router.get("/")
    async def index(user: CurrentUser | None = Depends(get_optional_user)):
        # Sem sessão → login; com sessão → home do papel. Nunca 401 JSON na raiz.
        destino = home_for(user.role) if user else "/login"
        return RedirectResponse(destino, status_code=status.HTTP_303_SEE_OTHER)

    app.include_router(router)


async def _csrf_guard(request: Request) -> None:
    await get_csrf().validate(request)


def _role_from_user(user) -> str:
    meta = getattr(user, "app_metadata", None) or {}
    role = (meta.get("role") or "CLIENTE").upper()
    return role if role in {"ADMIN", "OPERADOR", "CLIENTE"} else "CLIENTE"


def _tem_fator_verificado(user) -> bool:
    """Se o usuário tem um fator MFA já ativado (item 3.3).

    Lê os ``factors`` que o GoTrue devolve no próprio login — fator ``unverified``
    (enroll começado e abandonado) não conta: só quem concluiu a ativação é levado
    ao step-up."""
    fatores = getattr(user, "factors", None) or []
    return any(getattr(f, "status", None) == "verified" for f in fatores)
