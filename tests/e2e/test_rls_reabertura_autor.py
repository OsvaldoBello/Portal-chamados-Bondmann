"""e2e da reabertura pelo autor (migrations 0059 + 0090, marker ``rls``).

Só o banco prova isto: a regra mora em RLS (`chamados_update_cliente_reabertura`)
e no trigger `enforce_cliente_so_avaliacao`. Com `FakeRepo` a reabertura "passa"
para qualquer um — em produção ela quebrava com 500 sempre que o AUTOR não tinha
papel `CLIENTE` (2026-08-28, BD-2026-00717: líder da Controladoria reabrindo um
chamado do TI). O USING da policy irmã da avaliação deixa a linha entrar no
UPDATE e nenhum WITH CHECK a aceita — isso é ERRO no Postgres (42501), não
"0 linhas".
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager

import asyncpg
import pytest

from tests.e2e.conftest import Seed, _criar_chamado, as_user, rowcount

pytestmark = pytest.mark.rls


async def _migration_aplicada(conn: asyncpg.Connection) -> bool:
    """A 0090 é um CREATE OR REPLACE da função da 0006 — detecta pelo ramo novo."""
    fonte = await conn.fetchval(
        "SELECT prosrc FROM pg_proc WHERE proname = 'enforce_cliente_so_avaliacao'"
    )
    return bool(fonte) and "v_como_autor" in fonte


@asynccontextmanager
async def savepoint(conn: asyncpg.Connection):
    """Erro do Postgres aborta a transação inteira — sem SAVEPOINT o teste não
    continua depois de um `pytest.raises` (mesmo padrão de `test_rls_matrix.py`)."""
    tx = conn.transaction()
    await tx.start()
    try:
        yield
    finally:
        await tx.rollback()


async def _resolver(conn: asyncpg.Connection, chamado_id: uuid.UUID) -> None:
    await conn.execute(
        "UPDATE chamados SET status = 'RESOLVIDO', resolvido_em = now() WHERE id = $1",
        chamado_id,
    )


async def _reabrir(conn: asyncpg.Connection, chamado_id: uuid.UUID) -> str:
    """Exatamente o UPDATE de `AtendimentoRepo.reabrir`."""
    return await conn.execute(
        """
        UPDATE chamados
           SET status = 'EM_ATENDIMENTO'::status_chamado,
               resolvido_em = NULL,
               avaliacao_nota = NULL,
               avaliacao_comentario = NULL,
               avaliacao_em = NULL
         WHERE id = $1 AND status = 'RESOLVIDO'::status_chamado
        """,
        chamado_id,
    )


async def _status(conn: asyncpg.Connection, chamado_id: uuid.UUID) -> str:
    return await conn.fetchval("SELECT status::text FROM chamados WHERE id = $1", chamado_id)


@pytest.fixture
async def chamado_do_lider(conn: asyncpg.Connection, seed: Seed) -> uuid.UUID:
    """Chamado ABERTO pelo líder do Comercial (papel ADMIN, setor sem fila) para o
    RH — o formato exato do caso de produção: autor que não é `CLIENTE` e cujo
    setor não é o destino, então nem a policy de staff o alcança."""
    chamado_id = await _criar_chamado(
        conn,
        empresa_id=seed.empresa_id,
        cliente_id=seed.lider_comercial,
        departamento_id=seed.dept_rh,
        titulo="Chamado aberto pelo lider do Comercial",
    )
    await _resolver(conn, chamado_id)
    return chamado_id


async def test_autor_admin_de_outro_setor_reabre(
    conn: asyncpg.Connection, seed: Seed, chamado_do_lider: uuid.UUID
):
    """O bug de produção: antes da 0090 este UPDATE levantava
    `new row violates row-level security policy` (42501) -> 500 na tela."""
    if not await _migration_aplicada(conn):
        pytest.skip("migration 0090_reabertura_autor_sem_papel_cliente não aplicada")

    async with as_user(conn, seed.lider_comercial) as c:
        assert rowcount(await _reabrir(c, chamado_do_lider)) == 1

    assert await _status(conn, chamado_do_lider) == "EM_ATENDIMENTO"


async def test_autor_com_papel_cliente_continua_reabrindo(conn: asyncpg.Connection, seed: Seed):
    """Caminho original da 0059 — a 0090 amplia o alcance, não o troca."""
    if not await _migration_aplicada(conn):
        pytest.skip("migration 0090_reabertura_autor_sem_papel_cliente não aplicada")

    await _resolver(conn, seed.chamado_ti)

    async with as_user(conn, seed.autor_ti) as c:
        assert rowcount(await _reabrir(c, seed.chamado_ti)) == 1

    assert await _status(conn, seed.chamado_ti) == "EM_ATENDIMENTO"


async def test_quem_nao_e_autor_nao_reabre(
    conn: asyncpg.Connection, seed: Seed, chamado_do_lider: uuid.UUID
):
    """O autor do chamado é o líder; um funcionário qualquer não move nada — a
    policy nova trocou a checagem de PAPEL por autoria, não a removeu."""
    if not await _migration_aplicada(conn):
        pytest.skip("migration 0090_reabertura_autor_sem_papel_cliente não aplicada")

    async with as_user(conn, seed.autor_comercial) as c:
        assert rowcount(await _reabrir(c, chamado_do_lider)) == 0

    assert await _status(conn, chamado_do_lider) == "RESOLVIDO"


async def test_reabertura_do_autor_nao_libera_outras_colunas(
    conn: asyncpg.Connection, seed: Seed, chamado_do_lider: uuid.UUID
):
    """A trava de coluna (0006) passa a valer para o autor de qualquer papel:
    sem isso, a policy nova viraria uma porta para reescrever o próprio chamado
    (prioridade, operador, título) junto com a reabertura."""
    if not await _migration_aplicada(conn):
        pytest.skip("migration 0090_reabertura_autor_sem_papel_cliente não aplicada")

    async with as_user(conn, seed.lider_comercial) as c:
        async with savepoint(conn):
            with pytest.raises(asyncpg.RaiseError, match="reabri-lo"):
                await c.execute(
                    "UPDATE chamados SET status = 'EM_ATENDIMENTO', resolvido_em = NULL, "
                    "prioridade = 'URGENTE' WHERE id = $1",
                    chamado_do_lider,
                )

        assert rowcount(await _reabrir(c, chamado_do_lider)) == 1


async def test_staff_do_setor_de_destino_fica_fora_da_trava(conn: asyncpg.Connection, seed: Seed):
    """Contraprova do critério novo: quem atende o próprio setor (autoatendimento
    de Marketing/RH/TI, 0038/0042/0047) continua mexendo nas colunas de gestão do
    chamado que ele mesmo abriu — a trava é sobre agir como AUTOR, não sobre ser
    dono da linha."""
    if not await _migration_aplicada(conn):
        pytest.skip("migration 0090_reabertura_autor_sem_papel_cliente não aplicada")

    async with as_user(conn, seed.staff_marketing) as c:
        status = await c.execute(
            "UPDATE chamados SET prioridade = 'URGENTE', operador_id = $1 WHERE id = $2",
            seed.staff_marketing,
            seed.chamado_marketing_auto,
        )
    assert rowcount(status) == 1
