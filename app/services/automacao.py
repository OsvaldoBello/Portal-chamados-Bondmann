"""Automação de criação/desligamento de usuários — orquestração (F2, 2026-09-14).

Três responsabilidades, todas governadas por `plano_md_mestre_automacao_acessos.md`:

1. **Aprovação** (:func:`aprovar`): o TI clicou "Executar automação" no
   atendimento → monta o payload a partir de `dados_formulario`, calcula
   quando rodar e grava o job NA_FILA (sob RLS do TI).
2. **Resultado** (:func:`processar_resultado`): o worker devolveu as etapas →
   conta no Portal (criação concluída), nota interna técnica, mensagem ao RH
   (com credenciais quando tudo deu certo — D4), RESOLVIDO se concluído,
   REVOGAR_LICENCA agendada (+15 dias) após desligamento, e-mails.
3. **Vigilância** (:func:`iniciar_vigilancia`): job EXECUTANDO sem heartbeat
   vira FALHOU (ou volta pra fila, uma vez, se não for desligamento); worker
   mudo com fila vencida gera alerta por e-mail (uma vez por hora).

As mensagens gravadas no chamado saem em nome de quem APROVOU o job
(`aprovado_por`, um TI) — sem usuário de serviço novo no Supabase Auth.
"""

from __future__ import annotations

import asyncio
import logging
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from app.config import Settings, get_settings
from app.db import rls_connection
from app.domain import automacao as dom
from app.repositories import automacao as repo_admin
from app.repositories.automacao import AutomacaoRepo, JobAtivoExistente

log = logging.getLogger(__name__)

# Setores fixos no Portal para os perfis comerciais (spec 2026-09-10 da automação).
_SETOR_PORTAL_POR_PERFIL = {"REPRESENTANTE": "Representantes", "SUPERVISOR": "Supervisão de Vendas"}

# Estado em memória do contato com o worker (um processo — ADR-0006).
_ultimo_contato: datetime | None = None
_ultimo_worker_id: str | None = None
_ultimo_alerta_silencio: datetime | None = None


def registrar_contato_worker(worker_id: str) -> None:
    global _ultimo_contato, _ultimo_worker_id
    _ultimo_contato = datetime.now(UTC)
    _ultimo_worker_id = worker_id


def estado_worker() -> dict[str, Any]:
    """Para `/health/ready` e o card: quando o worker falou pela última vez."""
    return {
        "ultimo_contato": _ultimo_contato.isoformat() if _ultimo_contato else None,
        "worker_id": _ultimo_worker_id,
        "segundos_sem_contato": (
            int((datetime.now(UTC) - _ultimo_contato).total_seconds()) if _ultimo_contato else None
        ),
    }


def tipos_liberados(settings: Settings) -> set[str]:
    return {t.strip().upper() for t in settings.automacao_tipos.split(",") if t.strip()} & set(dom.TIPOS)


def api_habilitada(settings: Settings) -> bool:
    return bool(settings.automacao_worker_token)


# --------------------------------------------------------------------------
# Card do atendimento
# --------------------------------------------------------------------------
@dataclass(frozen=True)
class CardAutomacao:
    tipo: str
    tipo_label: str
    ativa: bool  # kill switch geral
    tipo_liberado: bool
    ultimo: dict[str, Any] | None  # job mais recente (ou None)
    historico: list[dict[str, Any]]  # demais jobs
    resumo: list[tuple[str, str]]  # o que será enviado ao worker
    pode_executar: bool
    pode_cancelar: bool
    pode_reexecutar: bool
    aviso: str


def montar_card(
    chamado: dict[str, Any],
    jobs: list[dict[str, Any]],
    settings: Settings,
    *,
    pode_agir: bool,
) -> CardAutomacao | None:
    """Estado do card "Automação de acessos" para a tela de atendimento —
    ``None`` quando o chamado não é de uma subcategoria automatizável ou não
    tem os dados estruturados (aberto fora do gate D3)."""
    tipo = dom.tipo_da_subcategoria(chamado.get("subcategoria"))
    dados = chamado.get("dados_formulario") or {}
    if tipo is None or not dados:
        return None
    ultimo = jobs[0] if jobs else None
    ativo = bool(ultimo and ultimo["status"] in dom.STATUS_ATIVOS)
    tipo_ok = tipo in tipos_liberados(settings)
    resolvido = chamado.get("status") == "RESOLVIDO"
    resumo = dom.resumo_payload(
        ultimo["payload"] if ultimo else dom.montar_payload(tipo, dados, chamado)
    )
    aviso = ""
    if not settings.automacao_ativa:
        aviso = "Automação pausada (AUTOMACAO_ATIVA desligada) — jobs na fila esperam."
    elif not tipo_ok:
        aviso = f"Tipo {tipo} não liberado em AUTOMACAO_TIPOS."
    pode_executar = pode_agir and not ativo and not resolvido
    pode_reexecutar = bool(
        pode_agir and not ativo and not resolvido and ultimo
        and ultimo["status"] in (dom.STATUS_COM_PENDENCIAS, dom.STATUS_FALHOU)
        and not ultimo.get("dry_run")
    )
    pode_cancelar = bool(pode_agir and ultimo and ultimo["status"] == dom.STATUS_NA_FILA)
    return CardAutomacao(
        tipo=tipo,
        tipo_label={"CRIACAO": "Criação de acessos", "DESLIGAMENTO": "Desligamento de acessos"}[tipo],
        ativa=settings.automacao_ativa,
        tipo_liberado=tipo_ok,
        ultimo=ultimo,
        historico=jobs[1:],
        resumo=resumo,
        pode_executar=pode_executar,
        pode_cancelar=pode_cancelar,
        pode_reexecutar=pode_reexecutar,
        aviso=aviso,
    )


# --------------------------------------------------------------------------
# Aprovação (TI, sob RLS)
# --------------------------------------------------------------------------
class AprovacaoInvalida(Exception):
    pass


async def aprovar(
    repo: AutomacaoRepo,
    claims: dict,
    chamado: dict[str, Any],
    *,
    aprovado_por: str,
    dry_run: bool,
    reexecutar: bool,
    settings: Settings | None = None,
) -> dict[str, Any]:
    """Cria o job NA_FILA para o chamado. ``reexecutar``: usa o payload do
    último job e pula as etapas que já deram SUCCESS nele."""
    settings = settings or get_settings()
    tipo = dom.tipo_da_subcategoria(chamado.get("subcategoria"))
    dados = chamado.get("dados_formulario") or {}
    if tipo is None or not dados:
        raise AprovacaoInvalida("Este chamado não tem os dados estruturados da automação.")
    if chamado.get("status") == "RESOLVIDO":
        raise AprovacaoInvalida("Chamado já resolvido.")
    jobs = await repo.jobs_do_chamado(claims, str(chamado["id"]))
    anterior = jobs[0] if jobs else None
    if anterior and anterior["status"] in dom.STATUS_ATIVOS:
        raise AprovacaoInvalida("Já existe uma execução na fila ou em andamento para este chamado.")
    reexecucao_de = None
    if reexecutar:
        if not anterior or anterior["status"] not in (dom.STATUS_COM_PENDENCIAS, dom.STATUS_FALHOU):
            raise AprovacaoInvalida("Não há execução anterior com pendências para reexecutar.")
        pular = dom.etapas_concluidas(anterior.get("resultado"))
        payload = dict(anterior["payload"])
        payload["pular_etapas"] = list(pular)
        reexecucao_de = str(anterior["id"])
    else:
        payload = dom.montar_payload(tipo, dados, chamado)
    agora = datetime.now(UTC)
    feriados = await repo.feriados_entre(
        claims, agora.date() - timedelta(days=1), agora.date() + timedelta(days=400)
    )
    executar_apos = dom.calcular_executar_apos(
        tipo, payload, agora=agora, feriados=feriados,
        hora_criacao=settings.automacao_hora_criacao,
        hora_desligamento=settings.automacao_hora_desligamento,
        licenca_dias=settings.automacao_licenca_dias,
    )
    if dry_run:
        executar_apos = agora  # simulação roda já, independentemente da data
    try:
        return await repo.criar_job(
            claims,
            chamado_id=str(chamado["id"]),
            tipo=tipo,
            payload=payload,
            executar_apos=executar_apos,
            dry_run=dry_run,
            aprovado_por=aprovado_por,
            reexecucao_de=reexecucao_de,
        )
    except JobAtivoExistente as exc:
        raise AprovacaoInvalida("Já existe uma execução na fila para este chamado.") from exc


# --------------------------------------------------------------------------
# Resultado do worker (conexão administrativa; roda em background)
# --------------------------------------------------------------------------
def _claims_aprovador(perfil_id: str) -> dict[str, Any]:
    """Claims mínimos para agir sob RLS como o TI que aprovou (ADR-0001: os
    claims são aplicados por ``SET LOCAL``, não verificados como JWT aqui —
    é o direito de quem clicou, exercido em background)."""
    return {"sub": perfil_id, "role": "authenticated", "app_metadata": {"role": "OPERADOR"}}


async def _criar_conta_portal(payload: dict[str, Any], aprovado_por: str) -> tuple[bool, str]:
    """Conta no Portal de Chamados para o novo colaborador (decisão do gestor:
    automático). Mesmo caminho de ``/admin/usuarios``: GoTrue Admin cria a
    conta (senha aleatória descartada — o 1º acesso é por "Esqueci minha
    senha", instruído no e-mail de boas-vindas) e o perfil recebe papel +
    setor via RLS do aprovador (TI), o que satisfaz `perfis_admin_all` e o
    trigger `perfis_self_so_avatar`."""
    from app.auth.supabase_client import ensure_admin_client

    email = str(payload.get("email") or "").strip().lower()
    nome = str(payload.get("nome_completo") or "").strip() or email
    perfil = payload.get("perfil")
    if perfil in _SETOR_PORTAL_POR_PERFIL:
        papel, setor_nome = "CLIENTE", _SETOR_PORTAL_POR_PERFIL[perfil]
    else:
        papel = payload.get("portal_papel") or "CLIENTE"
        setor_nome = payload.get("portal_setor") or ""
    if not email:
        return False, "payload sem e-mail"
    client = await ensure_admin_client()
    if client is None:
        return False, "service_role não configurada no portal"
    claims = _claims_aprovador(aprovado_por)
    async with rls_connection(claims) as conn:
        dep_id = await conn.fetchval(
            "SELECT id::text FROM departamentos WHERE ativo AND lower(nome) = lower($1)", setor_nome
        )
    if dep_id is None:
        return False, f'setor "{setor_nome}" não encontrado no Portal'
    try:
        resp = await client.auth.admin.create_user(
            {
                "email": email,
                "password": secrets.token_urlsafe(24),
                "email_confirm": True,
                "user_metadata": {"nome": nome},
                "app_metadata": {"role": papel},
            }
        )
    except Exception as exc:  # noqa: BLE001 — GoTrue: e-mail duplicado etc.
        return False, (
            f"GoTrue recusou a criação ({type(exc).__name__}) — se o e-mail já existe no "
            "Portal, ajuste papel/setor em /admin/usuarios"
        )
    novo = getattr(resp, "user", None)
    if novo is None:
        return False, "resposta inesperada do Supabase ao criar a conta"
    user_id = str(novo.id)
    # O trigger `handle_new_user` criou o perfil como CLIENTE sem setor;
    # promove papel + setor (dual-write: app_metadata já foi no create_user).
    async with rls_connection(claims) as conn:
        await conn.execute(
            "UPDATE perfis SET nome = $2, role = $3::papel_usuario, departamento_id = $4::uuid WHERE id = $1::uuid",
            user_id, nome, papel, dep_id,
        )
    from app.notification import notificar_novo_usuario_email

    try:
        await notificar_novo_usuario_email(nome, email)
    except Exception as exc:  # noqa: BLE001 — e-mail nunca derruba o processamento
        log.warning("[AUTOMACAO] boas-vindas não enviadas a %s: %s", email, type(exc).__name__)
    return True, f"conta {email} criada como {papel} em {setor_nome}"


def destinatarios_alerta(settings: Settings) -> list[str]:
    """`AUTOMACAO_ALERTA_EMAIL` aceita vários endereços separados por vírgula
    ou ponto-e-vírgula (admins da TI escolhidos pelo gestor)."""
    brutos = (settings.automacao_alerta_email or "").replace(";", ",").split(",")
    vistos: list[str] = []
    for e in (b.strip().lower() for b in brutos):
        if e and "@" in e and e not in vistos:
            vistos.append(e)
    return vistos


async def _email_alerta_ti(settings: Settings, assunto: str, corpo: str) -> None:
    destinos = destinatarios_alerta(settings)
    if not destinos:
        log.warning("[AUTOMACAO] alerta NÃO enviado (AUTOMACAO_ALERTA_EMAIL vazia): %s", assunto)
        return
    from app.notification import enviar_email

    for para in destinos:
        try:
            await enviar_email(para, assunto, corpo)
        except Exception as exc:  # noqa: BLE001
            log.warning("[AUTOMACAO] alerta por e-mail para %s falhou: %s", para, type(exc).__name__)


async def processar_resultado(
    job: dict[str, Any],
    etapas: list[dict[str, Any]],
    credenciais_brutas: Any,
    licenca: Any,
    *,
    settings: Settings | None = None,
) -> None:
    """Efeitos do resultado já persistido em `automacao_jobs` (ver docstring
    do módulo). Cada efeito é isolado: falha num não impede os demais."""
    settings = settings or get_settings()
    chamado_id = str(job["chamado_id"])
    codigo = job.get("chamado_codigo") or ""
    remetente = str(job["aprovado_por"])
    status_final = str(job["status"])
    tipo = str(job["tipo"])
    dry_run = bool(job.get("dry_run"))
    erro_geral = job.get("erro")
    site = settings.site_url.rstrip("/")

    # 1) Conta no Portal (criação concluída, real).
    portal_msg = ""
    if tipo == dom.TIPO_CRIACAO and status_final == dom.STATUS_CONCLUIDO and not dry_run:
        try:
            ok, portal_msg = await _criar_conta_portal(job["payload"], remetente)
        except Exception as exc:  # noqa: BLE001
            ok, portal_msg = False, f"erro inesperado ({type(exc).__name__})"
        etapas = etapas + [
            {
                "nome": "Portal de Chamados Bondmann - Conta e Permissões",
                "status": dom.ETAPA_SUCCESS if ok else dom.ETAPA_FAILED,
                "erro": None if ok else portal_msg,
                "detalhes": {"portal": portal_msg} if ok else {},
            }
        ]
        if not ok:
            status_final = dom.STATUS_COM_PENDENCIAS

    # 2) Nota interna técnica (sempre).
    try:
        await repo_admin.admin_gravar_mensagem(
            chamado_id, remetente, dom.texto_nota_interna(job, etapas, erro_geral=erro_geral), interna=True
        )
    except Exception:  # noqa: BLE001
        log.exception("[AUTOMACAO] nota interna não gravada (job %s)", job.get("id"))

    # 3) Mensagem ao RH — não em FALHOU (a TI assume manualmente) nem em REVOGAR_LICENCA.
    publica = status_final in (dom.STATUS_CONCLUIDO, dom.STATUS_COM_PENDENCIAS) and tipo != dom.TIPO_REVOGAR_LICENCA
    if publica:
        credenciais = dom.filtrar_credenciais(credenciais_brutas) if not dry_run else []
        if portal_msg and status_final == dom.STATUS_CONCLUIDO:
            credenciais.append(("Portal de Chamados", "conta criada — primeiro acesso por \"Esqueci minha senha\""))
        texto = dom.texto_mensagem_publica(job, etapas, credenciais, status_final)
        try:
            await repo_admin.admin_gravar_mensagem(chamado_id, remetente, texto, interna=False)
            # E-mail de "nova mensagem" ao autor/observadores com corpo NEUTRO
            # (o padrão reproduz o texto integral — aqui ele pode ter senha).
            from app.notification import notificar_nova_mensagem_email

            chamado_min = {
                "id": chamado_id, "codigo": codigo, "titulo": job.get("chamado_titulo") or "",
                "cliente_id": job.get("chamado_cliente_id"), "operador_id": job.get("chamado_operador_id"),
            }
            await notificar_nova_mensagem_email(
                chamado_min, remetente, dom.texto_email_neutro(codigo, status_final)
            )
        except Exception:  # noqa: BLE001
            log.exception("[AUTOMACAO] mensagem pública não gravada/notificada (job %s)", job.get("id"))

    # 4) Resolver o chamado só quando TUDO deu certo, de verdade.
    if status_final == dom.STATUS_CONCLUIDO and not dry_run and tipo != dom.TIPO_REVOGAR_LICENCA:
        try:
            await repo_admin.admin_resolver_chamado(chamado_id, remetente, str(job["id"]))
        except Exception:  # noqa: BLE001
            log.exception("[AUTOMACAO] chamado %s não resolvido (job %s)", codigo, job.get("id"))

    # 5) Licença M365: agenda a revogação +N dias após um desligamento real.
    if tipo == dom.TIPO_DESLIGAMENTO and not dry_run and isinstance(licenca, dict) and licenca.get("email"):
        try:
            payload_lic = dom.montar_payload_revogar_licenca(licenca, {"id": chamado_id, "codigo": codigo})
            agora = datetime.now(UTC)
            executar_apos = dom.calcular_executar_apos(
                dom.TIPO_REVOGAR_LICENCA, payload_lic, agora=agora, feriados=set(),
                hora_criacao=settings.automacao_hora_criacao, licenca_dias=settings.automacao_licenca_dias,
            )
            novo = await repo_admin.admin_agendar_job(
                chamado_id=chamado_id, tipo=dom.TIPO_REVOGAR_LICENCA, payload=payload_lic,
                executar_apos=executar_apos, aprovado_por=remetente, reexecucao_de=str(job["id"]),
            )
            if novo:
                await repo_admin.admin_registrar_historico(
                    chamado_id, remetente, "AUTOMACAO_APROVADA",
                    {"job_id": str(novo["id"]), "tipo": dom.TIPO_REVOGAR_LICENCA,
                     "executar_apos": executar_apos.isoformat(), "origem": str(job["id"])},
                )
        except Exception:  # noqa: BLE001
            log.exception("[AUTOMACAO] revogação de licença não agendada (job %s)", job.get("id"))

    # 6) Alerta detalhado aos admins da TI quando algo falhou (regra do gestor,
    #    2026-09-15): cada etapa com o erro devolvido, o que ficou por fazer, link.
    if status_final in (dom.STATUS_COM_PENDENCIAS, dom.STATUS_FALHOU):
        rotulo = "FALHOU" if status_final == dom.STATUS_FALHOU else "com pendências"
        await _email_alerta_ti(
            settings,
            f"[Automação de acessos] {tipo} {rotulo} — chamado {codigo}{' (simulação)' if dry_run else ''}",
            dom.texto_email_alerta_ti(
                job, etapas, status_final, erro_geral=erro_geral,
                link=f"{site}/workspace/chamados/{chamado_id}",
            ),
        )


# --------------------------------------------------------------------------
# Vigilância (lifespan)
# --------------------------------------------------------------------------
async def vigiar_uma_vez(settings: Settings) -> None:
    global _ultimo_alerta_silencio
    site = settings.site_url.rstrip("/")
    # (a) jobs com heartbeat vencido
    for job in await repo_admin.admin_jobs_travados(settings.automacao_heartbeat_timeout_s):
        requeue = job["tipo"] != dom.TIPO_DESLIGAMENTO and int(job.get("tentativas") or 0) < 2
        motivo = (
            f"worker {job.get('worker_id') or '?'} parou de responder na etapa "
            f"\"{job.get('etapa_atual') or '?'}\" (sem heartbeat há mais de "
            f"{int(settings.automacao_heartbeat_timeout_s)}s)"
        )
        atualizado = await repo_admin.admin_marcar_travado(str(job["id"]), motivo, requeue=requeue)
        if not atualizado:
            continue
        log.warning("[AUTOMACAO] job %s travado → %s", job["id"], "NA_FILA" if requeue else "FALHOU")
        try:
            await repo_admin.admin_gravar_mensagem(
                str(job["chamado_id"]), str(job["aprovado_por"]),
                f"Automação: {motivo}. "
                + ("O job voltou para a fila e será tentado de novo." if requeue
                   else "Marcado como FALHOU — desligamento pela metade exige conferência manual antes de repetir."),
                interna=True,
            )
        except Exception:  # noqa: BLE001
            log.exception("[AUTOMACAO] nota de job travado não gravada")
        await _email_alerta_ti(
            settings,
            f"[Automação de acessos] Execução travada no chamado {job.get('chamado_codigo') or ''}",
            f"{motivo}.\n\n{'Voltou para a fila automaticamente.' if requeue else 'Precisa de conferência manual.'}\n\n"
            f"{site}/workspace/chamados/{job['chamado_id']}\n",
        )
    # (b) worker mudo com fila vencida
    vencida_desde = await repo_admin.admin_fila_vencida_desde()
    if vencida_desde is None:
        return
    agora = datetime.now(UTC)
    silencio_s = settings.automacao_worker_silencio_s
    ref = _ultimo_contato or vencida_desde
    if (agora - ref).total_seconds() < silencio_s:
        return
    if _ultimo_alerta_silencio and (agora - _ultimo_alerta_silencio) < timedelta(hours=1):
        return
    _ultimo_alerta_silencio = agora
    log.warning("[AUTOMACAO] worker sem contato com fila vencida desde %s", vencida_desde.isoformat())
    await _email_alerta_ti(
        settings,
        "[Automação de acessos] Worker sem contato",
        f"Há job na fila vencido desde {vencida_desde.astimezone(UTC).isoformat()} e o worker "
        f"não fala com o portal há mais de {int(silencio_s)}s "
        f"(último contato: {_ultimo_contato.isoformat() if _ultimo_contato else 'nunca desde o boot'}).\n\n"
        "Confira o serviço automacao-worker no Railway e o túnel Tailscale.\n",
    )


async def _loop_vigilancia(settings: Settings) -> None:
    while True:
        await asyncio.sleep(settings.automacao_vigilancia_intervalo_s)
        try:
            await vigiar_uma_vez(settings)
        except Exception as exc:  # noqa: BLE001 — loop de fundo nunca morre por uma volta
            log.warning("[AUTOMACAO] ciclo de vigilância falhou: %s", exc)


def iniciar_vigilancia(settings: Settings | None = None) -> asyncio.Task | None:
    settings = settings or get_settings()
    if not settings.automacao_ativa or settings.automacao_vigilancia_intervalo_s <= 0:
        return None
    return asyncio.create_task(_loop_vigilancia(settings))
