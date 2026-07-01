"""Workspace do Operador (Fase 4) — fila por departamento, atendimento e ações.

Área do **staff** (role OPERADOR/ADMIN). O que cada um vê é imposto pela RLS
(Seção 3.3 / migration 0010): TI vê todos os chamados; RH/Marketing veem os do
seu setor. Ações (status/prioridade/atribuição) geram `historico_chamados`; a
resposta pode ser **nota interna** (decidido no servidor). Chat em tempo real
reutiliza o mesmo fragmento/Realtime do portal (Seção 6.1).
"""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import RedirectResponse

from app.auth.dependencies import CurrentUser, require_role
from app.config import get_settings
from app.repositories.chamados import PRIORIDADES, ChamadosRepo, get_chamados_repo
from app.security.csrf import get_csrf
from app.templating import render

router = APIRouter(prefix="/workspace", tags=["workspace"])

STATUS_VALIDOS = ("NOVO", "EM_ATENDIMENTO", "AGUARDANDO", "RESOLVIDO")


@dataclass(frozen=True)
class StaffCtx:
    user: CurrentUser
    perfil: dict


async def staff_context(
    user: CurrentUser = Depends(require_role("OPERADOR", "ADMIN")),
    repo: ChamadosRepo = Depends(get_chamados_repo),
) -> StaffCtx:
    perfil = await repo.perfil(user.claims)
    if not perfil:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Perfil não encontrado.")
    return StaffCtx(user=user, perfil=perfil)


async def _csrf_guard(request: Request) -> None:
    await get_csrf().validate(request)


def _access_token(request: Request) -> str | None:
    from app.auth.session import ACCESS_COOKIE

    return request.cookies.get(ACCESS_COOKIE)


# --------------------------------------------------------------------------
# Fila — Lista e Kanban
# --------------------------------------------------------------------------
@router.get("")
async def fila_lista(
    request: Request,
    status: str = "",
    ctx: StaffCtx = Depends(staff_context),
    repo: ChamadosRepo = Depends(get_chamados_repo),
):
    filtro = status if status in STATUS_VALIDOS else None
    chamados = await repo.fila(ctx.user.claims, status=filtro)
    stats = await repo.fila_stats(ctx.user.claims)
    return render(
        request,
        "workspace/fila.html",
        {"perfil": ctx.perfil, "chamados": chamados, "stats": stats, "filtro": filtro},
    )


@router.get("/fila/fragmento")
async def fila_fragmento(
    request: Request,
    status: str = "",
    ctx: StaffCtx = Depends(staff_context),
    repo: ChamadosRepo = Depends(get_chamados_repo),
):
    filtro = status if status in STATUS_VALIDOS else None
    chamados = await repo.fila(ctx.user.claims, status=filtro)
    return render(request, "workspace/_fila_linhas.html", {"chamados": chamados})


@router.get("/kanban")
async def kanban(
    request: Request,
    ctx: StaffCtx = Depends(staff_context),
    repo: ChamadosRepo = Depends(get_chamados_repo),
):
    chamados = await repo.fila(ctx.user.claims)
    colunas = {s: [c for c in chamados if c["status"] == s] for s in STATUS_VALIDOS}
    stats = await repo.fila_stats(ctx.user.claims)
    return render(
        request,
        "workspace/kanban.html",
        {"perfil": ctx.perfil, "colunas": colunas, "stats": stats, "status_validos": STATUS_VALIDOS},
    )


# --------------------------------------------------------------------------
# Atendimento (tela individual)
# --------------------------------------------------------------------------
async def _carregar_atendimento(request, chamado_id, ctx, repo, **extra):
    chamado = await repo.obter(ctx.user.claims, chamado_id)
    if chamado is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Chamado não encontrado.")
    mensagens = await repo.mensagens(ctx.user.claims, chamado_id)
    operadores = await repo.operadores(ctx.user.claims)
    settings = get_settings()
    ctx_render = {
        "perfil": ctx.perfil,
        "chamado": chamado,
        "mensagens": mensagens,
        "operadores": operadores,
        "prioridades": PRIORIDADES,
        "status_validos": STATUS_VALIDOS,
        "supabase_url": settings.supabase_url or None,
        "anon_key": settings.supabase_anon_key or None,
        "access_token": _access_token(request),
    }
    ctx_render.update(extra)
    return render(request, "workspace/atendimento.html", ctx_render)


@router.get("/chamados/{chamado_id}")
async def atendimento(
    request: Request,
    chamado_id: str,
    ctx: StaffCtx = Depends(staff_context),
    repo: ChamadosRepo = Depends(get_chamados_repo),
):
    return await _carregar_atendimento(request, chamado_id, ctx, repo)


@router.get("/chamados/{chamado_id}/mensagens/fragmento")
async def mensagens_fragmento(
    request: Request,
    chamado_id: str,
    ctx: StaffCtx = Depends(staff_context),
    repo: ChamadosRepo = Depends(get_chamados_repo),
):
    chamado = await repo.obter(ctx.user.claims, chamado_id)
    if chamado is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Chamado não encontrado.")
    mensagens = await repo.mensagens(ctx.user.claims, chamado_id)
    return render(request, "portal/_mensagens.html", {"chamado": chamado, "mensagens": mensagens})


def _voltar(chamado_id: str) -> RedirectResponse:
    return RedirectResponse(
        f"/workspace/chamados/{chamado_id}", status_code=status.HTTP_303_SEE_OTHER
    )


# --------------------------------------------------------------------------
# Ações rápidas (cada uma registra em historico_chamados)
# --------------------------------------------------------------------------
@router.post("/chamados/{chamado_id}/status")
async def mudar_status(
    request: Request,
    chamado_id: str,
    novo_status: str = Form(...),
    ctx: StaffCtx = Depends(staff_context),
    repo: ChamadosRepo = Depends(get_chamados_repo),
    _: None = Depends(_csrf_guard),
):
    if novo_status in STATUS_VALIDOS:
        await repo.alterar_status(ctx.user.claims, chamado_id, novo_status)
    return _voltar(chamado_id)


@router.post("/chamados/{chamado_id}/prioridade")
async def mudar_prioridade(
    request: Request,
    chamado_id: str,
    nova_prioridade: str = Form(...),
    ctx: StaffCtx = Depends(staff_context),
    repo: ChamadosRepo = Depends(get_chamados_repo),
    _: None = Depends(_csrf_guard),
):
    if nova_prioridade in PRIORIDADES:
        await repo.alterar_prioridade(ctx.user.claims, chamado_id, nova_prioridade)
    return _voltar(chamado_id)


@router.post("/chamados/{chamado_id}/atribuir")
async def atribuir(
    request: Request,
    chamado_id: str,
    operador_id: str = Form(""),
    ctx: StaffCtx = Depends(staff_context),
    repo: ChamadosRepo = Depends(get_chamados_repo),
    _: None = Depends(_csrf_guard),
):
    await repo.atribuir(ctx.user.claims, chamado_id, operador_id.strip() or None)
    return _voltar(chamado_id)


@router.post("/chamados/{chamado_id}/mensagens")
async def responder(
    request: Request,
    chamado_id: str,
    conteudo: str = Form(""),
    is_interna: str = Form(""),
    ctx: StaffCtx = Depends(staff_context),
    repo: ChamadosRepo = Depends(get_chamados_repo),
    _: None = Depends(_csrf_guard),
):
    conteudo = conteudo.strip()
    interna = is_interna in ("1", "true", "on", "True")
    if conteudo:
        await repo.responder_staff(
            ctx.user.claims, chamado_id, conteudo=conteudo, is_interna=interna
        )
    return _voltar(chamado_id)


def register_workspace_routes(app) -> None:
    app.include_router(router)
