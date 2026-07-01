"""Portal do Cliente (Fase 3) — dashboard, abertura e detalhe de chamados.

Área do papel CLIENTE. A abertura de chamado é por **Categoria + Assunto**
(não há dimensão de "produto" — decisão de produto na aprovação do protótipo).
Quando o chamado está RESOLVIDO, o autor pode **avaliá-lo de 1 a 5 estrelas**
(fonte do CSAT, Seção 6). Todo acesso a dados passa por RLS (Seção 3.1).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status
from fastapi.responses import RedirectResponse

from app.auth.dependencies import CurrentUser, require_role
from app.auth.session import ACCESS_COOKIE
from app.repositories.chamados import (
    PRIORIDADES,
    ChamadosRepo,
    get_chamados_repo,
    validar_nota,
)
from app.security.csrf import get_csrf
from app.security.uploads import UploadInvalido, validar_anexo
from app.storage import AnexosStorage, StorageError, get_storage
from app.templating import render

log = logging.getLogger("app.portal")

router = APIRouter(prefix="/portal", tags=["portal"])

# Limite de anexos por mensagem (bound de trabalho/carga).
MAX_ANEXOS = 5


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


def _access_token(request: Request) -> str | None:
    """JWT do usuário (cookie de sessão) — necessário para o Storage sob RLS."""
    return request.cookies.get(ACCESS_COOKIE)


def _storage_opcional() -> AnexosStorage | None:
    """Retorna o Storage se configurado; ``None`` quando ausente (degrada sem erro)."""
    try:
        return get_storage()
    except RuntimeError:
        return None


async def _assinar_anexos(request: Request, mensagens: list[dict]) -> None:
    """Gera signed URLs (TTL 1h) *on-demand* para cada anexo (C2 — nunca cacheado).

    Muta as mensagens in-place adicionando ``url`` a cada anexo. Falha graciosa:
    sem Storage/token, ``url`` fica ``None`` e o template mostra 'indisponível'.
    """
    storage = _storage_opcional()
    token = _access_token(request)
    for m in mensagens:
        for anexo in m.get("anexos") or []:
            anexo["url"] = None
            path = anexo.get("path")
            if storage and token and path:
                anexo["url"] = await storage.signed_url(token, path)


async def _processar_uploads(
    request: Request,
    arquivos: list[UploadFile],
    *,
    empresa_id: str,
    chamado_id: str,
    max_bytes: int,
) -> list[dict]:
    """Valida (tamanho/allow-list/magic bytes), envia ao Storage e devolve os
    metadados persistíveis. Levanta :class:`UploadInvalido` em qualquer violação."""
    reais = [a for a in arquivos if a and a.filename]
    if not reais:
        return []
    if len(reais) > MAX_ANEXOS:
        raise UploadInvalido(f"Máximo de {MAX_ANEXOS} anexos por mensagem.")

    storage = _storage_opcional()
    token = _access_token(request)
    if storage is None or token is None:
        raise UploadInvalido("Envio de anexos indisponível no momento.")

    anexos: list[dict] = []
    for arquivo in reais:
        # Leitura limitada a max_bytes+1 para rejeitar excesso sem carregar ilimitado.
        conteudo = await arquivo.read(max_bytes + 1)
        validado = validar_anexo(arquivo.filename, conteudo, max_bytes=max_bytes)
        path = AnexosStorage.path(empresa_id, chamado_id, validado.nome_objeto)
        try:
            await storage.upload(token, path, validado.conteudo, validado.mime)
        except StorageError as exc:
            raise UploadInvalido("Falha ao enviar o anexo. Tente novamente.") from exc
        anexos.append(
            {
                "path": path,
                "nome": validado.nome_original,
                "mime": validado.mime,
                "tamanho": validado.tamanho,
            }
        )
    return anexos


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
    await _assinar_anexos(request, mensagens)
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
    conteudo: str = Form(""),
    arquivos: list[UploadFile] = File(default=[]),
    ctx: PortalCtx = Depends(portal_context),
    repo: ChamadosRepo = Depends(get_chamados_repo),
    _: None = Depends(_csrf_guard),
):
    from app.config import get_settings

    conteudo = conteudo.strip()

    def _erro(msg: str, code: int):
        return render(
            request,
            "portal/_responder_erro.html",
            {"chamado_id": chamado_id, "erro": msg},
            status_code=code,
        )

    try:
        anexos = await _processar_uploads(
            request,
            arquivos,
            empresa_id=str(ctx.perfil["empresa_id"]),
            chamado_id=chamado_id,
            max_bytes=get_settings().anexo_max_bytes,
        )
    except UploadInvalido as exc:
        return _erro(str(exc), status.HTTP_422_UNPROCESSABLE_ENTITY)

    # Exige conteúdo OU ao menos um anexo (não grava mensagem totalmente vazia).
    if not conteudo and not anexos:
        return _erro("Escreva uma mensagem ou anexe um arquivo.", status.HTTP_400_BAD_REQUEST)

    await repo.adicionar_mensagem(
        ctx.user.claims,
        chamado_id,
        remetente_id=ctx.user.id,
        conteudo=conteudo,
        anexos=anexos,
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
