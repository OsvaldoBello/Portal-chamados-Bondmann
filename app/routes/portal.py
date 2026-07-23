"""Portal do Cliente (Fase 3) — dashboard, abertura e detalhe de chamados.

Área do papel CLIENTE. A abertura de chamado é por **Categoria + Assunto**
(não há dimensão de "produto" — decisão de produto na aprovação do protótipo).
Quando o chamado está RESOLVIDO, o autor pode **avaliá-lo de 1 a 5 estrelas**
(fonte do CSAT, Seção 6). Todo acesso a dados passa por RLS (Seção 3.1).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from hashlib import sha256

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Form,
    HTTPException,
    Request,
    UploadFile,
    status,
)
from fastapi.responses import RedirectResponse, Response

from app.anexos import (
    access_token as _access_token,
    assinar_anexos as _assinar_anexos,
    enviar_uploads as _enviar_uploads,
    processar_uploads as _processar_uploads,
    validar_uploads as _validar_uploads,
)
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
from app.security.csrf import get_csrf
from app.security.uploads import UploadInvalido
from app.services.portal import PortalService
from app.templating import portal_base_template, render

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
    # Líder de setor (ADMIN com departamento_id — migration 0028) acompanha,
    # nesta mesma página, os chamados abertos por colegas do seu setor (mesmo
    # que destinados a outro departamento); a RLS já restringe quem realmente
    # recebe essas linhas, então OPERADOR/CLIENTE não veem nada aqui mesmo que
    # a query rode. Unifica o que antes era a aba separada "Chamados do
    # Departamento" (`/workspace/departamento`, removida).
    chamados_colegas = None
    if ctx.perfil.get("role") == "ADMIN":
        chamados_colegas = await repo.chamados_departamento(
            ctx.user.claims,
            departamento_id=str(ctx.perfil.get("departamento_id") or "") or None,
        )
    return render(
        request,
        "portal/dashboard.html",
        {
            "perfil": ctx.perfil,
            "chamados": chamados,
            "stats": stats,
            "chamados_colegas": chamados_colegas,
            "base_template": portal_base_template(ctx.perfil),
        },
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
    # Catálogo unificado (0027): todo setor da empresa é um "departamento"; só os
    # que RECEBEM chamado (têm fila/staff) entram no destino do roteamento.
    setores_ativos = await repo.departamentos_ativos(ctx.user.claims)
    departamentos = [d for d in setores_ativos if d.get("recebe_chamados")]
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
    marketing_dep_id = PortalService.marketing_dep_id(departamentos)
    usuarios_copia = await repo.usuarios_para_copia(ctx.user.claims, excluir_id=ctx.user.id)
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
            "data_entrega_min": PortalService.data_entrega_min().isoformat(),
            "form": form,
            "erro": erro,
            "setores": [d["nome"] for d in setores_ativos],
            "usuarios_copia": usuarios_copia,
            "base_template": portal_base_template(ctx.perfil),
        },
        status_code=status_code,
    )


@router.get("/chamados/novo")
async def novo_chamado_form(
    request: Request,
    ctx: PortalCtx = Depends(portal_context),
    repo: ChamadosRepo = Depends(get_chamados_repo),
):
    # Avaliação pendente bloqueia a abertura de um novo chamado (2026-07-21):
    # quem tem um atendimento RESOLVIDO ainda sem nota (1-5 ★) é mandado pra lá
    # primeiro, em vez do formulário de abertura.
    pendente = await repo.avaliacao_pendente(ctx.user.claims)
    if pendente is not None:
        return RedirectResponse(
            f"/portal/chamados/{pendente['id']}?avaliar_pendente=1",
            status_code=status.HTTP_303_SEE_OTHER,
        )
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
    sem_prazo: str = Form(""),         # Marketing: demanda sem urgência/prazo (0040)
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
    sem_prazo_marcado = sem_prazo.strip().lower() in {"on", "1", "true"}
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
        "sem_prazo": sem_prazo_marcado,
    }

    async def _erro(msg: str, code: int = status.HTTP_400_BAD_REQUEST):
        return await _render_form(request, ctx, repo, erro=msg, form=form, status_code=code)

    # Departamento, categoria e assunto/descrição são obrigatórios.
    if not departamento_id:
        return await _erro("Selecione o departamento de destino do chamado.")
    if not setor:
        return await _erro("Informe o setor para o qual a demanda está sendo pedida.")
    setores_ativos = await repo.departamentos_ativos(ctx.user.claims)
    if setor not in {d["nome"] for d in setores_ativos}:
        return await _erro("Setor selecionado inválido.")

    # Marketing trabalha por DEMANDA: em vez de prioridade, exige uma DATA DE
    # ENTREGA com no mínimo 48h (2 dias). Para os demais setores, mantém a
    # prioridade. Regra centralizada em PortalService — antes duplicada aqui
    # e em `_render_form` (mesma fórmula de "achar o Marketing pelo nome").
    marketing = PortalService.regras_marketing(
        departamento_id=departamento_id,
        setores_ativos=setores_ativos,
        prioridade=prioridade,
        sem_prazo_marcado=sem_prazo_marcado,
        data_entrega=data_entrega,
    )
    if marketing.erro:
        return await _erro(marketing.erro)
    prioridade = marketing.prioridade
    data_entrega_val = marketing.data_entrega
    sem_prazo_val = marketing.sem_prazo
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
    # A abertura SEMPRE entra como "Solicitação". A classificação Solicitação/Marketing
    # é decisão do operador/admin na tela de atendimento — nunca de quem abre o chamado
    # (forçado no servidor, mesmo que alguém envie o campo manualmente).
    origem_demanda_val = "Solicitação"

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
        sem_prazo=sem_prazo_val,
    )

    # "Em cópia" (Fase 8): observadores escolhidos já na abertura — multi-setorial,
    # qualquer pessoa da organização (não só do departamento de destino).
    for observador_id in {v.strip() for v in form_data.getlist("observadores") if v.strip()}:
        await repo.adicionar_observador(ctx.user.claims, str(novo["id"]), observador_id)

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
    avaliar_pendente: str = "",
    ctx: PortalCtx = Depends(portal_context),
    repo: ChamadosRepo = Depends(get_chamados_repo),
):
    chamado = await repo.obter(ctx.user.claims, chamado_id)
    if chamado is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Chamado não encontrado.")
    mensagens = await repo.mensagens(ctx.user.claims, chamado_id)
    await _assinar_anexos(request, mensagens)
    settings = get_settings()
    # "Em cópia" (Fase 8): lista de observadores + seletor pra adicionar mais um
    # (RLS já restringe: só quem enxerga o chamado consegue ver/editar a lista).
    observadores = await repo.observadores(ctx.user.claims, chamado_id)
    ja_observadores = {str(o["perfil_id"]) for o in observadores}
    usuarios_copia = [
        u for u in await repo.usuarios_para_copia(ctx.user.claims, excluir_id=ctx.user.id)
        if str(u["id"]) not in ja_observadores
    ]
    return render(
        request,
        "portal/chamado_detalhe.html",
        {
            "perfil": ctx.perfil,
            "chamado": chamado,
            "mensagens": mensagens,
            "pode_avaliar": PortalService.pode_avaliar(chamado, ctx.user.id),
            "avaliar_pendente": bool(avaliar_pendente),
            "observadores": observadores,
            "usuarios_copia": usuarios_copia,
            # Config do Realtime no browser (Seção 6.1): anon key + JWT do usuário.
            "supabase_url": settings.supabase_url or None,
            "anon_key": settings.supabase_anon_key or None,
            "access_token": _access_token(request),
            "base_template": portal_base_template(ctx.perfil),
        },
    )


@router.post("/chamados/{chamado_id}/observadores")
async def adicionar_observador(
    request: Request,
    chamado_id: str,
    perfil_id: str = Form(""),
    ctx: PortalCtx = Depends(portal_context),
    repo: ChamadosRepo = Depends(get_chamados_repo),
    _: None = Depends(_csrf_guard),
):
    perfil_id = perfil_id.strip()
    if perfil_id:
        await repo.adicionar_observador(ctx.user.claims, chamado_id, perfil_id)
    return RedirectResponse(
        f"/portal/chamados/{chamado_id}", status_code=status.HTTP_303_SEE_OTHER
    )


@router.post("/chamados/{chamado_id}/observadores/{perfil_id}/remover")
async def remover_observador(
    request: Request,
    chamado_id: str,
    perfil_id: str,
    ctx: PortalCtx = Depends(portal_context),
    repo: ChamadosRepo = Depends(get_chamados_repo),
    _: None = Depends(_csrf_guard),
):
    await repo.remover_observador(ctx.user.claims, chamado_id, perfil_id)
    return RedirectResponse(
        f"/portal/chamados/{chamado_id}", status_code=status.HTTP_303_SEE_OTHER
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
    # ETag/304 (Seção 2.2): se nada mudou desde o último poll, responde 304 sem
    # buscar as mensagens nem regenerar as signed URLs dos anexos — a causa do
    # chat "piscar" a cada 10s mesmo sem mensagem nova (URL assinada muda a cada
    # render, forçando o navegador a recarregar as imagens já exibidas).
    n, mx = await repo.mensagens_assinatura(ctx.user.claims, chamado_id)
    etag = f'W/"{sha256(f"{chamado_id}:{n}:{mx}".encode()).hexdigest()[:16]}"'
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304)
    mensagens = await repo.mensagens(ctx.user.claims, chamado_id)
    await _assinar_anexos(request, mensagens)
    resp = render(request, "portal/_mensagens.html", {"chamado": chamado, "mensagens": mensagens})
    resp.headers["ETag"] = etag
    resp.headers["Cache-Control"] = "no-cache"
    return resp


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
                {"pode_avaliar": PortalService.pode_avaliar(chamado, ctx.user.id), "erro": str(exc)},
                status.HTTP_422_UNPROCESSABLE_ENTITY,
            )
        return redir

    if not PortalService.pode_avaliar(chamado, ctx.user.id):
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
        return fragmento({"pode_avaliar": PortalService.pode_avaliar(chamado, ctx.user.id)})
    return redir


def register_portal_routes(app) -> None:
    app.include_router(router)
