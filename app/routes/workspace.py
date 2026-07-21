"""Workspace do Operador (Fase 4) — fila por departamento, atendimento e ações.

Área do **staff** (role OPERADOR/ADMIN). O que cada um vê é imposto pela RLS
(Seção 3.3 / migration 0010): TI vê todos os chamados; RH/Marketing veem os do
seu setor. Ações (status/prioridade/atribuição) geram `historico_chamados`; a
resposta pode ser **nota interna** (decidido no servidor). Chat em tempo real
reutiliza o mesmo fragmento/Realtime do portal (Seção 6.1).
"""

from __future__ import annotations

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
from fastapi.responses import JSONResponse, RedirectResponse, Response

from app.anexos import assinar_anexos, processar_uploads
from app.auth.dependencies import CurrentUser, require_role
from app.config import get_settings
from app.db import rls_request_scope
from app.domain.sla_visual import estado_sla
from app.repositories.chamados import PRIORIDADES, ChamadosRepo, get_chamados_repo
from app.security.csrf import get_csrf
from app.security.uploads import UploadInvalido
from app.services.atendimento import AtendimentoService
from app.templating import render

router = APIRouter(prefix="/workspace", tags=["workspace"])

STATUS_VALIDOS = ("NOVO", "A_FAZER", "EM_ATENDIMENTO", "AGUARDANDO_TERCEIROS", "AGUARDANDO", "RESOLVIDO")


@dataclass(frozen=True)
class StaffCtx:
    user: CurrentUser
    perfil: dict


async def staff_context(
    user: CurrentUser = Depends(require_role("OPERADOR", "ADMIN")),
    repo: ChamadosRepo = Depends(get_chamados_repo),
):
    # Dependência yield: uma conexão RLS por request, reusada por todas as queries.
    async with rls_request_scope(user.claims):
        perfil = await repo.perfil(user.claims)
        if not perfil:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="Perfil não encontrado."
            )
        yield StaffCtx(user=user, perfil=perfil)


async def _csrf_guard(request: Request) -> None:
    await get_csrf().validate(request)


def _access_token(request: Request) -> str | None:
    from app.auth.session import current_access_token

    return current_access_token(request)


# Filtros de SLA da fila → estado calculado por app.domain.sla_visual.estado_sla.
_SLA_FILTROS = {"atrasado": "danger", "risco": "warn", "no_prazo": "ok"}


def _parse_filtros(status: str, categoria: str, prioridade: str, operador: str, sla: str) -> dict:
    """Normaliza os filtros da fila (status/categoria/prioridade/operador/SLA)."""
    prio = (prioridade or "").strip().upper()
    sla_v = (sla or "").strip()
    return {
        "status": status if status in STATUS_VALIDOS else None,
        "categoria_id": (categoria or "").strip() or None,
        "prioridade": prio if prio in PRIORIDADES else None,
        "operador_id": (operador or "").strip() or None,
        "sla": sla_v if sla_v in _SLA_FILTROS else "",
    }


def _filtros_qs(f: dict) -> str:
    """Querystring que preserva os filtros ativos (para o polling do fragmento)."""
    from urllib.parse import urlencode

    pares = {}
    if f["status"]:
        pares["status"] = f["status"]
    if f["categoria_id"]:
        pares["categoria"] = f["categoria_id"]
    if f["prioridade"]:
        pares["prioridade"] = f["prioridade"]
    if f["operador_id"]:
        pares["operador"] = f["operador_id"]
    if f["sla"]:
        pares["sla"] = f["sla"]
    return urlencode(pares)


def _aplicar_sla(chamados: list[dict], sla: str) -> list[dict]:
    """Filtra a lista pelo estado de SLA (calculado no domínio, não no SQL)."""
    alvo = _SLA_FILTROS.get(sla)
    if alvo is None:
        return chamados
    return [
        c for c in chamados
        if estado_sla(
            c.get("created_at"), c.get("limite_resolucao"), c.get("resolvido_em"),
            status=c.get("status"),
        ).estado == alvo
    ]


def _parse_filtros_kanban(
    categoria: str, prioridade: str, operador: str, sla: str, setor: str, data_de: str, data_ate: str
) -> dict:
    """Normaliza os filtros do Kanban (categoria/prioridade/operador/SLA/setor/período).

    Sem filtro de ``status``: no Kanban o status é escolhido implicitamente pela
    coluna, não por um seletor.
    """
    from datetime import date

    prio = (prioridade or "").strip().upper()
    sla_v = (sla or "").strip()

    def _data(v: str):
        v = (v or "").strip()
        if not v:
            return None
        try:
            return date.fromisoformat(v)
        except ValueError:
            return None

    return {
        "categoria_id": (categoria or "").strip() or None,
        "prioridade": prio if prio in PRIORIDADES else None,
        "operador_id": (operador or "").strip() or None,
        "sla": sla_v if sla_v in _SLA_FILTROS else "",
        "setor": (setor or "").strip() or None,
        "data_de": _data(data_de),
        "data_ate": _data(data_ate),
        "data_de_raw": (data_de or "").strip(),
        "data_ate_raw": (data_ate or "").strip(),
    }


async def _opcoes_filtro(ctx: StaffCtx, repo: ChamadosRepo) -> tuple[list[dict], list[dict]]:
    """Categorias e operadores do setor do staff (para os selects de filtro)."""
    dep_id = str(ctx.perfil.get("departamento_id") or "") or None
    categorias = await repo.categorias_ativas(ctx.user.claims, dep_id)
    operadores = await repo.operadores(ctx.user.claims, departamento_id=dep_id)
    return categorias, operadores


def _dep_id(ctx: StaffCtx) -> str | None:
    return str(ctx.perfil.get("departamento_id") or "") or None


async def _buscar_fila(repo: ChamadosRepo, claims: dict, dep_id: str | None, f: dict) -> list[dict]:
    chamados = await repo.fila(
        claims,
        departamento_id=dep_id,
        status=f["status"],
        categoria_id=f["categoria_id"],
        prioridade=f["prioridade"],
        operador_id=f["operador_id"],
    )
    return _aplicar_sla(chamados, f["sla"])


# --------------------------------------------------------------------------
# Fila — Lista e Kanban
# --------------------------------------------------------------------------
@router.get("")
async def fila_lista(
    request: Request,
    status: str = "",
    categoria: str = "",
    prioridade: str = "",
    operador: str = "",
    sla: str = "",
    ctx: StaffCtx = Depends(staff_context),
    repo: ChamadosRepo = Depends(get_chamados_repo),
):
    # Sem fila no próprio setor (ADMIN líder de setor, ex.: Controladoria) — não
    # atende, só abre/acompanha chamados. Reforça no servidor o que a nav já
    # esconde (2026-07-21): sem isso, a URL direta ainda abriria uma fila vazia.
    # 303 literal (não `status.HTTP_303_SEE_OTHER`): o parâmetro `status` desta
    # rota (filtro de status da fila) sombreia o módulo `fastapi.status` aqui dentro.
    if not ctx.perfil.get("recebe_chamados"):
        return RedirectResponse("/portal", status_code=303)
    f = _parse_filtros(status, categoria, prioridade, operador, sla)
    dep_id = _dep_id(ctx)
    chamados = await _buscar_fila(repo, ctx.user.claims, dep_id, f)
    stats = await repo.fila_stats(ctx.user.claims, departamento_id=dep_id)
    categorias, operadores = await _opcoes_filtro(ctx, repo)

    is_marketing = ctx.perfil.get("departamento") == "Marketing"
    if is_marketing:
        status_cards = [
            ("", "Total"),
            ("NOVO", "Novos"),
            ("A_FAZER", "A fazer"),
            ("EM_ATENDIMENTO", "Em andamento"),
            ("AGUARDANDO_TERCEIROS", "Aguardando terceiros"),
            ("AGUARDANDO", "Aguardando Validação"),
            ("RESOLVIDO", "Concluídos"),
        ]
    else:
        status_cards = [
            ("", "Total"),
            ("NOVO", "Novos"),
            ("EM_ATENDIMENTO", "Em atendimento"),
            ("AGUARDANDO", "Aguardando"),
            ("RESOLVIDO", "Resolvidos"),
        ]

    return render(
        request,
        "workspace/fila.html",
        {
            "perfil": ctx.perfil,
            "chamados": chamados,
            "stats": stats,
            "filtro": f["status"],
            "categorias": categorias,
            "operadores": operadores,
            "prioridades": PRIORIDADES,
            "sla_filtros": list(_SLA_FILTROS.keys()),
            "categoria_sel": f["categoria_id"] or "",
            "prioridade_sel": f["prioridade"] or "",
            "operador_sel": f["operador_id"] or "",
            "sla_sel": f["sla"],
            "filtros_qs": _filtros_qs(f),
            "status_cards": status_cards,
        },
    )


@router.get("/fila/fragmento")
async def fila_fragmento(
    request: Request,
    status: str = "",
    categoria: str = "",
    prioridade: str = "",
    operador: str = "",
    sla: str = "",
    ctx: StaffCtx = Depends(staff_context),
    repo: ChamadosRepo = Depends(get_chamados_repo),
):
    f = _parse_filtros(status, categoria, prioridade, operador, sla)
    dep_id = _dep_id(ctx)
    # ETag/304 (Seção 2.2): consulta leve de assinatura; se nada mudou desde o
    # último poll, responde 304 sem buscar todas as linhas nem re-renderizar. Os
    # filtros entram na chave do ETag (via querystring) para não colidir entre si.
    n, mx = await repo.fila_assinatura(ctx.user.claims, departamento_id=dep_id, status=f["status"])
    etag = f'W/"{sha256(f"{_filtros_qs(f)}:{n}:{mx}".encode()).hexdigest()[:16]}"'
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304)  # 304 Not Modified
    chamados = await _buscar_fila(repo, ctx.user.claims, dep_id, f)
    resp = render(request, "workspace/_fila_linhas.html", {"chamados": chamados})
    resp.headers["ETag"] = etag
    resp.headers["Cache-Control"] = "no-cache"
    return resp


@router.get("/kanban")
async def kanban(
    request: Request,
    categoria: str = "",
    prioridade: str = "",
    operador: str = "",
    sla: str = "",
    setor: str = "",
    data_de: str = "",
    data_ate: str = "",
    ctx: StaffCtx = Depends(staff_context),
    repo: ChamadosRepo = Depends(get_chamados_repo),
):
    if not ctx.perfil.get("recebe_chamados"):
        return RedirectResponse("/portal", status_code=status.HTTP_303_SEE_OTHER)
    is_marketing = ctx.perfil.get("departamento") == "Marketing"
    if is_marketing:
        status_list = ("NOVO", "A_FAZER", "EM_ATENDIMENTO", "AGUARDANDO_TERCEIROS", "AGUARDANDO", "RESOLVIDO")
    else:
        # A_FAZER não é mais exclusivo do Marketing (migration 0047 generalizou
        # o autoatendimento pra todos os setores): sem essa coluna aqui, um
        # chamado nesse status (ex.: os 471 importados como "[Legado #...]",
        # todos A_FAZER) contava certo nos cards da fila (`fila_stats`, sem
        # filtro por `status_list`) mas sumia do quadro — `colunas` só inclui
        # os status de `status_list`, e A_FAZER ficava de fora.
        status_list = ("NOVO", "A_FAZER", "EM_ATENDIMENTO", "AGUARDANDO", "RESOLVIDO")

    f = _parse_filtros_kanban(categoria, prioridade, operador, sla, setor, data_de, data_ate)
    dep_id = _dep_id(ctx)
    chamados = await repo.fila(
        ctx.user.claims,
        departamento_id=dep_id,
        categoria_id=f["categoria_id"],
        prioridade=f["prioridade"],
        operador_id=f["operador_id"],
        setor=f["setor"],
        data_de=f["data_de"],
        data_ate=f["data_ate"],
    )
    chamados = _aplicar_sla(chamados, f["sla"])
    # dept_bate calculado uma única vez aqui (AtendimentoService) e consumido
    # pelo Kanban via c.dept_bate — antes o template recomputava a mesma
    # fórmula por conta própria (ver histórico do item 2.2, M2).
    for c in chamados:
        c["dept_bate"] = AtendimentoService.dept_bate(c, ctx.perfil)
    colunas = {s: [c for c in chamados if c["status"] == s] for s in status_list}
    if is_marketing:
        # Coluna "Concluídos" foge da ordenação padrão da fila (por data de
        # entrega): aqui o que importa é destacar quem terminou por último, não
        # o prazo (já cumprido). Mais recente concluído primeiro.
        colunas["RESOLVIDO"].sort(key=lambda c: c["resolvido_em"] or c["created_at"], reverse=True)
    stats = await repo.fila_stats(ctx.user.claims, departamento_id=dep_id)
    categorias, operadores = await _opcoes_filtro(ctx, repo)
    setores = await repo.setores_ativos(ctx.user.claims, dep_id)
    tem_filtro = any([
        f["categoria_id"], f["prioridade"], f["operador_id"], f["sla"],
        f["setor"], f["data_de_raw"], f["data_ate_raw"],
    ])
    return render(
        request,
        "workspace/kanban.html",
        {
            "perfil": ctx.perfil,
            "colunas": colunas,
            "stats": stats,
            "status_validos": status_list,
            "categorias": categorias,
            "operadores": operadores,
            "setores": setores,
            "prioridades": PRIORIDADES,
            "sla_filtros": list(_SLA_FILTROS.keys()),
            "categoria_sel": f["categoria_id"] or "",
            "prioridade_sel": f["prioridade"] or "",
            "operador_sel": f["operador_id"] or "",
            "sla_sel": f["sla"],
            "setor_sel": f["setor"] or "",
            "data_de_sel": f["data_de_raw"],
            "data_ate_sel": f["data_ate_raw"],
            "tem_filtro": tem_filtro,
        },
    )


# --------------------------------------------------------------------------
# Atendimento (tela individual)
# --------------------------------------------------------------------------
async def _carregar_atendimento(request, chamado_id, ctx, repo, *, origem: str = "", **extra):
    chamado = await repo.obter(ctx.user.claims, chamado_id)
    if chamado is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Chamado não encontrado.")
    mensagens = await repo.mensagens(ctx.user.claims, chamado_id)
    await assinar_anexos(request, mensagens)
    # "Em cópia" (Fase 8) — só leitura aqui; gerenciar (adicionar/remover) é no
    # Portal (/portal/chamados/{id}), acessível a qualquer staff que veja o chamado.
    observadores = await repo.observadores(ctx.user.claims, chamado_id)
    # Permissões de UI (dept_bate/eh_autor/autoatendimento/pode_reivindicar/
    # pode_atender) centralizadas em AtendimentoService (item 2.2, M2) — regra
    # de segregação de função (0029) + exceção de autoatendimento (0038/0042)
    # + gate de setor do líder (0028), num único lugar em vez de espalhadas
    # entre rota e template.
    perm = AtendimentoService.permissoes(chamado, ctx.perfil, ctx.user.id)
    eh_autor = perm.eh_autor
    # Responsáveis atribuíveis = staff do departamento do chamado (mesmo setor),
    # exceto o próprio autor (autor nunca é o responsável pelo próprio chamado) —
    # exceto nos departamentos com autoatendimento, onde o autor entra na lista
    # normalmente.
    operadores = await repo.operadores(
        ctx.user.claims,
        departamento_id=str(chamado.get("departamento_id") or "") or None,
        excluir_id=None if perm.eh_autoatendimento else (str(chamado.get("cliente_id") or "") or None),
    )
    # Líder de setor (0028) enxerga chamados abertos pela própria equipe mesmo
    # fora da fila do seu departamento — mas só ACOMPANHA: quem atende (muda
    # status, responde) é sempre o staff do MESMO departamento do chamado, TI
    # incluído (0020 tirou o "acesso total" de atendimento do TI; o TI só ganha
    # um chamado de outro setor via repasse, que o move pra fila da TI antes).
    #
    # Regra de segregação de função (validada 2026-07-09): o chamado fica só
    # leitura para TODO mundo — inclusive para o próprio setor de destino — até
    # alguém que NÃO seja o autor "Iniciar atendimento". A partir daí, qualquer
    # pessoa do setor (exceto o autor) pode responder/alterar. O autor NUNCA
    # responde/assume como staff o próprio chamado, mesmo sendo do mesmo setor —
    # ele acompanha e responde como solicitante em "Meus chamados" (Fase 1).
    pode_reivindicar = perm.pode_reivindicar
    pode_atender = perm.pode_atender
    # Repasse de departamento é exclusivo do TI (RLS reforça); só então buscamos a lista.
    # Só entram setores que RECEBEM chamado (têm fila) — repassar para um setor sem
    # staff de atendimento não faz sentido (0027).
    departamentos = (
        await repo.departamentos_destino_ativos(ctx.user.claims) if ctx.perfil.get("is_ti") else []
    )
    # Categoria/subcategoria editáveis nas Ações (2026-07-21): mesmo catálogo
    # cacheado usado na abertura do chamado (CatalogoRepo), escopado ao
    # departamento do chamado — trocar de setor via repasse já muda o catálogo
    # disponível aqui na próxima carga da página.
    categorias_edit = await repo.categorias_ativas(
        ctx.user.claims, str(chamado.get("departamento_id") or "") or None
    )
    subcategorias_edit = (
        await repo.subcategorias_ativas(ctx.user.claims, str(chamado["categoria_id"]))
        if chamado.get("categoria_id") else []
    )
    settings = get_settings()
    ctx_render = {
        "perfil": ctx.perfil,
        "chamado": chamado,
        "mensagens": mensagens,
        "operadores": operadores,
        "departamentos": departamentos,
        "categorias_edit": categorias_edit,
        "subcategorias_edit": subcategorias_edit,
        "observadores": observadores,
        "eh_autor": eh_autor,
        "pode_reivindicar": pode_reivindicar,
        "pode_atender": pode_atender,
        "prioridades": PRIORIDADES,
        "status_validos": STATUS_VALIDOS,
        "supabase_url": settings.supabase_url or None,
        "anon_key": settings.supabase_anon_key or None,
        "access_token": _access_token(request),
        "origem": origem,
    }
    ctx_render.update(extra)
    return render(request, "workspace/atendimento.html", ctx_render)


@router.get("/chamados/{chamado_id}")
async def atendimento(
    request: Request,
    chamado_id: str,
    origem: str = "",
    excluir: str = "",
    ctx: StaffCtx = Depends(staff_context),
    repo: ChamadosRepo = Depends(get_chamados_repo),
):
    return await _carregar_atendimento(
        request, chamado_id, ctx, repo, origem=origem, confirmar_exclusao=bool(excluir)
    )


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
    await assinar_anexos(request, mensagens)
    return render(request, "portal/_mensagens.html", {"chamado": chamado, "mensagens": mensagens})


def _voltar(chamado_id: str, origem: str = "") -> RedirectResponse:
    qs = f"?origem={origem}" if origem else ""
    return RedirectResponse(
        f"/workspace/chamados/{chamado_id}{qs}", status_code=status.HTTP_303_SEE_OTHER
    )


# --------------------------------------------------------------------------
# Ações rápidas (cada uma registra em historico_chamados)
# --------------------------------------------------------------------------
@router.post("/chamados/{chamado_id}/status")
async def mudar_status(
    request: Request,
    chamado_id: str,
    novo_status: str = Form(...),
    origem: str = "",
    ctx: StaffCtx = Depends(staff_context),
    repo: ChamadosRepo = Depends(get_chamados_repo),
    _: None = Depends(_csrf_guard),
):
    """Troca de status (form da tela de detalhe e drag do Kanban).

    Saindo de ``NOVO``/``A_FAZER`` pra QUALQUER outro status, isso é o "iniciar
    atendimento" — atribui o operador (com a mesma segregação de
    função/exceção do Marketing) em vez de só mudar o rótulo da coluna; sem essa
    atribuição o chamado "andava" no Kanban mas ninguém ficava responsável por
    ele (bug real: arrastar direto de "A Fazer" pra "Aguardando", pulando "Em
    andamento", deixava passar — BOND-2026-00035/00038). Se o chamado já tinha
    operador (só mudando de coluna, ex.: devolvido de "Aguardando"),
    ``iniciar_atendimento`` é um no-op e cai no fallback de troca simples.
    """
    resultado: dict | None = None
    if novo_status in STATUS_VALIDOS:
        if novo_status not in ("NOVO", "A_FAZER"):
            resultado = await repo.iniciar_atendimento(
                ctx.user.claims, chamado_id, operador_id=ctx.user.id, novo_status=novo_status
            )
        if resultado is None:
            resultado = await repo.alterar_status(ctx.user.claims, chamado_id, novo_status)

    # O drag do Kanban chama via fetch (header X-Kanban-Drag) e precisa saber se
    # a mudança realmente aconteceu para poder desfazer o arraste na tela; o
    # form clássico da tela de detalhe continua recebendo o redirect de sempre.
    if request.headers.get("X-Kanban-Drag"):
        return JSONResponse({"ok": resultado is not None})
    return _voltar(chamado_id, origem)


@router.post("/chamados/{chamado_id}/marketing-meta")
async def salvar_marketing_meta(
    request: Request,
    chamado_id: str,
    volume: int = Form(...),
    origem_demanda: str = Form(...),
    causa_atraso: str = Form(""),
    origem: str = "",
    ctx: StaffCtx = Depends(staff_context),
    repo: ChamadosRepo = Depends(get_chamados_repo),
    _: None = Depends(_csrf_guard),
):
    await repo.salvar_marketing_meta(
        ctx.user.claims,
        chamado_id,
        volume=volume,
        origem_demanda=origem_demanda,
        causa_atraso=causa_atraso.strip() or None
    )
    return _voltar(chamado_id, origem)


@router.post("/chamados/{chamado_id}/prioridade")
async def mudar_prioridade(
    request: Request,
    chamado_id: str,
    nova_prioridade: str = Form(...),
    origem: str = "",
    ctx: StaffCtx = Depends(staff_context),
    repo: ChamadosRepo = Depends(get_chamados_repo),
    _: None = Depends(_csrf_guard),
):
    if nova_prioridade in PRIORIDADES:
        await repo.alterar_prioridade(ctx.user.claims, chamado_id, nova_prioridade)
    return _voltar(chamado_id, origem)


@router.get("/chamados/{chamado_id}/subcategorias")
async def subcategorias_edit_fragmento(
    request: Request,
    chamado_id: str,
    categoria_id: str = "",
    ctx: StaffCtx = Depends(staff_context),
    repo: ChamadosRepo = Depends(get_chamados_repo),
):
    """Cascade da edição de Categoria/Subcategoria nas Ações (recarrega ao
    trocar a categoria). ``chamado_id`` não entra na consulta — mantido na URL
    só por consistência com o resto das rotas de ação do chamado."""
    categoria_id = categoria_id.strip()
    subs = await repo.subcategorias_ativas(ctx.user.claims, categoria_id) if categoria_id else []
    return render(
        request, "workspace/_subcategoria_edit_options.html",
        {"subcategorias": subs, "subcategoria_sel": ""},
    )


@router.post("/chamados/{chamado_id}/categoria")
async def alterar_categoria(
    request: Request,
    chamado_id: str,
    categoria_id: str = Form(""),
    subcategoria_id: str = Form(""),
    origem: str = "",
    ctx: StaffCtx = Depends(staff_context),
    repo: ChamadosRepo = Depends(get_chamados_repo),
    _: None = Depends(_csrf_guard),
):
    """Corrige a categorização de um chamado já aberto (staff no escopo) —
    defesa em profundidade: revalida o par categoria/departamento e categoria/
    subcategoria no servidor, mesma regra da abertura (não confia só no que o
    select mandou)."""
    categoria_id = categoria_id.strip()
    subcategoria_id = subcategoria_id.strip()
    chamado = await repo.obter(ctx.user.claims, chamado_id)
    if chamado is not None:
        dep_id = str(chamado.get("departamento_id") or "")
        categoria_ok = bool(categoria_id) and await repo.categoria_valida(
            ctx.user.claims, categoria_id=categoria_id, departamento_id=dep_id
        )
        if not categoria_ok:
            # Categoria inválida/ausente: a subcategoria fica órfã sem ela —
            # limpa as duas (não dá pra revalidar subcategoria contra nada).
            categoria_id = ""
            subcategoria_id = ""
        elif subcategoria_id and not await repo.subcategoria_valida(
            ctx.user.claims, categoria_id=categoria_id, subcategoria_id=subcategoria_id
        ):
            subcategoria_id = ""
        await repo.alterar_categoria(
            ctx.user.claims, chamado_id,
            categoria_id=categoria_id or None, subcategoria_id=subcategoria_id or None,
        )
    return _voltar(chamado_id, origem)


@router.post("/chamados/{chamado_id}/atribuir")
async def atribuir(
    request: Request,
    chamado_id: str,
    operador_id: str = Form(""),
    origem: str = "",
    ctx: StaffCtx = Depends(staff_context),
    repo: ChamadosRepo = Depends(get_chamados_repo),
    _: None = Depends(_csrf_guard),
):
    await repo.atribuir(ctx.user.claims, chamado_id, operador_id.strip() or None)
    return _voltar(chamado_id, origem)


@router.post("/chamados/{chamado_id}/iniciar")
async def iniciar_atendimento(
    request: Request,
    chamado_id: str,
    origem: str = "",
    ctx: StaffCtx = Depends(staff_context),
    repo: ChamadosRepo = Depends(get_chamados_repo),
    _: None = Depends(_csrf_guard),
):
    """Botão "Iniciar atendimento": NOVO→EM_ATENDIMENTO e assume o chamado."""
    await repo.iniciar_atendimento(ctx.user.claims, chamado_id, operador_id=ctx.user.id)
    return _voltar(chamado_id, origem)


@router.post("/chamados/{chamado_id}/transferir")
async def transferir(
    request: Request,
    chamado_id: str,
    departamento_id: str = Form(""),
    origem: str = "",
    ctx: StaffCtx = Depends(staff_context),
    repo: ChamadosRepo = Depends(get_chamados_repo),
    _: None = Depends(_csrf_guard),
):
    """Repassa o chamado para outro departamento. Só o TI consegue (a RLS bloqueia
    os demais); o gate de UI evita mostrar a opção para quem não é TI."""
    departamento_id = departamento_id.strip()
    if departamento_id and ctx.perfil.get("is_ti"):
        await repo.transferir(ctx.user.claims, chamado_id, departamento_id=departamento_id)
    return _voltar(chamado_id, origem)


@router.post("/chamados/{chamado_id}/mensagens")
async def responder(
    request: Request,
    chamado_id: str,
    background_tasks: BackgroundTasks,
    conteudo: str = Form(""),
    is_interna: str = Form(""),
    arquivos: list[UploadFile] = File(default=[]),
    origem: str = "",
    ctx: StaffCtx = Depends(staff_context),
    repo: ChamadosRepo = Depends(get_chamados_repo),
    _: None = Depends(_csrf_guard),
):
    conteudo = conteudo.strip()
    interna = is_interna in ("1", "true", "on", "True")

    # Anexos validados server-side (10MB, allow-list, magic bytes) e enviados ao
    # Storage privado antes de gravar a mensagem (Seção 3.9). Falha → re-render.
    try:
        anexos = await processar_uploads(
            request,
            arquivos,
            empresa_id=str(ctx.perfil["empresa_id"]),
            chamado_id=chamado_id,
            max_bytes=get_settings().anexo_max_bytes,
        )
    except UploadInvalido as exc:
        return await _carregar_atendimento(
            request, chamado_id, ctx, repo, erro_composer=str(exc), origem=origem
        )

    if conteudo or anexos:
        await repo.responder_staff(
            ctx.user.claims, chamado_id, conteudo=conteudo, is_interna=interna, anexos=anexos
        )
        if not interna:
            chamado = await repo.obter(ctx.user.claims, chamado_id)
            if chamado:
                from app.notification import agendar_notificacao_email
                await agendar_notificacao_email(
                    background_tasks, chamado, ctx.user.id, conteudo or "[Arquivo anexo]"
                )
    return _voltar(chamado_id, origem)


@router.post("/chamados/{chamado_id}/encerrar")
async def encerrar(
    request: Request,
    chamado_id: str,
    background_tasks: BackgroundTasks,
    resolucao: str = Form(""),
    origem: str = "",
    ctx: StaffCtx = Depends(staff_context),
    repo: ChamadosRepo = Depends(get_chamados_repo),
    _: None = Depends(_csrf_guard),
):
    """Encerra o chamado (→ RESOLVIDO). A **nota de solução** (opcional) vira uma
    mensagem **pública** — visível ao solicitante, que então pode avaliar. Ação
    de staff no escopo (RLS); as duas escritas rodam na mesma transação do request."""
    resolucao = resolucao.strip()
    if resolucao:
        await repo.responder_staff(
            ctx.user.claims, chamado_id, conteudo=resolucao, is_interna=False
        )
        chamado = await repo.obter(ctx.user.claims, chamado_id)
        if chamado:
            from app.notification import agendar_notificacao_email
            await agendar_notificacao_email(
                background_tasks, chamado, ctx.user.id, resolucao
            )
    await repo.alterar_status(ctx.user.claims, chamado_id, "RESOLVIDO")
    return _voltar(chamado_id, origem)


@router.post("/chamados/{chamado_id}/excluir")
async def excluir(
    request: Request,
    chamado_id: str,
    origem: str = "",
    ctx: StaffCtx = Depends(staff_context),
    repo: ChamadosRepo = Depends(get_chamados_repo),
    _: None = Depends(_csrf_guard),
):
    """Exclui um chamado aberto por engano. Escopo restrito pela RLS
    (`chamados_delete_staff`): TI apaga qualquer um; RH/Marketing só do
    próprio setor. Sem confirmação adicional aqui — a UI já exige duas
    etapas antes de chamar esta rota."""
    await repo.excluir(ctx.user.claims, chamado_id)
    destino = "/workspace/kanban" if origem == "kanban" else "/workspace"
    return RedirectResponse(destino, status_code=status.HTTP_303_SEE_OTHER)


def register_workspace_routes(app) -> None:
    app.include_router(router)
