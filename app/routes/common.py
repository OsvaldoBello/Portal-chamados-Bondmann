"""Rotas transversais a qualquer papel autenticado (ex.: sino de notificações).

O sino da topbar carrega este fragmento por HTMX ao ser aberto. A lista é
**escopada pela RLS** (funcionário vê os próprios; staff vê o seu setor; TI tudo),
então uma única rota serve portal, workspace e admin.

`/realtime/config` entrega ao browser a config mínima do Realtime (URL do
projeto + anon key + JWT do próprio usuário, lido do cookie no servidor) para o
sino em tempo real (`notificacoes.js`) e o chat. Só dados do próprio usuário —
mesma decisão da Seção 6.2 (o browser recebe URL+anon key+JWT do usuário).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from app.auth.dependencies import CurrentUser, get_current_user
from app.auth.session import ACCESS_COOKIE
from app.config import get_settings
from app.repositories.chamados import ChamadosRepo, get_chamados_repo
from app.templating import render

router = APIRouter(tags=["common"])


@router.get("/notificacoes")
async def notificacoes(
    request: Request,
    user: CurrentUser = Depends(get_current_user),
    repo: ChamadosRepo = Depends(get_chamados_repo),
):
    itens = await repo.notificacoes(user.claims)
    # O staff abre no workspace (atendimento); o funcionário no portal (detalhe).
    base = "/workspace/chamados" if user.role in ("OPERADOR", "ADMIN") else "/portal/chamados"
    return render(request, "_notificacoes.html", {"itens": itens, "base": base})


@router.get("/realtime/config")
async def realtime_config(
    request: Request,
    user: CurrentUser = Depends(get_current_user),
):
    """Config do Realtime para o browser (sino em tempo real + chat).

    Devolve ``{}`` quando o Supabase não está configurado ou não há sessão —
    o cliente degrada para o sino por clique / polling. O token é o do próprio
    usuário (cookie httpOnly, lido no servidor); a RLS aplica na entrega."""
    settings = get_settings()
    token = request.cookies.get(ACCESS_COOKIE)
    if not (settings.supabase_url and settings.supabase_anon_key and token):
        return JSONResponse({})
    return JSONResponse(
        {
            "url": settings.supabase_url,
            "key": settings.supabase_anon_key,
            "token": token,
            "uid": user.id,
        },
        headers={"Cache-Control": "no-store"},
    )


def register_common_routes(app) -> None:
    app.include_router(router)
