"""Painel Admin & Relatórios (Fases 5 e 4) — indicadores por papel.

KPIs (TMA, conformidade de SLA, CSAT, produtividade), gestão de catálogos
(departamentos/categorias/planos) e export CSV.

**Acesso (decisão de produto 2026-07-09 — indicadores só do próprio setor):**
- **TI** (`auth_is_ti()` + role OPERADOR/ADMIN): vê os indicadores **apenas do
  próprio setor (TI)**, igual a qualquer outro departamento — não tem mais
  acesso a "Todos os setores" nem aos números de RH/Marketing. Continua sendo
  o único que **gere** catálogos/usuários/planos (isso é administração do
  sistema, não "indicador de outro setor").
- **ADMIN de departamento** (role ``ADMIN`` com ``departamento_id``, ex.: um
  gestor do RH): vê os indicadores **apenas do seu setor** (CSAT, SLA, rapidez,
  avaliações) — a RLS já escopa as queries ao departamento. **Não** gere
  catálogos (isso é do TI).

Gráficos com Chart.js (self-hosted, SRI); dados passados como JSON inerte
(`<script type="application/json">`) e lidos por `admin.js` (CSP-safe).
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from datetime import datetime

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import RedirectResponse, Response

from app import cache
from app.auth.dependencies import CurrentUser, get_current_user
from app.db import rls_request_scope
from app.repositories.admin import AdminRepo, get_admin_repo
from app.repositories.chamados import (
    CACHE_CATEGORIAS,
    CACHE_DEPARTAMENTOS,
    CACHE_SUBCATEGORIAS,
    ChamadosRepo,
    get_chamados_repo,
)
from app.security.csrf import get_csrf
from app.services.admin import AdminService
from app.templating import render

router = APIRouter(prefix="/admin", tags=["admin"])


@dataclass(frozen=True)
class AdminCtx:
    user: CurrentUser
    perfil: dict
    is_ti: bool
    escopo: str  # rótulo do escopo dos indicadores (setor ou "Todos os setores")


async def admin_context(
    user: CurrentUser = Depends(get_current_user),
    repo: ChamadosRepo = Depends(get_chamados_repo),
):
    """Autoriza o acesso ao painel e resolve o **escopo** dos indicadores.

    Entra o **TI** (acesso total) e o **ADMIN de departamento** (gestor do
    próprio setor). OPERADOR/CLIENTE recebem 403. Um ADMIN sem setor não tem
    escopo de relatório → 403 (o único "admin global" é o TI).

    Dependência ``yield``: uma conexão RLS por request, reusada pelas queries
    (perfil, KPIs, catálogos, export) — performance (Seção 2.1)."""
    async with rls_request_scope(user.claims):
        perfil = await repo.perfil(user.claims)
        # is_ti aqui exige TAMBÉM ser staff (OPERADOR/ADMIN) — um CLIENTE do setor
        # TI (funcionário comum) não deve entrar no painel admin só por pertencer
        # ao departamento (perfil.is_ti é só "departamento = TI", sem olhar papel).
        is_ti = bool(
            perfil and perfil.get("is_ti") and perfil.get("role") in ("OPERADOR", "ADMIN")
        )
        is_admin_dep = bool(
            perfil and perfil.get("role") == "ADMIN" and perfil.get("departamento")
        )
        if not (is_ti or is_admin_dep):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Área restrita a gestores (Admin do setor) e ao TI.",
            )
        # Indicadores só do PRÓPRIO setor (decisão 2026-07-09) — TI passa a ser
        # escopado igual a qualquer Admin de departamento; "Todos os setores"
        # deixou de existir. TI segue sendo o único que GERE catálogos (_require_ti).
        escopo = perfil.get("departamento") or "—"
        yield AdminCtx(user=user, perfil=perfil, is_ti=is_ti, escopo=escopo)


def _require_ti(ctx: AdminCtx) -> None:
    """Gestão de catálogos é exclusiva do TI (o Admin de setor só vê relatórios)."""
    if not ctx.is_ti:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Gestão de catálogos é restrita ao TI.",
        )


async def _csrf_guard(request: Request) -> None:
    await get_csrf().validate(request)


def _base_ctx(ctx: AdminCtx) -> dict:
    """Contexto comum aos templates do admin (perfil/escopo para o shell)."""
    return {"perfil": ctx.perfil, "is_ti": ctx.is_ti, "escopo": ctx.escopo}


@router.get("")
async def dashboard(
    request: Request,
    ctx: AdminCtx = Depends(admin_context),
    repo: AdminRepo = Depends(get_admin_repo),
):
    claims = ctx.user.claims

    # INDICADORES escopados ao PRÓPRIO setor sempre (2026-07-09) — TI incluído.
    # Nada de seletor "Todos os setores"/outro departamento: a RLS (rls_connection,
    # nunca admin_connection) já limita as queries ao departamento do usuário.
    dep_id = str(ctx.perfil.get("departamento_id") or "") or None
    escopo = ctx.escopo

    kpis = await repo.kpis(claims, departamento_id=dep_id, todos_setores=False)
    graficos = {
        "por_status": await repo.por_status(claims, departamento_id=dep_id, todos_setores=False),
        "csat": await repo.csat_distribuicao(claims, departamento_id=dep_id, todos_setores=False),
        "por_departamento": await repo.por_departamento(claims, departamento_id=dep_id, todos_setores=False),
        "por_setor": await repo.por_setor(claims, departamento_id=dep_id, todos_setores=False),
        "produtividade": await repo.produtividade(claims, departamento_id=dep_id, todos_setores=False),
    }
    avaliacoes = await repo.avaliacoes_recentes(claims, departamento_id=dep_id, todos_setores=False)

    if escopo == "Marketing":
        mkt_data = await repo.mkt_dashboard_data(claims)
        return render(
            request,
            "admin/dashboard_marketing.html",
            {
                **_base_ctx(ctx),
                "escopo": escopo,
                "departamentos": [],
                "departamento_sel": "",
                "mkt_data": mkt_data,
            },
        )

    return render(
        request,
        "admin/dashboard.html",
        {
            **_base_ctx(ctx),
            "escopo": escopo,
            "kpis": kpis,
            "graficos": graficos,
            "avaliacoes": avaliacoes,
            "departamentos": [],
            "departamento_sel": "",
        },
    )


@router.get("/gestao")
async def gestao(
    request: Request,
    prioridade_ok: str = "",
    feriados_ok: str = "",
    ctx: AdminCtx = Depends(admin_context),
    repo: AdminRepo = Depends(get_admin_repo),
):
    _require_ti(ctx)
    categorias = await repo.categorias(ctx.user.claims)
    return render(
        request,
        "admin/gestao.html",
        {
            **_base_ctx(ctx),
            "departamentos": await repo.departamentos(ctx.user.claims),
            "categorias": categorias,
            "categorias_ativas": [c for c in categorias if c.get("ativo")],
            "subcategorias": await repo.subcategorias(ctx.user.claims),
            "planos": await repo.planos(ctx.user.claims),
            "prioridade_ok": prioridade_ok,
            "feriados_ok": feriados_ok,
            "midia_regional": await repo.marketing_midia_regional(ctx.user.claims),
        },
    )


@router.post("/marketing-midia")
async def salvar_marketing_midia(
    request: Request,
    mes: str = Form(...),               # "YYYY-MM" (input type="month")
    investimento: str = Form("0"),
    regioes: str = Form("0"),
    descontinuidades: str = Form("0"),
    aderencias: str = Form("0"),
    ctx: AdminCtx = Depends(admin_context),
    repo: AdminRepo = Depends(get_admin_repo),
    _: None = Depends(_csrf_guard),
):
    """Cria/atualiza um mês de Mídia Regional do Marketing (Fase 6 — 2026-07-09):
    qualquer mês agora entra por aqui, sem precisar de migration — é o que torna
    `marketing_midia_regional` dinâmica em vez de engessada nos 5 meses do seed
    original."""
    _require_ti(ctx)
    from datetime import date as _date

    mes = mes.strip()
    try:
        ano_s, mes_s = mes.split("-", 1)
        mes_data = _date(int(ano_s), int(mes_s), 1)
    except (ValueError, TypeError):
        return RedirectResponse("/admin/gestao", status_code=status.HTTP_303_SEE_OTHER)

    def _num(bruto: str, cast):
        try:
            return cast((bruto or "0").strip().replace(",", "."))
        except (ValueError, TypeError):
            return cast(0)

    await repo.upsert_marketing_midia_regional(
        ctx.user.claims,
        mes=mes_data,
        investimento=_num(investimento, float),
        regioes=_num(regioes, int),
        descontinuidades=_num(descontinuidades, int),
        aderencias=_num(aderencias, int),
    )
    return RedirectResponse("/admin/gestao", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/jobs/sincronizar-feriados")
async def job_sincronizar_feriados(
    request: Request,
    ctx: AdminCtx = Depends(admin_context),
    repo: AdminRepo = Depends(get_admin_repo),
    _: None = Depends(_csrf_guard),
):
    """Sincroniza `feriados` com o calendário nacional calculado pela biblioteca
    `holidays` (Fase 5 — 2026-07-09), no lugar de editar a tabela à mão a cada
    virada de ano. `ON CONFLICT DO NOTHING`: nunca apaga/sobrescreve feriados
    locais já cadastrados manualmente. Só TI (mesmo escopo de `feriados_admin`
    na RLS)."""
    _require_ti(ctx)
    from datetime import datetime

    from app.domain.feriados import feriados_nacionais, proximos_anos

    anos = proximos_anos(datetime.now().year)
    novos = await repo.sincronizar_feriados(ctx.user.claims, feriados_nacionais(anos))
    return RedirectResponse(f"/admin/gestao?feriados_ok={novos}", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/jobs/recalcular-prioridade-marketing")
async def job_recalcular_prioridade_marketing(
    request: Request,
    ctx: AdminCtx = Depends(admin_context),
    repo: AdminRepo = Depends(get_admin_repo),
    _: None = Depends(_csrf_guard),
):
    """Recalcula a PRIORIDADE dos chamados do Marketing pelos dias restantes até
    `data_entrega` (Fase 4 — 2026-07-09): >7d Baixa, 4-7d Média, 2-3d Alta, <=1d
    (inclusive vencido) Urgente. Caminho automático diário é o `pg_cron`
    (`supabase/migrations/0031_prioridade_dinamica_marketing.sql`, mesma função
    SQL); este botão/rota serve para rodar sob demanda ou como alvo de um
    scheduler externo, caso `pg_cron` não esteja habilitado no projeto. Só TI
    (rotina de manutenção do sistema, não é indicador de setor)."""
    _require_ti(ctx)
    n = await repo.recalcular_prioridade_marketing(ctx.user.claims)
    return RedirectResponse(f"/admin/gestao?prioridade_ok={n}", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/departamentos")
async def criar_departamento(
    request: Request,
    nome: str = Form(...),
    recebe_chamados: bool = Form(False),
    ctx: AdminCtx = Depends(admin_context),
    repo: AdminRepo = Depends(get_admin_repo),
    _: None = Depends(_csrf_guard),
):
    _require_ti(ctx)
    nome = nome.strip()
    if nome:
        await repo.criar_departamento(ctx.user.claims, nome, recebe_chamados=recebe_chamados)
        cache.invalidate(CACHE_DEPARTAMENTOS)
    return RedirectResponse("/admin/gestao", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/departamentos/{dep_id}/toggle")
async def toggle_departamento(
    request: Request,
    dep_id: str,
    ctx: AdminCtx = Depends(admin_context),
    repo: AdminRepo = Depends(get_admin_repo),
    _: None = Depends(_csrf_guard),
):
    _require_ti(ctx)
    await repo.toggle_departamento(ctx.user.claims, dep_id)
    cache.invalidate(CACHE_DEPARTAMENTOS)
    return RedirectResponse("/admin/gestao", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/departamentos/{dep_id}/toggle-recebe")
async def toggle_recebe_departamento(
    request: Request,
    dep_id: str,
    ctx: AdminCtx = Depends(admin_context),
    repo: AdminRepo = Depends(get_admin_repo),
    _: None = Depends(_csrf_guard),
):
    """Liga/desliga se o setor recebe chamado (tem fila de atendimento) — só o
    catálogo de categorias e o roteamento de chamado usam esse flag."""
    _require_ti(ctx)
    await repo.toggle_recebe_departamento(ctx.user.claims, dep_id)
    cache.invalidate(CACHE_DEPARTAMENTOS)
    return RedirectResponse("/admin/gestao", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/categorias")
async def criar_categoria(
    request: Request,
    nome: str = Form(...),
    descricao: str = Form(""),
    departamento_id: str = Form(""),
    ctx: AdminCtx = Depends(admin_context),
    repo: AdminRepo = Depends(get_admin_repo),
    _: None = Depends(_csrf_guard),
):
    _require_ti(ctx)
    nome = nome.strip()
    # Categorias pertencem a um departamento (0019); valida o setor informado.
    # Categoria só faz sentido num setor com fila de atendimento (0027).
    dep_id = AdminService.departamento_valido(
        await repo.departamentos(ctx.user.claims), departamento_id, exigir_fila=True
    )
    if nome and dep_id:
        await repo.criar_categoria(ctx.user.claims, nome, descricao.strip() or None, dep_id)
        cache.invalidate_prefix(CACHE_CATEGORIAS)
    return RedirectResponse("/admin/gestao", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/categorias/{cat_id}/toggle")
async def toggle_categoria(
    request: Request,
    cat_id: str,
    ctx: AdminCtx = Depends(admin_context),
    repo: AdminRepo = Depends(get_admin_repo),
    _: None = Depends(_csrf_guard),
):
    _require_ti(ctx)
    await repo.toggle_categoria(ctx.user.claims, cat_id)
    cache.invalidate_prefix(CACHE_CATEGORIAS)
    return RedirectResponse("/admin/gestao", status_code=status.HTTP_303_SEE_OTHER)


def _invalidar_subcategorias(categoria_id: str | None) -> None:
    """Expurga o cache de subcategorias da categoria afetada (chave por-categoria)."""
    if categoria_id:
        cache.invalidate(f"{CACHE_SUBCATEGORIAS}:{categoria_id}")


@router.post("/subcategorias")
async def criar_subcategoria(
    request: Request,
    nome: str = Form(...),
    categoria_id: str = Form(""),
    ctx: AdminCtx = Depends(admin_context),
    repo: AdminRepo = Depends(get_admin_repo),
    _: None = Depends(_csrf_guard),
):
    _require_ti(ctx)
    nome = nome.strip()
    categoria_id = categoria_id.strip()
    # A categoria-mãe precisa existir/estar ativa (defesa em profundidade).
    valida = categoria_id and any(
        str(c["id"]) == categoria_id and c.get("ativo")
        for c in await repo.categorias(ctx.user.claims)
    )
    if nome and valida:
        await repo.criar_subcategoria(ctx.user.claims, categoria_id, nome)
        _invalidar_subcategorias(categoria_id)
    return RedirectResponse("/admin/gestao", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/subcategorias/{sub_id}/toggle")
async def toggle_subcategoria(
    request: Request,
    sub_id: str,
    ctx: AdminCtx = Depends(admin_context),
    repo: AdminRepo = Depends(get_admin_repo),
    _: None = Depends(_csrf_guard),
):
    _require_ti(ctx)
    categoria_id = await repo.toggle_subcategoria(ctx.user.claims, sub_id)
    _invalidar_subcategorias(categoria_id)
    return RedirectResponse("/admin/gestao", status_code=status.HTTP_303_SEE_OTHER)


_PLANO_CAMPOS = (
    "resposta_baixa_min", "resposta_media_min", "resposta_alta_min",
    "resolucao_baixa_min", "resolucao_media_min", "resolucao_alta_min",
    "resposta_default_min", "resolucao_default_min",
)


@router.post("/planos/{plano_id}")
async def editar_plano(
    request: Request,
    plano_id: str,
    ctx: AdminCtx = Depends(admin_context),
    repo: AdminRepo = Depends(get_admin_repo),
    _: None = Depends(_csrf_guard),
):
    """Edita os tempos de SLA (minutos) de um plano. Só TI. Vale para os chamados
    criados a partir de agora (o trigger `calcular_sla_chamado` recalcula)."""
    _require_ti(ctx)
    form = await request.form()

    def _minutos(nome: str) -> int | None:
        bruto = (form.get(nome) or "").strip()
        if not bruto:
            return None
        try:
            n = int(bruto)
        except ValueError:
            return None
        return n if n >= 0 else None

    campos = {c: _minutos(c) for c in _PLANO_CAMPOS}
    await repo.atualizar_plano(ctx.user.claims, plano_id, campos=campos)
    return RedirectResponse("/admin/gestao", status_code=status.HTTP_303_SEE_OTHER)


# --------------------------------------------------------------------------
# Gestão de contas (criar/promover usuários) — exclusivo do TI.
# --------------------------------------------------------------------------
_PAPEIS = {"CLIENTE", "OPERADOR", "ADMIN"}
# Rótulos amigáveis (o papel interno CLIENTE aparece como "Funcionário" na UI).
_PAPEL_LABEL = {"CLIENTE": "Funcionário", "OPERADOR": "Operador", "ADMIN": "Admin de setor"}
_SENHA_MIN = 8


async def _emails_por_id() -> dict[str, str]:
    """Mapa ``{user_id: email}`` via Admin API (service_role). O e-mail vive em
    ``auth.users``, fora do alcance do papel ``authenticated`` — por isso vem daqui.
    Degrada para ``{}`` se a service_role não estiver configurada."""
    from app.auth.supabase_client import ensure_admin_client

    client = await ensure_admin_client()
    if client is None:
        return {}
    try:
        usuarios = await client.auth.admin.list_users()
    except Exception:  # noqa: BLE001 — Admin API indisponível → sem e-mails
        return {}
    itens = getattr(usuarios, "users", usuarios) or []
    return {str(u.id): (getattr(u, "email", None) or "") for u in itens}


@router.get("/usuarios")
async def usuarios(
    request: Request,
    ok: str = "",
    erro: str = "",
    confirmar: str = "",
    ctx: AdminCtx = Depends(admin_context),
    repo: AdminRepo = Depends(get_admin_repo),
):
    _require_ti(ctx)
    from app.auth.supabase_client import ensure_admin_client

    lista = await repo.usuarios(ctx.user.claims)
    emails = await _emails_por_id()
    for u in lista:
        u["email"] = emails.get(str(u["id"]), "")
    return render(
        request,
        "admin/usuarios.html",
        {
            **_base_ctx(ctx),
            "usuarios": lista,
            # Setor de origem (0028): todo usuário tem um, então TODOS os
            # departamentos ativos entram aqui (não só os que recebem chamado).
            "departamentos": [d for d in await repo.departamentos(ctx.user.claims) if d.get("ativo")],
            "admin_disponivel": (await ensure_admin_client()) is not None,
            "ok": ok,
            "erro": erro,
            # Exclusão em 2 etapas: ``confirmar`` = id do usuário aguardando confirmação.
            "confirmar": confirmar.strip(),
        },
    )


@router.post("/usuarios")
async def criar_usuario(
    request: Request,
    nome: str = Form(""),
    email: str = Form(...),
    senha: str = Form(...),
    papel: str = Form("CLIENTE"),
    departamento_id: str = Form(""),
    ctx: AdminCtx = Depends(admin_context),
    repo: AdminRepo = Depends(get_admin_repo),
    _: None = Depends(_csrf_guard),
):
    """Cria a conta (GoTrue Admin API) já confirmada e promove papel + setor
    (todo usuário tem um setor de origem — 0028). Dual-write: app_metadata.role
    (JWT) + perfis (RLS)."""
    _require_ti(ctx)
    from app.auth.supabase_client import ensure_admin_client

    def _volta(*, ok: str = "", erro: str = ""):
        from urllib.parse import urlencode

        qs = urlencode({k: v for k, v in {"ok": ok, "erro": erro}.items() if v})
        return RedirectResponse(f"/admin/usuarios?{qs}", status_code=status.HTTP_303_SEE_OTHER)

    email = email.strip().lower()
    nome = nome.strip() or email
    papel = papel.strip().upper()
    if papel not in _PAPEIS:
        papel = "CLIENTE"
    if len(senha) < _SENHA_MIN:
        return _volta(erro=f"A senha deve ter ao menos {_SENHA_MIN} caracteres.")
    # Setor de origem obrigatório pra qualquer papel (0028) — inclusive
    # Funcionário: é o que permite o líder do setor ver os chamados da equipe.
    # Só OPERADOR exige setor com fila (é quem atende, 0028).
    dep_id = AdminService.departamento_valido(
        await repo.departamentos(ctx.user.claims), departamento_id, exigir_fila=(papel == "OPERADOR")
    )
    if dep_id is None:
        return _volta(erro="Selecione o departamento do usuário.")

    client = await ensure_admin_client()
    if client is None:
        return _volta(erro="Criação indisponível: service_role não configurada no servidor.")

    try:
        resp = await client.auth.admin.create_user(
            {
                "email": email,
                "password": senha,
                "email_confirm": True,
                "user_metadata": {"nome": nome},
                "app_metadata": {"role": papel},
            }
        )
    except Exception as exc:  # noqa: BLE001 — e-mail duplicado / erro do GoTrue
        return _volta(erro=f"Não foi possível criar: {type(exc).__name__} (e-mail já existe?).")

    novo = getattr(resp, "user", None)
    if novo is None:
        return _volta(erro="Conta não criada (resposta inesperada do Supabase).")

    # O trigger criou o perfil CLIENTE sem setor; sempre promovemos papel+setor.
    await repo.atualizar_papel(ctx.user.claims, str(novo.id), role=papel, departamento_id=dep_id)

    # Foto de perfil (opcional, Fase 7): o TI já pode enviá-la na criação da
    # conta. O upload usa a service_role key (não o JWT do TI) porque a policy
    # de Storage só libera cada um escrever no PRÓPRIO path — aqui o alvo é o
    # path do usuário recém-criado, então é preciso bypassar a RLS.
    form_data = await request.form()
    avatar = form_data.get("avatar")
    aviso_avatar = ""
    if avatar is not None and getattr(avatar, "filename", ""):
        from app.avatar_storage import (
            AvatarStorageError,
            avatar_path as _avatar_path,
            enviar_avatar,
            preparar_avatar,
        )
        from app.config import get_settings
        from app.security.uploads import UploadInvalido

        settings = get_settings()
        conteudo = await avatar.read(settings.avatar_max_bytes + 1)
        try:
            processada = preparar_avatar(avatar.filename, conteudo, max_bytes=settings.avatar_max_bytes)
            path = _avatar_path(str(novo.id), "png")
            await enviar_avatar(
                settings.supabase_service_role_key, path, processada, "image/png"
            )
            await repo.atualizar_avatar(ctx.user.claims, str(novo.id), avatar_path=path)
        except (UploadInvalido, AvatarStorageError) as exc:
            aviso_avatar = f" (a foto não foi salva: {exc})"

    return _volta(ok=f"Conta {email} criada como {_PAPEL_LABEL[papel]}.{aviso_avatar}")


@router.post("/usuarios/{user_id}/papel")
async def mudar_papel(
    request: Request,
    user_id: str,
    papel: str = Form("CLIENTE"),
    departamento_id: str = Form(""),
    ctx: AdminCtx = Depends(admin_context),
    repo: AdminRepo = Depends(get_admin_repo),
    _: None = Depends(_csrf_guard),
):
    """Altera papel + setor de um usuário existente. Dual-write: perfis (RLS) e
    app_metadata.role (Admin API). A mudança do JWT vale no próximo login/refresh."""
    _require_ti(ctx)
    from app.auth.supabase_client import ensure_admin_client

    def _volta(*, ok: str = "", erro: str = ""):
        from urllib.parse import urlencode

        qs = urlencode({k: v for k, v in {"ok": ok, "erro": erro}.items() if v})
        return RedirectResponse(f"/admin/usuarios?{qs}", status_code=status.HTTP_303_SEE_OTHER)

    papel = papel.strip().upper()
    if papel not in _PAPEIS:
        return _volta(erro="Papel inválido.")
    dep_id = AdminService.departamento_valido(
        await repo.departamentos(ctx.user.claims), departamento_id, exigir_fila=(papel == "OPERADOR")
    )
    if dep_id is None:
        return _volta(erro="Selecione o departamento do usuário.")

    client = await ensure_admin_client()
    resultado = await AdminService.promover_papel(
        repo=repo, claims=ctx.user.claims, user_id=user_id, papel=papel,
        departamento_id=dep_id, client=client,
    )
    if not resultado.sucesso:
        return _volta(erro=resultado.mensagem)
    return _volta(ok=resultado.mensagem)


@router.post("/usuarios/{user_id}/excluir")
async def excluir_usuario(
    request: Request,
    user_id: str,
    ctx: AdminCtx = Depends(admin_context),
    repo: AdminRepo = Depends(get_admin_repo),
    _: None = Depends(_csrf_guard),
):
    """Exclui a conta (GoTrue Admin API). A remoção de ``auth.users`` cascateia
    para ``perfis``; se o usuário tiver chamados/mensagens (FK RESTRICT), a
    exclusão falha e sugerimos desativar em vez de excluir. Só TI."""
    _require_ti(ctx)
    from app.auth.supabase_client import ensure_admin_client

    def _volta(*, ok: str = "", erro: str = ""):
        from urllib.parse import urlencode

        qs = urlencode({k: v for k, v in {"ok": ok, "erro": erro}.items() if v})
        return RedirectResponse(f"/admin/usuarios?{qs}", status_code=status.HTTP_303_SEE_OTHER)

    if str(user_id) == str(ctx.user.id):
        return _volta(erro="Você não pode excluir a própria conta.")

    client = await ensure_admin_client()
    if client is None:
        return _volta(erro="Exclusão indisponível: service_role não configurada no servidor.")

    try:
        await client.auth.admin.delete_user(user_id)
    except Exception:  # noqa: BLE001 — FK RESTRICT (tem chamados/mensagens) ou erro do GoTrue
        return _volta(
            erro="Não foi possível excluir: o usuário tem chamados ou mensagens no "
                 "histórico. Reatribua/encerre esses itens ou apenas não use a conta."
        )
    return _volta(ok="Usuário excluído.")


_CSV_COLS = [
    "codigo", "titulo", "descricao", "status", "prioridade", "departamento",
    "categoria", "subcategoria", "solicitante", "operador", "created_at",
    "limite_resolucao", "respondido_em", "resolvido_em", "avaliacao_nota",
    "avaliacao_em", "avaliacao_comentario",
]

# Cabeçalho legível (pt-BR) — mesmo padrão de planilha de relatório usado pela
# empresa: nomes de coluna por extenso em vez das chaves internas do banco.
_CSV_HEADERS = {
    "codigo": "Chamado",
    "titulo": "Título",
    "descricao": "Descrição",
    "status": "Status",
    "prioridade": "Prioridade",
    "departamento": "Departamento",
    "categoria": "Categoria",
    "subcategoria": "Subcategoria",
    "solicitante": "Solicitante",
    "operador": "Operador",
    "created_at": "Data de criação",
    "limite_resolucao": "Prazo de resolução",
    "respondido_em": "Respondido em",
    "resolvido_em": "Resolvido em",
    "avaliacao_nota": "Avaliação",
    "avaliacao_em": "Avaliado em",
    "avaliacao_comentario": "Comentário da avaliação",
}


@router.get("/export/csv")
async def export_csv(
    request: Request,
    ctx: AdminCtx = Depends(admin_context),
    repo: AdminRepo = Depends(get_admin_repo),
):
    from app.templating import fmt_dt

    linhas = await repo.exportar(ctx.user.claims)
    buf = io.StringIO()
    writer = csv.DictWriter(
        buf, fieldnames=_CSV_COLS, extrasaction="ignore", delimiter=";"
    )
    writer.writerow(_CSV_HEADERS)
    for r in linhas:
        writer.writerow(
            {
                k: (fmt_dt(v, "%d/%m/%Y %H:%M") if isinstance(v, datetime) else (v if v is not None else ""))
                for k, v in r.items()
            }
        )
    nome = f"chamados_{datetime.utcnow():%Y%m%d}.csv"
    # BOM UTF-8: sem ele o Excel (padrão pt-BR) lê acentos como lixo (mojibake).
    # ";" como delimitador é o padrão de CSV que o Excel pt-BR abre já separado
    # em colunas (o separador decimal "," do locale conflita com "," de campo).
    conteudo = "﻿" + buf.getvalue()
    return Response(
        content=conteudo,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{nome}"'},
    )


def register_admin_routes(app) -> None:
    app.include_router(router)
