"""Meu perfil — foto (Fase 7 — 2026-07-09) e telefone de contato (2026-07-29).

Aberta a **qualquer usuário autenticado** (funcionário ou staff) — reusa o
mesmo contexto do Portal (``portal_context``), já que a rota não é específica
de papel. Upload de imagem no bucket público ``avatares``
(``app/avatar_storage.py``); a validação de tipo/tamanho reusa
``app/security/uploads.py`` (mesmo motor dos anexos), restrita a imagens.

O telefone (``perfis.telefone``, migration 0062) existe para o formulário de
abertura não pedir o mesmo número a cada chamado: ``POST /perfil/telefone``
grava, e ``app/routes/portal.py`` pré-preenche o campo obrigatório da abertura
com ele (e o cadastra sozinho na primeira abertura de quem ainda não tem).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile, status
from fastapi.responses import RedirectResponse

from app.anexos import access_token as _access_token
from app.avatar_storage import AvatarStorageError, avatar_path, enviar_avatar, preparar_avatar
from app.config import get_settings
from app.repositories.chamados import ChamadosRepo, get_chamados_repo, validar_telefone_contato
from app.routes.portal import PortalCtx, portal_context
from app.security.csrf import get_csrf
from app.security.uploads import UploadInvalido
from app.templating import portal_base_template, render

router = APIRouter(prefix="/perfil", tags=["perfil"])


async def _csrf_guard(request: Request) -> None:
    await get_csrf().validate(request)


def _pagina(
    request: Request,
    ctx: PortalCtx,
    *,
    erro: str = "",
    ok: str = "",
    telefone: str | None = None,
    status_code: int = status.HTTP_200_OK,
):
    """Renderiza "Meu perfil". ``telefone`` sobrescreve o valor do campo no
    re-render de erro (preserva o que a pessoa digitou); ``None`` usa o perfil."""
    return render(
        request,
        "portal/perfil.html",
        {
            "perfil": ctx.perfil,
            "erro": erro,
            "ok": ok,
            "telefone": ctx.perfil.get("telefone") or "" if telefone is None else telefone,
            "base_template": portal_base_template(ctx.perfil),
        },
        status_code=status_code,
    )


@router.get("")
async def ver_perfil(
    request: Request,
    erro: str = "",
    ok: str = "",
    ctx: PortalCtx = Depends(portal_context),
):
    return _pagina(request, ctx, erro=erro, ok=ok)


@router.post("")
async def atualizar_perfil(
    request: Request,
    arquivo: UploadFile = File(...),
    ctx: PortalCtx = Depends(portal_context),
    repo: ChamadosRepo = Depends(get_chamados_repo),
    _: None = Depends(_csrf_guard),
):
    def _erro(msg: str):
        return _pagina(
            request, ctx, erro=msg, status_code=status.HTTP_422_UNPROCESSABLE_CONTENT
        )

    if not arquivo or not arquivo.filename:
        return _erro("Selecione uma imagem.")

    settings = get_settings()
    conteudo = await arquivo.read(settings.avatar_max_bytes + 1)
    try:
        processada = preparar_avatar(
            arquivo.filename, conteudo, max_bytes=settings.avatar_max_bytes
        )
    except UploadInvalido as exc:
        return _erro(str(exc))

    token = _access_token(request)
    if not token:
        return _erro("Sessão expirada — faça login de novo e tente enviar a foto outra vez.")

    path = avatar_path(str(ctx.user.id), "png")
    try:
        await enviar_avatar(token, path, processada, "image/png")
    except AvatarStorageError:
        return _erro("Falha ao enviar a foto. Tente novamente.")

    await repo.atualizar_avatar(ctx.user.claims, avatar_path=path)
    return RedirectResponse("/perfil", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/telefone")
async def atualizar_telefone(
    request: Request,
    telefone: str = Form(""),
    ctx: PortalCtx = Depends(portal_context),
    repo: ChamadosRepo = Depends(get_chamados_repo),
    _: None = Depends(_csrf_guard),
):
    """Cadastra/atualiza o telefone de contato do próprio usuário (2026-07-29).

    Mesma validação da abertura (``validar_telefone_contato``) — o número aqui é
    exatamente o que vai pré-preencher aquele campo, então aceitar formato mais
    frouxo aqui só empurraria o erro para o formulário de chamado."""
    try:
        limpo = validar_telefone_contato(telefone)
    except ValueError as exc:
        return _pagina(
            request, ctx, erro=str(exc), telefone=telefone,
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        )
    await repo.atualizar_telefone(ctx.user.claims, telefone=limpo)
    return RedirectResponse(
        "/perfil?ok=Telefone+salvo.", status_code=status.HTTP_303_SEE_OTHER
    )


def register_perfil_routes(app) -> None:
    app.include_router(router)
