"""e2e do status automático RESPOSTA_CLIENTE (migrations 0061 + 0072, marker ``rls``).

Só o banco prova isto: a 0061 é um trigger SECURITY DEFINER que dispara um
UPDATE em `chamados` de dentro do INSERT em `mensagens`, e esse UPDATE ainda
atravessa a trava de coluna do CLIENTE (`enforce_cliente_so_avaliacao`, 0006/
0059/0072). Com `FakeRepo` a resposta do autor "passa" sempre — em produção ela
quebrava com 500 para todo chamado de TI/RH já em atendimento (2026-08-03,
BOND-2026-00645).
"""

from __future__ import annotations

from contextlib import asynccontextmanager

import asyncpg
import pytest

from tests.e2e.conftest import Seed, as_user

pytestmark = pytest.mark.rls


async def _migration_aplicada(conn: asyncpg.Connection) -> bool:
    """A 0072 é um CREATE OR REPLACE da função da 0006 — detecta pelo ramo novo."""
    fonte = await conn.fetchval(
        "SELECT prosrc FROM pg_proc WHERE proname = 'enforce_cliente_so_avaliacao'"
    )
    return bool(fonte) and "RESPOSTA_CLIENTE" in fonte


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


async def _status(conn: asyncpg.Connection, chamado_id) -> str:
    return await conn.fetchval("SELECT status::text FROM chamados WHERE id = $1", chamado_id)


async def _preparar(conn: asyncpg.Connection, seed: Seed, status: str) -> None:
    await conn.execute(
        "UPDATE chamados SET status = $2::status_chamado, operador_id = $3 WHERE id = $1",
        seed.chamado_ti,
        status,
        seed.staff_ti,
    )


@pytest.mark.parametrize("status_inicial", ["EM_ATENDIMENTO", "AGUARDANDO"])
async def test_autor_responde_no_chat_e_status_vai_para_resposta_cliente(
    conn: asyncpg.Connection, seed: Seed, status_inicial: str
):
    if not await _migration_aplicada(conn):
        pytest.skip("migration 0072_fix_resposta_cliente_trava_coluna não aplicada")

    await _preparar(conn, seed, status_inicial)

    async with as_user(conn, seed.autor_ti) as c:
        await c.execute(
            "INSERT INTO mensagens (chamado_id, remetente_id, conteudo, is_interna) "
            "VALUES ($1, $2, 'segue o print que voces pediram', false)",
            seed.chamado_ti,
            seed.autor_ti,
        )

    assert await _status(conn, seed.chamado_ti) == "RESPOSTA_CLIENTE"
    assert await conn.fetchval(
        "SELECT count(*) FROM historico_chamados "
        "WHERE chamado_id = $1 AND detalhes->>'motivo' = 'mensagem_autor'",
        seed.chamado_ti,
    ) == 1


async def test_resposta_do_staff_devolve_o_chamado_para_em_atendimento(
    conn: asyncpg.Connection, seed: Seed
):
    if not await _migration_aplicada(conn):
        pytest.skip("migration 0072_fix_resposta_cliente_trava_coluna não aplicada")

    await _preparar(conn, seed, "RESPOSTA_CLIENTE")

    async with as_user(conn, seed.staff_ti) as c:
        await c.execute(
            "INSERT INTO mensagens (chamado_id, remetente_id, conteudo, is_interna) "
            "VALUES ($1, $2, 'recebido, ja estou olhando', false)",
            seed.chamado_ti,
            seed.staff_ti,
        )

    assert await _status(conn, seed.chamado_ti) == "EM_ATENDIMENTO"


async def test_autor_continua_sem_poder_mexer_nas_demais_colunas(
    conn: asyncpg.Connection, seed: Seed
):
    """O ramo novo da 0072 libera SÓ a transição de status — a trava de coluna
    (o que a 0006 existe para garantir) segue valendo na avaliação."""
    if not await _migration_aplicada(conn):
        pytest.skip("migration 0072_fix_resposta_cliente_trava_coluna não aplicada")

    await conn.execute(
        "UPDATE chamados SET status = 'RESOLVIDO', resolvido_em = now() WHERE id = $1",
        seed.chamado_ti,
    )

    async with as_user(conn, seed.autor_ti) as c:
        async with savepoint(conn):
            with pytest.raises(asyncpg.RaiseError, match="só pode alterar a avaliação"):
                await c.execute(
                    "UPDATE chamados SET avaliacao_nota = 5, avaliacao_em = now(), "
                    "prioridade = 'URGENTE' WHERE id = $1",
                    seed.chamado_ti,
                )

        await c.execute(
            "UPDATE chamados SET avaliacao_nota = 5, avaliacao_em = now() WHERE id = $1",
            seed.chamado_ti,
        )

    assert await conn.fetchval(
        "SELECT avaliacao_nota FROM chamados WHERE id = $1", seed.chamado_ti
    ) == 5
