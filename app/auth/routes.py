"""Rotas de autenticação: /login, /cadastro, /logout (Fase 2).

Auth via supabase-py async (GoTrue). Em sucesso, grava os tokens em cookies
de sessão e redireciona conforme o papel. Rate limiting aplicado em /login e
/cadastro (Seção 2.4).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request, status
from fastapi.responses import RedirectResponse
from slowapi import Limiter

from app.auth.dependencies import CurrentUser, get_current_user
from app.auth.session import REFRESH_COOKIE, SessionTokens, clear_session, set_session
from app.auth.supabase_client import get_supabase
from app.security.csrf import get_csrf
from app.templating import render

router = APIRouter(tags=["auth"])

# Destino pós-login por papel (dashboards das Fases 3–5).
_HOME_BY_ROLE = {"ADMIN": "/admin", "OPERADOR": "/workspace", "CLIENTE": "/portal"}


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
        supabase = get_supabase()
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
        if session is None:
            return render(
                request,
                "login.html",
                {"erro": "Falha ao autenticar."},
                status_code=status.HTTP_401_UNAUTHORIZED,
            )

        role = _role_from_user(result.user)
        response = RedirectResponse(home_for(role), status_code=status.HTTP_303_SEE_OTHER)
        set_session(
            response,
            SessionTokens(session.access_token, session.refresh_token),
        )
        return response

    @router.get("/cadastro")
    async def cadastro_form(request: Request):
        return render(request, "cadastro.html")

    @router.post("/cadastro")
    @limiter.limit("3/minute")
    async def cadastro_submit(
        request: Request,
        nome: str = Form(...),
        email: str = Form(...),
        password: str = Form(...),
        _: None = Depends(_csrf_guard),
    ):
        supabase = get_supabase()
        try:
            await supabase.auth.sign_up(
                {
                    "email": email,
                    "password": password,
                    "options": {"data": {"nome": nome}},
                }
            )
        except Exception:
            return render(
                request,
                "cadastro.html",
                {"erro": "Não foi possível concluir o cadastro."},
                status_code=status.HTTP_400_BAD_REQUEST,
            )
        # handle_new_user cria o perfil CLIENTE. Confirmação de e-mail conforme
        # config do projeto; orientamos o usuário a confirmar e fazer login.
        return render(request, "login.html", {"info": "Cadastro realizado. Faça login."})

    @router.post("/logout")
    async def logout(request: Request, _: None = Depends(_csrf_guard)):
        refresh = request.cookies.get(REFRESH_COOKIE)
        if refresh:
            try:
                await get_supabase().auth.sign_out()
            except Exception:
                pass
        response = RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)
        clear_session(response)
        return response

    @router.get("/")
    async def index(user: CurrentUser = Depends(get_current_user)):
        return RedirectResponse(home_for(user.role), status_code=status.HTTP_303_SEE_OTHER)

    app.include_router(router)


async def _csrf_guard(request: Request) -> None:
    await get_csrf().validate(request)


def _role_from_user(user) -> str:
    meta = getattr(user, "app_metadata", None) or {}
    role = (meta.get("role") or "CLIENTE").upper()
    return role if role in {"ADMIN", "OPERADOR", "CLIENTE"} else "CLIENTE"
