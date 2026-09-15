"""Repositório da fila de automação de acessos (`automacao_jobs`, migration 0091).

Duas superfícies, de propósito separadas:

- :class:`AutomacaoRepo` — o que o **staff** faz pela tela de atendimento,
  sob RLS (criar job ao aprovar, cancelar, listar). Policies da 0091.
- Funções ``admin_*`` — o que o **worker** e a **vigilância** fazem sem
  usuário (claim atômico, heartbeat, resultado, jobs travados), pela conexão
  administrativa — mesmo precedente da IA de triagem em `ia_triagens`.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from typing import Any

import asyncpg

from app.db import admin_connection, rls_connection


class JobAtivoExistente(Exception):
    """Já há um job NA_FILA/EXECUTANDO deste tipo para o chamado (índice
    parcial `ux_automacao_jobs_ativo`)."""


def _row(row: asyncpg.Record | None) -> dict[str, Any] | None:
    if row is None:
        return None
    d = dict(row)
    for chave in ("payload", "resultado"):
        if isinstance(d.get(chave), str):
            d[chave] = json.loads(d[chave])
    return d


_COLUNAS = """
    j.id, j.chamado_id, j.tipo::text AS tipo, j.status::text AS status, j.dry_run,
    j.payload, j.resultado, j.executar_apos, j.aprovado_por, j.aprovado_em,
    j.worker_id, j.claimed_at, j.heartbeat_at, j.etapa_atual, j.finalizado_em,
    j.tentativas, j.reexecucao_de, j.erro, j.created_at, j.updated_at
"""


class AutomacaoRepo:
    """Superfície do staff (RLS)."""

    async def jobs_do_chamado(self, claims: dict, chamado_id: str) -> list[dict[str, Any]]:
        """Histórico de jobs do chamado, mais recente primeiro (RLS: só staff
        do departamento de destino enxerga — fora do escopo vem vazio)."""
        async with rls_connection(claims) as conn:
            rows = await conn.fetch(
                f"""SELECT {_COLUNAS}, p.nome AS aprovado_por_nome
                      FROM automacao_jobs j
                      LEFT JOIN perfis p ON p.id = j.aprovado_por
                     WHERE j.chamado_id = $1::uuid
                     ORDER BY j.created_at DESC""",
                chamado_id,
            )
        return [_row(r) for r in rows]  # type: ignore[misc]

    async def criar_job(
        self,
        claims: dict,
        *,
        chamado_id: str,
        tipo: str,
        payload: dict[str, Any],
        executar_apos: datetime,
        dry_run: bool,
        aprovado_por: str,
        reexecucao_de: str | None = None,
    ) -> dict[str, Any]:
        """Aprovação do TI: o job nasce direto NA_FILA (policy de INSERT exige
        ``aprovado_por = auth.uid()`` e staff do departamento do chamado)."""
        async with rls_connection(claims) as conn:
            try:
                # Savepoint: dentro do request a conexão é compartilhada com a
                # transação da rota — sem ele, a violação do índice deixaria a
                # transação abortada e o re-render seguinte quebraria.
                async with conn.transaction():
                    row = await conn.fetchrow(
                        f"""INSERT INTO automacao_jobs
                                (chamado_id, tipo, payload, executar_apos, dry_run, aprovado_por, reexecucao_de)
                            VALUES ($1::uuid, $2::automacao_tipo, $3::jsonb, $4, $5, $6::uuid, $7::uuid)
                         RETURNING {_COLUNAS.replace('j.', '')}""",
                        chamado_id,
                        tipo,
                        json.dumps(payload),
                        executar_apos,
                        dry_run,
                        aprovado_por,
                        reexecucao_de,
                    )
            except asyncpg.UniqueViolationError as exc:
                raise JobAtivoExistente() from exc
            await conn.execute(
                """INSERT INTO historico_chamados (chamado_id, ator_id, acao, detalhes)
                   VALUES ($1::uuid, $2::uuid, 'AUTOMACAO_APROVADA', $3::jsonb)""",
                chamado_id,
                aprovado_por,
                json.dumps(
                    {"job_id": str(row["id"]), "tipo": tipo, "dry_run": dry_run,
                     "executar_apos": executar_apos.isoformat(), "reexecucao_de": reexecucao_de}
                ),
            )
        return _row(row)  # type: ignore[return-value]

    async def cancelar_job(self, claims: dict, chamado_id: str, job_id: str, ator_id: str) -> bool:
        """Cancela um job ainda NA_FILA (policy de UPDATE só deixa essa transição)."""
        async with rls_connection(claims) as conn:
            row = await conn.fetchrow(
                """UPDATE automacao_jobs
                      SET status = 'CANCELADO', finalizado_em = now()
                    WHERE id = $1::uuid AND chamado_id = $2::uuid AND status = 'NA_FILA'
                RETURNING id""",
                job_id,
                chamado_id,
            )
            if row is None:
                return False
            await conn.execute(
                """INSERT INTO historico_chamados (chamado_id, ator_id, acao, detalhes)
                   VALUES ($1::uuid, $2::uuid, 'AUTOMACAO_CANCELADA', $3::jsonb)""",
                chamado_id,
                ator_id,
                json.dumps({"job_id": job_id}),
            )
            return True

    async def feriados_entre(self, claims: dict, de: date, ate: date) -> set[date]:
        async with rls_connection(claims) as conn:
            rows = await conn.fetch(
                "SELECT data FROM feriados WHERE data BETWEEN $1 AND $2", de, ate
            )
        return {r["data"] for r in rows}


_repo = AutomacaoRepo()


def get_automacao_repo() -> AutomacaoRepo:
    """Dependência FastAPI; sobreposta nos testes por um fake."""
    return _repo


# --------------------------------------------------------------------------
# Conexão administrativa — worker (API) e vigilância (lifespan)
# --------------------------------------------------------------------------
async def admin_claim_proximo(worker_id: str, tipos: list[str]) -> dict[str, Any] | None:
    """Pega UM job vencido da fila, atomicamente (``FOR UPDATE SKIP LOCKED``):
    dois workers nunca levam o mesmo job."""
    if not tipos:
        return None
    async with admin_connection() as conn:
        row = await conn.fetchrow(
            f"""WITH proximo AS (
                    SELECT id FROM automacao_jobs
                     WHERE status = 'NA_FILA'
                       AND executar_apos <= now()
                       AND tipo::text = ANY($2::text[])
                     ORDER BY executar_apos, created_at
                     FOR UPDATE SKIP LOCKED
                     LIMIT 1
                )
                UPDATE automacao_jobs j
                   SET status = 'EXECUTANDO', worker_id = $1, claimed_at = now(),
                       heartbeat_at = now(), tentativas = tentativas + 1, etapa_atual = NULL
                  FROM proximo, chamados c
                 WHERE j.id = proximo.id AND c.id = j.chamado_id
             RETURNING {_COLUNAS}, c.codigo AS chamado_codigo""",
            worker_id,
            tipos,
        )
    return _row(row)


async def admin_heartbeat(job_id: str, worker_id: str, etapa: str | None) -> bool:
    """Renova o heartbeat; ``False`` se o job não é mais deste worker (foi
    dado como morto e reatribuído, ou já terminou)."""
    async with admin_connection() as conn:
        row = await conn.fetchrow(
            """UPDATE automacao_jobs
                  SET heartbeat_at = now(), etapa_atual = $3
                WHERE id = $1::uuid AND worker_id = $2 AND status = 'EXECUTANDO'
            RETURNING id""",
            job_id,
            worker_id,
            (etapa or "")[:200] or None,
        )
    return row is not None


async def admin_finalizar(
    job_id: str,
    worker_id: str,
    *,
    status: str,
    resultado: list[dict[str, Any]],
    erro: str | None,
) -> dict[str, Any] | None:
    """Transição final vinda do worker. Só aceita se o job está EXECUTANDO
    com este ``worker_id`` (idempotência: um segundo POST de resultado não
    faz nada e devolve ``None``)."""
    async with admin_connection() as conn:
        row = await conn.fetchrow(
            f"""UPDATE automacao_jobs j
                   SET status = $3::automacao_status, resultado = $4::jsonb, erro = $5,
                       finalizado_em = now(), heartbeat_at = now(), etapa_atual = NULL
                  FROM chamados c
                 WHERE j.id = $1::uuid AND j.worker_id = $2 AND j.status = 'EXECUTANDO'
                   AND c.id = j.chamado_id
             RETURNING {_COLUNAS}, c.codigo AS chamado_codigo, c.titulo AS chamado_titulo,
                       c.cliente_id AS chamado_cliente_id, c.operador_id AS chamado_operador_id,
                       c.status::text AS chamado_status, c.departamento_id AS chamado_departamento_id""",
            job_id,
            worker_id,
            status,
            json.dumps(resultado),
            (erro or "")[:2000] or None,
        )
    return _row(row)


async def admin_gravar_mensagem(
    chamado_id: str, remetente_id: str, conteudo: str, *, interna: bool
) -> None:
    async with admin_connection() as conn:
        await conn.execute(
            "INSERT INTO mensagens (chamado_id, remetente_id, conteudo, is_interna, anexos) "
            "VALUES ($1::uuid, $2::uuid, $3, $4, '[]'::jsonb)",
            chamado_id,
            remetente_id,
            conteudo,
            interna,
        )


async def admin_resolver_chamado(chamado_id: str, ator_id: str, job_id: str) -> bool:
    """Marca RESOLVIDO (só se ainda não estava) + histórico — mesma escrita de
    ``AtendimentoRepo.alterar_status``, sem RLS porque roda fora de request."""
    async with admin_connection() as conn:
        atual = await conn.fetchval(
            "SELECT status::text FROM chamados WHERE id = $1::uuid FOR UPDATE", chamado_id
        )
        if atual is None or atual == "RESOLVIDO":
            return False
        await conn.execute(
            "UPDATE chamados SET status = 'RESOLVIDO', resolvido_em = now() WHERE id = $1::uuid",
            chamado_id,
        )
        await conn.execute(
            """INSERT INTO historico_chamados (chamado_id, ator_id, acao, detalhes)
               VALUES ($1::uuid, $2::uuid, 'STATUS_ALTERADO', $3::jsonb)""",
            chamado_id,
            ator_id,
            json.dumps({"de": atual, "para": "RESOLVIDO", "automacao_job_id": job_id}),
        )
    return True


async def admin_registrar_historico(
    chamado_id: str, ator_id: str, acao: str, detalhes: dict[str, Any]
) -> None:
    async with admin_connection() as conn:
        await conn.execute(
            """INSERT INTO historico_chamados (chamado_id, ator_id, acao, detalhes)
               VALUES ($1::uuid, $2::uuid, $3, $4::jsonb)""",
            chamado_id,
            ator_id,
            acao,
            json.dumps(detalhes),
        )


async def admin_agendar_job(
    *,
    chamado_id: str,
    tipo: str,
    payload: dict[str, Any],
    executar_apos: datetime,
    aprovado_por: str,
    reexecucao_de: str | None = None,
) -> dict[str, Any] | None:
    """Job criado pelo próprio portal (ex.: REVOGAR_LICENCA ao concluir um
    desligamento). ``None`` se já existe um ativo do mesmo tipo."""
    async with admin_connection() as conn:
        try:
            row = await conn.fetchrow(
                f"""INSERT INTO automacao_jobs
                        (chamado_id, tipo, payload, executar_apos, aprovado_por, reexecucao_de)
                    VALUES ($1::uuid, $2::automacao_tipo, $3::jsonb, $4, $5::uuid, $6::uuid)
                 RETURNING {_COLUNAS.replace('j.', '')}""",
                chamado_id,
                tipo,
                json.dumps(payload),
                executar_apos,
                aprovado_por,
                reexecucao_de,
            )
        except asyncpg.UniqueViolationError:
            return None
    return _row(row)


async def admin_feriados_entre(de: date, ate: date) -> set[date]:
    async with admin_connection() as conn:
        rows = await conn.fetch("SELECT data FROM feriados WHERE data BETWEEN $1 AND $2", de, ate)
    return {r["data"] for r in rows}


async def admin_jobs_travados(timeout_s: float) -> list[dict[str, Any]]:
    """Jobs EXECUTANDO cujo heartbeat parou há mais de ``timeout_s``."""
    async with admin_connection() as conn:
        rows = await conn.fetch(
            f"""SELECT {_COLUNAS}, c.codigo AS chamado_codigo
                  FROM automacao_jobs j JOIN chamados c ON c.id = j.chamado_id
                 WHERE j.status = 'EXECUTANDO'
                   AND COALESCE(j.heartbeat_at, j.claimed_at) < now() - ($1::float * interval '1 second')""",
            timeout_s,
        )
    return [_row(r) for r in rows]  # type: ignore[misc]


async def admin_marcar_travado(job_id: str, motivo: str, *, requeue: bool) -> dict[str, Any] | None:
    """Vigilância: job morto vira FALHOU — ou volta pra fila (``requeue``)
    mantendo ``tentativas`` (o claim incrementa de novo)."""
    async with admin_connection() as conn:
        if requeue:
            row = await conn.fetchrow(
                f"""UPDATE automacao_jobs j
                       SET status = 'NA_FILA', worker_id = NULL, claimed_at = NULL,
                           heartbeat_at = NULL, etapa_atual = NULL, erro = $2
                      FROM chamados c
                     WHERE j.id = $1::uuid AND j.status = 'EXECUTANDO' AND c.id = j.chamado_id
                 RETURNING {_COLUNAS}, c.codigo AS chamado_codigo""",
                job_id,
                motivo,
            )
        else:
            row = await conn.fetchrow(
                f"""UPDATE automacao_jobs j
                       SET status = 'FALHOU', erro = $2, finalizado_em = now(), etapa_atual = NULL
                      FROM chamados c
                     WHERE j.id = $1::uuid AND j.status = 'EXECUTANDO' AND c.id = j.chamado_id
                 RETURNING {_COLUNAS}, c.codigo AS chamado_codigo""",
                job_id,
                motivo,
            )
    return _row(row)


async def admin_fila_vencida_desde() -> datetime | None:
    """``executar_apos`` mais antigo entre os jobs NA_FILA já vencidos (ou
    ``None`` se a fila vencida está vazia) — insumo do alerta de worker mudo."""
    async with admin_connection() as conn:
        return await conn.fetchval(
            "SELECT min(executar_apos) FROM automacao_jobs WHERE status = 'NA_FILA' AND executar_apos <= now()"
        )


async def admin_contagem_fila() -> dict[str, int]:
    async with admin_connection() as conn:
        rows = await conn.fetch(
            "SELECT status::text AS status, count(*) AS n FROM automacao_jobs "
            "WHERE status IN ('NA_FILA', 'EXECUTANDO') GROUP BY status"
        )
    return {r["status"]: int(r["n"]) for r in rows}


def agora_utc() -> datetime:
    return datetime.now(UTC)
