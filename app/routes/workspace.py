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

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status, BackgroundTasks
from fastapi.responses import RedirectResponse, Response

from app.anexos import assinar_anexos, processar_uploads
from app.auth.dependencies import CurrentUser, require_role
from app.config import get_settings
from app.db import rls_request_scope
from app.domain.sla_visual import estado_sla
from app.repositories.chamados import PRIORIDADES, ChamadosRepo, get_chamados_repo
from app.security.csrf import get_csrf
from app.security.uploads import UploadInvalido
from app.templating import render

router = APIRouter(prefix="/workspace", tags=["workspace"])

STATUS_VALIDOS = ("NOVO", "A_FAZER", "EM_ATENDIMENTO", "AGUARDANDO", "RESOLVIDO")


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
            c.get("created_at"), c.get("limite_resolucao"), c.get("resolvido_em")
        ).estado == alvo
    ]


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
    etag = 'W/"%s"' % sha256(f"{_filtros_qs(f)}:{n}:{mx}".encode()).hexdigest()[:16]
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
    ctx: StaffCtx = Depends(staff_context),
    repo: ChamadosRepo = Depends(get_chamados_repo),
):
    is_marketing = ctx.perfil.get("departamento") == "Marketing"
    if is_marketing:
        status_list = ("NOVO", "A_FAZER", "EM_ATENDIMENTO", "AGUARDANDO", "RESOLVIDO")
    else:
        status_list = ("NOVO", "EM_ATENDIMENTO", "AGUARDANDO", "RESOLVIDO")

    dep_id = _dep_id(ctx)
    chamados = await repo.fila(ctx.user.claims, departamento_id=dep_id)
    colunas = {s: [c for c in chamados if c["status"] == s] for s in status_list}
    if is_marketing:
        # Coluna "Concluídos" foge da ordenação padrão da fila (por data de
        # entrega): aqui o que importa é destacar quem terminou por último, não
        # o prazo (já cumprido). Mais recente concluído primeiro.
        colunas["RESOLVIDO"].sort(key=lambda c: c["resolvido_em"] or c["created_at"], reverse=True)
    stats = await repo.fila_stats(ctx.user.claims, departamento_id=dep_id)
    return render(
        request,
        "workspace/kanban.html",
        {
            "perfil": ctx.perfil,
            "colunas": colunas,
            "stats": stats,
            "status_validos": status_list,
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
    eh_autor = str(chamado.get("cliente_id") or "") == str(ctx.user.id)
    # Marketing é a exceção da segregação de função (0029): o setor não é
    # suporte (alguém de fora abre, o setor atende) — é o próprio time que cria
    # e gerencia as demandas, quadro estilo Trello. Lá o autor PODE ser o
    # responsável pela própria demanda; nos demais setores, nunca.
    eh_marketing = str(chamado.get("departamento") or "") == "Marketing"
    # Responsáveis atribuíveis = staff do departamento do chamado (mesmo setor),
    # exceto o próprio autor (autor nunca é o responsável pelo próprio chamado) —
    # exceto no Marketing, onde o autor entra na lista normalmente.
    operadores = await repo.operadores(
        ctx.user.claims,
        departamento_id=str(chamado.get("departamento_id") or "") or None,
        excluir_id=None if eh_marketing else (str(chamado.get("cliente_id") or "") or None),
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
    dept_bate = str(chamado.get("departamento_id") or "") == str(
        ctx.perfil.get("departamento_id") or ""
    ) and bool(ctx.perfil.get("departamento_id"))
    ja_assumido = chamado.get("operador_id") is not None
    bloqueado_por_autoria = eh_autor and not eh_marketing
    pode_reivindicar = dept_bate and not bloqueado_por_autoria and not ja_assumido
    pode_atender = dept_bate and not bloqueado_por_autoria and ja_assumido
    # Repasse de departamento é exclusivo do TI (RLS reforça); só então buscamos a lista.
    # Só entram setores que RECEBEM chamado (têm fila) — repassar para um setor sem
    # staff de atendimento não faz sentido (0027).
    departamentos = (
        await repo.departamentos_destino_ativos(ctx.user.claims) if ctx.perfil.get("is_ti") else []
    )
    settings = get_settings()
    ctx_render = {
        "perfil": ctx.perfil,
        "chamado": chamado,
        "mensagens": mensagens,
        "operadores": operadores,
        "departamentos": departamentos,
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
    if novo_status in STATUS_VALIDOS:
        await repo.alterar_status(ctx.user.claims, chamado_id, novo_status)
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
