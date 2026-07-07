"""Portal do Cliente (Fase 3) — dashboard, abertura e detalhe de chamados.

Área do papel CLIENTE. A abertura de chamado é por **Categoria + Assunto**
(não há dimensão de "produto" — decisão de produto na aprovação do protótipo).
Quando o chamado está RESOLVIDO, o autor pode **avaliá-lo de 1 a 5 estrelas**
(fonte do CSAT, Seção 6). Todo acesso a dados passa por RLS (Seção 3.1).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

# Marketing trabalha por demanda: a data de entrega deve respeitar um mínimo de
# 48h (2 dias) para início de desenvolvimento.
_TZ_BR = ZoneInfo("America/Sao_Paulo")
_ENTREGA_MIN_DIAS = 2

SETORES = [
    "Brigadistas",
    "Comercial",
    "Controladoria",
    "Diretoria",
    "Dpto Químico",
    "Financeiro",
    "Marketing",
    "RH",
    "SIG",
]


def _data_entrega_min() -> date:
    """Menor data de entrega permitida (hoje + 48h, no fuso de Brasília)."""
    return datetime.now(_TZ_BR).date() + timedelta(days=_ENTREGA_MIN_DIAS)

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status, BackgroundTasks
from fastapi.responses import RedirectResponse

from app.auth.dependencies import CurrentUser, get_current_user
from app.config import get_settings
from app.db import rls_request_scope
from app.ratelimit import limiter
from app.repositories.chamados import (
    PRIORIDADES,
    ChamadosRepo,
    get_chamados_repo,
    validar_nota,
)
from app.anexos import (
    access_token as _access_token,
    assinar_anexos as _assinar_anexos,
    enviar_uploads as _enviar_uploads,
    processar_uploads as _processar_uploads,
    validar_uploads as _validar_uploads,
)
from app.security.csrf import get_csrf
from app.security.uploads import UploadInvalido
from app.templating import render

log = logging.getLogger("app.portal")

router = APIRouter(prefix="/portal", tags=["portal"])


@dataclass(frozen=True)
class PortalCtx:
    user: CurrentUser
    perfil: dict


async def portal_context(
    user: CurrentUser = Depends(get_current_user),
    repo: ChamadosRepo = Depends(get_chamados_repo),
):
    """Contexto do portal (abertura + "meus chamados").

    Aberto a **qualquer usuário autenticado**: funcionários e também staff
    (RH/Marketing/TI) podem abrir chamados para qualquer departamento. A fila
    de atendimento por departamento é o Workspace do operador (Fase 4).
    Exige perfil com org interna associada (`empresa_id`).

    Dependência ``yield``: abre **uma** conexão RLS por request; todas as queries
    do request a reusam (performance — Seção 2.1).
    """
    async with rls_request_scope(user.claims):
        perfil = await repo.perfil(user.claims)
        if not perfil or perfil.get("empresa_id") is None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Perfil sem organização associada. Contate o administrador.",
            )
        yield PortalCtx(user=user, perfil=perfil)


async def _csrf_guard(request: Request) -> None:
    await get_csrf().validate(request)


def pode_avaliar(chamado: dict, user_id: str) -> bool:
    """Regra de UI: autor + RESOLVIDO podem avaliar (RLS reforça no banco)."""
    return (
        chamado.get("status") == "RESOLVIDO"
        and str(chamado.get("cliente_id")) == str(user_id)
    )


def _stats_de(chamados: list[dict]) -> dict[str, int]:
    """Contagem por status a partir dos chamados já buscados — evita uma query
    extra no dashboard (o funcionário tem poucos chamados; escopo é o próprio)."""
    por: dict[str, int] = {}
    for c in chamados:
        por[c["status"]] = por.get(c["status"], 0) + 1
    return {
        "total": len(chamados),
        "novo": por.get("NOVO", 0),
        "em_atendimento": por.get("EM_ATENDIMENTO", 0),
        "aguardando": por.get("AGUARDANDO", 0),
        "resolvido": por.get("RESOLVIDO", 0),
    }


@router.get("")
async def dashboard(
    request: Request,
    ctx: PortalCtx = Depends(portal_context),
    repo: ChamadosRepo = Depends(get_chamados_repo),
):
    chamados = await repo.listar(ctx.user.claims)
    stats = _stats_de(chamados)
    return render(
        request,
        "portal/dashboard.html",
        {"perfil": ctx.perfil, "chamados": chamados, "stats": stats},
    )


async def _render_form(
    request: Request,
    ctx: PortalCtx,
    repo: ChamadosRepo,
    *,
    erro: str | None = None,
    form: dict | None = None,
    status_code: int = status.HTTP_200_OK,
):
    """Renderiza o formulário de abertura (usado no GET e no re-render de erro).
    Preserva o que o usuário digitou em ``form`` e recarrega as subcategorias da
    categoria escolhida (para o select vir preenchido no erro)."""
    form = form or {}
    departamentos = await repo.departamentos_ativos(ctx.user.claims)
    # Categorias pertencem ao departamento (0019): só carregam após escolher o setor.
    dep_sel = form.get("departamento_id") or ""
    categorias = (
        await repo.categorias_ativas(ctx.user.claims, dep_sel) if dep_sel else []
    )
    subcategorias: list[dict] = []
    if form.get("categoria_id"):
        subcategorias = await repo.subcategorias_ativas(ctx.user.claims, form["categoria_id"])
    # Id do departamento "Marketing" — o front usa para exibir o aviso de prazo (48h)
    # e o texto de ajuda específico da descrição (ver novo_chamado.js).
    marketing_dep_id = next(
        (str(d["id"]) for d in departamentos if (d.get("nome") or "").strip().lower() == "marketing"),
        "",
    )
    return render(
        request,
        "portal/novo_chamado.html",
        {
            "perfil": ctx.perfil,
            "categorias": categorias,
            "departamentos": departamentos,
            "subcategorias": subcategorias,
            "marketing_dep_id": marketing_dep_id,
            "prioridades": PRIORIDADES,
            "data_entrega_min": _data_entrega_min().isoformat(),
            "form": form,
            "erro": erro,
            "setores": SETORES,
        },
        status_code=status_code,
    )


@router.get("/chamados/novo")
async def novo_chamado_form(
    request: Request,
    ctx: PortalCtx = Depends(portal_context),
    repo: ChamadosRepo = Depends(get_chamados_repo),
):
    return await _render_form(request, ctx, repo)


@router.get("/chamados/categorias")
async def categorias_fragmento(
    request: Request,
    departamento_id: str = "",
    ctx: PortalCtx = Depends(portal_context),
    repo: ChamadosRepo = Depends(get_chamados_repo),
):
    """Cascade da abertura: <option>s de categoria do departamento escolhido
    (carregado via HTMX quando o usuário troca o departamento). Declarado ANTES
    da rota dinâmica ``/chamados/{chamado_id}``."""
    departamento_id = departamento_id.strip()
    cats = (
        await repo.categorias_ativas(ctx.user.claims, departamento_id)
        if departamento_id
        else []
    )
    return render(request, "portal/_categorias_options.html", {"categorias": cats})


@router.get("/chamados/subcategorias")
async def subcategorias_fragmento(
    request: Request,
    categoria_id: str = "",
    ctx: PortalCtx = Depends(portal_context),
    repo: ChamadosRepo = Depends(get_chamados_repo),
):
    """Cascade da abertura: <option>s de subcategoria para a categoria escolhida
    (carregado via HTMX quando o usuário muda a categoria). Declarado ANTES da
    rota dinâmica ``/chamados/{chamado_id}``."""
    categoria_id = categoria_id.strip()
    subs = (
        await repo.subcategorias_ativas(ctx.user.claims, categoria_id)
        if categoria_id
        else []
    )
    return render(request, "portal/_subcategorias_options.html", {"subcategorias": subs})


@router.post("/chamados")
@limiter.limit("15/minute")
async def criar_chamado(
    request: Request,
    titulo: str = Form(...),           # "Assunto"
    descricao: str = Form(...),
    departamento_id: str = Form(""),   # destino: TI / RH / Marketing
    categoria_id: str = Form(""),
    subcategoria_id: str = Form(""),
    prioridade: str = Form("MEDIA"),
    setor: str = Form(""),             # setor demandante
    data_entrega: str = Form(""),      # fluxo por demanda (Marketing)
    ctx: PortalCtx = Depends(portal_context),
    repo: ChamadosRepo = Depends(get_chamados_repo),
    _: None = Depends(_csrf_guard),
):
    titulo = titulo.strip()
    descricao = descricao.strip()
    departamento_id = departamento_id.strip()
    categoria_id = categoria_id.strip()
    subcategoria_id = subcategoria_id.strip()
    setor = setor.strip()
    data_entrega = data_entrega.strip()
    prioridade = prioridade.upper()
    if prioridade not in PRIORIDADES:
        prioridade = "MEDIA"

    form = {
        "departamento_id": departamento_id,
        "categoria_id": categoria_id,
        "subcategoria_id": subcategoria_id,
        "titulo": titulo,
        "descricao": descricao,
        "prioridade": prioridade,
        "setor": setor,
        "data_entrega": data_entrega,
    }

    async def _erro(msg: str, code: int = status.HTTP_400_BAD_REQUEST):
        return await _render_form(request, ctx, repo, erro=msg, form=form, status_code=code)

    # Departamento, categoria e assunto/descrição são obrigatórios.
    if not departamento_id:
        return await _erro("Selecione o departamento de destino do chamado.")
    if not setor:
        return await _erro("Informe o setor para o qual a demanda está sendo pedida.")
    if setor not in SETORES:
        return await _erro("Setor selecionado inválido.")

    # Marketing trabalha por DEMANDA: em vez de prioridade, exige uma DATA DE
    # ENTREGA com no mínimo 48h (2 dias). Para os demais setores, mantém a prioridade.
    departamentos = await repo.departamentos_ativos(ctx.user.claims)
    marketing_id = next(
        (str(d["id"]) for d in departamentos if (d.get("nome") or "").strip().lower() == "marketing"),
        "",
    )
    is_marketing = bool(marketing_id) and departamento_id == marketing_id
    data_entrega_val: date | None = None
    if is_marketing:
        prioridade = "MEDIA"  # não usada no fluxo por demanda
        if not data_entrega:
            return await _erro("Informe a data de entrega desejada (mínimo de 48h).")
        try:
            escolhida = date.fromisoformat(data_entrega)
        except ValueError:
            return await _erro("Data de entrega inválida.")
        minimo = _data_entrega_min()
        if escolhida < minimo:
            return await _erro(
                "A data de entrega deve ser a partir de "
                f"{minimo.strftime('%d/%m/%Y')} — mínimo de 48h para início do desenvolvimento."
            )
        data_entrega_val = escolhida
    if not categoria_id:
        return await _erro("Selecione a categoria do chamado.")
    # A categoria precisa pertencer ao departamento escolhido (0019 — defesa em
    # profundidade contra POST forjado).
    if not await repo.categoria_valida(
        ctx.user.claims, categoria_id=categoria_id, departamento_id=departamento_id
    ):
        return await _erro("A categoria não pertence ao departamento escolhido.")
    # Subcategoria só é exigida quando a categoria tem subcategorias ativas
    # (categorias como "Identidade Visual" podem não ter).
    subs_da_categoria = await repo.subcategorias_ativas(ctx.user.claims, categoria_id)
    if subs_da_categoria:
        if not subcategoria_id:
            return await _erro("Selecione a subcategoria do chamado.")
        if not await repo.subcategoria_valida(
            ctx.user.claims, categoria_id=categoria_id, subcategoria_id=subcategoria_id
        ):
            return await _erro("A subcategoria não pertence à categoria escolhida.")
    else:
        subcategoria_id = ""  # categoria sem subcategorias → chamado sem subcategoria
    if not titulo or not descricao:
        return await _erro("Informe o assunto e a descrição do chamado.")

    # Anexos lidos direto do multipart. Não declaramos ``UploadFile`` como parâmetro
    # aqui porque, neste endpoint com ``@limiter.limit`` (slowapi embrulha a função)
    # + ``from __future__ import annotations``, o FastAPI não resolve ``list[UploadFile]``
    # e quebra na introspecção. Ler do form evita a annotation problemática.
    form_data = await request.form()
    arquivos = [f for f in form_data.getlist("arquivos") if getattr(f, "filename", "")]

    volume_str = form_data.get("volume") or "1"
    try:
        volume_val = int(volume_str)
    except ValueError:
        volume_val = 1
    origem_demanda_val = form_data.get("origem_demanda") or "Solicitação"

    # Valida anexos ANTES de criar (barra tipos/tamanhos inválidos sem efeito colateral).
    try:
        validados = await _validar_uploads(
            arquivos, max_bytes=get_settings().anexo_max_bytes
        )
    except UploadInvalido as exc:
        return await _erro(str(exc), status.HTTP_422_UNPROCESSABLE_ENTITY)

    novo = await repo.criar(
        ctx.user.claims,
        empresa_id=str(ctx.perfil["empresa_id"]),
        cliente_id=ctx.user.id,
        categoria_id=categoria_id,
        subcategoria_id=subcategoria_id or None,
        departamento_id=departamento_id,
        titulo=titulo,
        descricao=descricao,
        prioridade=prioridade,
        setor=setor,
        data_entrega=data_entrega_val,
        volume=volume_val,
        origem_demanda=origem_demanda_val,
    )

    # Anexos da abertura viram a primeira mensagem (pública) do autor.
    if validados:
        try:
            anexos = await _enviar_uploads(
                request, validados, empresa_id=str(ctx.perfil["empresa_id"]),
                chamado_id=str(novo["id"]),
            )
            if anexos:
                await repo.adicionar_mensagem(
                    ctx.user.claims, str(novo["id"]),
                    remetente_id=ctx.user.id, conteudo="", anexos=anexos,
                )
        except UploadInvalido as exc:
            # Chamado já criado; não trava a abertura por falha pontual de Storage.
            log.warning("Anexo da abertura falhou (chamado %s): %s", novo["id"], exc)

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
    settings = get_settings()
    return render(
        request,
        "portal/chamado_detalhe.html",
        {
            "perfil": ctx.perfil,
            "chamado": chamado,
            "mensagens": mensagens,
            "pode_avaliar": pode_avaliar(chamado, ctx.user.id),
            # Config do Realtime no browser (Seção 6.1): anon key + JWT do usuário.
            "supabase_url": settings.supabase_url or None,
            "anon_key": settings.supabase_anon_key or None,
            "access_token": _access_token(request),
        },
    )


@router.get("/chamados/{chamado_id}/mensagens/fragmento")
async def mensagens_fragmento(
    request: Request,
    chamado_id: str,
    ctx: PortalCtx = Depends(portal_context),
    repo: ChamadosRepo = Depends(get_chamados_repo),
):
    """Fragmento da conversa (HTMX polling + refresh disparado pelo Realtime).
    Re-renderiza no servidor — RLS aplicada e signed URLs de anexo regeneradas."""
    chamado = await repo.obter(ctx.user.claims, chamado_id)
    if chamado is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Chamado não encontrado.")
    mensagens = await repo.mensagens(ctx.user.claims, chamado_id)
    await _assinar_anexos(request, mensagens)
    return render(request, "portal/_mensagens.html", {"chamado": chamado, "mensagens": mensagens})


@router.post("/chamados/{chamado_id}/mensagens")
async def responder_chamado(
    request: Request,
    chamado_id: str,
    background_tasks: BackgroundTasks,
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

    # Buscar informações do chamado para disparar notificação por email
    chamado = await repo.obter(ctx.user.claims, chamado_id)
    if chamado:
        from app.notification import agendar_notificacao_email
        await agendar_notificacao_email(
            background_tasks, chamado, ctx.user.id, conteudo or "[Arquivo anexo]"
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
