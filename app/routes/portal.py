"""Portal do Cliente (Fase 3) — dashboard, abertura e detalhe de chamados.

Área do papel CLIENTE. A abertura de chamado é por **Categoria + Assunto**
(não há dimensão de "produto" — decisão de produto na aprovação do protótipo).
Quando o chamado está RESOLVIDO, o autor pode **avaliá-lo de 1 a 5 estrelas**
(fonte do CSAT, Seção 6). Todo acesso a dados passa por RLS (Seção 3.1).
"""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import RedirectResponse

from app.auth.dependencies import CurrentUser, require_role
from app.repositories.chamados import (
    PRIORIDADES,
    ChamadosRepo,
    get_chamados_repo,
    validar_nota,
)
from app.security.csrf import get_csrf
from app.templating import render

router = APIRouter(prefix="/portal", tags=["portal"])


@dataclass(frozen=True)
class PortalCtx:
    user: CurrentUser
    perfil: dict


async def portal_context(
    user: CurrentUser = Depends(require_role("CLIENTE")),
    repo: ChamadosRepo = Depends(get_chamados_repo),
) -> PortalCtx:
    """Carrega o perfil do CLIENTE e exige empresa associada (multi-tenant)."""
    perfil = await repo.perfil(user.claims)
    if not perfil or perfil.get("empresa_id") is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Perfil sem empresa associada. Contate o administrador.",
        )
    return PortalCtx(user=user, perfil=perfil)


async def _csrf_guard(request: Request) -> None:
    await get_csrf().validate(request)


def pode_avaliar(chamado: dict, user_id: str) -> bool:
    """Regra de UI: autor + RESOLVIDO podem avaliar (RLS reforça no banco)."""
    return (
        chamado.get("status") == "RESOLVIDO"
        and str(chamado.get("cliente_id")) == str(user_id)
    )


@router.get("")
async def dashboard(
    request: Request,
    ctx: PortalCtx = Depends(portal_context),
    repo: ChamadosRepo = Depends(get_chamados_repo),
):
    chamados = await repo.listar(ctx.user.claims)
    stats = await repo.stats(ctx.user.claims)
    return render(
        request,
        "portal/dashboard.html",
        {"perfil": ctx.perfil, "chamados": chamados, "stats": stats},
    )


@router.get("/chamados/novo")
async def novo_chamado_form(
    request: Request,
    ctx: PortalCtx = Depends(portal_context),
    repo: ChamadosRepo = Depends(get_chamados_repo),
):
    categorias = await repo.categorias_ativas(ctx.user.claims)
    return render(
        request,
        "portal/novo_chamado.html",
        {"perfil": ctx.perfil, "categorias": categorias, "prioridades": PRIORIDADES},
    )


@router.post("/chamados")
async def criar_chamado(
    request: Request,
    titulo: str = Form(...),           # "Assunto"
    descricao: str = Form(...),
    categoria_id: str = Form(""),
    prioridade: str = Form("MEDIA"),
    ctx: PortalCtx = Depends(portal_context),
    repo: ChamadosRepo = Depends(get_chamados_repo),
    _: None = Depends(_csrf_guard),
):
    titulo = titulo.strip()
    descricao = descricao.strip()
    prioridade = prioridade.upper()
    if prioridade not in PRIORIDADES:
        prioridade = "MEDIA"

    if not titulo or not descricao:
        categorias = await repo.categorias_ativas(ctx.user.claims)
        return render(
            request,
            "portal/novo_chamado.html",
            {
                "perfil": ctx.perfil,
                "categorias": categorias,
                "prioridades": PRIORIDADES,
                "erro": "Informe o assunto e a descrição do chamado.",
            },
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    novo = await repo.criar(
        ctx.user.claims,
        empresa_id=str(ctx.perfil["empresa_id"]),
        cliente_id=ctx.user.id,
        categoria_id=categoria_id or None,
        titulo=titulo,
        descricao=descricao,
        prioridade=prioridade,
    )
    return RedirectResponse(
        f"/portal/chamados/{novo['id']}", status_code=status.HTTP_303_SEE_OTHER
    )


@router.get("/chamados/{chamado_id}")
async def detalhe_chamado(
    request: Request,
    chamado_id: str,
    ctx: PortalCtx = Depends(portal_context),
    repo: ChamadosRepo = Depends(get_chamados_repo),
):
    chamado = await repo.obter(ctx.user.claims, chamado_id)
    if chamado is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Chamado não encontrado.")
    mensagens = await repo.mensagens(ctx.user.claims, chamado_id)
    return render(
        request,
        "portal/chamado_detalhe.html",
        {
            "perfil": ctx.perfil,
            "chamado": chamado,
            "mensagens": mensagens,
            "pode_avaliar": pode_avaliar(chamado, ctx.user.id),
        },
    )


@router.post("/chamados/{chamado_id}/mensagens")
async def responder_chamado(
    request: Request,
    chamado_id: str,
    conteudo: str = Form(...),
    ctx: PortalCtx = Depends(portal_context),
    repo: ChamadosRepo = Depends(get_chamados_repo),
    _: None = Depends(_csrf_guard),
):
    conteudo = conteudo.strip()
    if conteudo:
        await repo.adicionar_mensagem(
            ctx.user.claims, chamado_id, remetente_id=ctx.user.id, conteudo=conteudo
        )
    return RedirectResponse(
        f"/portal/chamados/{chamado_id}", status_code=status.HTTP_303_SEE_OTHER
    )


@router.post("/chamados/{chamado_id}/avaliacao")
async def avaliar_chamado(
    request: Request,
    chamado_id: str,
    nota: str = Form(...),
    comentario: str = Form(""),
    ctx: PortalCtx = Depends(portal_context),
    repo: ChamadosRepo = Depends(get_chamados_repo),
    _: None = Depends(_csrf_guard),
):
    """Registra a avaliação 1–5 do autor.

    Com HTMX (header ``HX-Request``) devolve o fragmento atualizado; sem HTMX,
    aplica o padrão Post/Redirect/Get de volta ao detalhe do chamado.
    """
    is_htmx = request.headers.get("HX-Request") == "true"
    chamado = await repo.obter(ctx.user.claims, chamado_id)
    if chamado is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Chamado não encontrado.")

    def fragmento(ctx_extra: dict, code: int = 200):
        return render(
            request,
            "portal/_avaliacao.html",
            {"chamado": chamado, **ctx_extra},
            status_code=code,
        )

    redir = RedirectResponse(
        f"/portal/chamados/{chamado_id}", status_code=status.HTTP_303_SEE_OTHER
    )

    try:
        nota_int = validar_nota(nota)
    except ValueError as exc:
        if is_htmx:
            return fragmento(
                {"pode_avaliar": pode_avaliar(chamado, ctx.user.id), "erro": str(exc)},
                status.HTTP_422_UNPROCESSABLE_ENTITY,
            )
        return redir

    if not pode_avaliar(chamado, ctx.user.id):
        if is_htmx:
            return fragmento(
                {"pode_avaliar": False,
                 "erro": "Só o autor pode avaliar, e apenas após a resolução."},
                status.HTTP_403_FORBIDDEN,
            )
        return redir

    atualizado = await repo.avaliar(
        ctx.user.claims, chamado_id, nota=nota_int, comentario=(comentario.strip() or None)
    )
    chamado = {**chamado, **(atualizado or {})}
    if is_htmx:
        return fragmento({"pode_avaliar": pode_avaliar(chamado, ctx.user.id)})
    return redir


def register_portal_routes(app) -> None:
    app.include_router(router)
