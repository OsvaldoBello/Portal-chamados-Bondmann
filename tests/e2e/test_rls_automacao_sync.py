"""e2e da sincronização de liderança (migrations 0092 + 0093, marker ``rls``).

Sync não tem chamado: as policies da 0091 (que exigem chamado do setor) devem
escondê-la do staff e impedir que a tela crie uma; o CHECK garante que só a
sync fica sem chamado; o índice garante uma sync ativa por vez."""
from __future__ import annotations

import json
from contextlib import asynccontextmanager

import asyncpg
import pytest

from tests.e2e.conftest import Seed, as_user

pytestmark = pytest.mark.rls


async def _aplicada(conn: asyncpg.Connection) -> bool:
    return bool(await conn.fetchval("SELECT to_regclass('public.automacao_lideranca_gerenciada') IS NOT NULL"))


@asynccontextmanager
async def savepoint(conn: asyncpg.Connection):
    tx = conn.transaction()
    await tx.start()
    try:
        yield
    finally:
        await tx.rollback()


async def _sync(conn: asyncpg.Connection) -> str:
    return await conn.fetchval(
        "INSERT INTO automacao_jobs (tipo, payload) VALUES ('SINCRONIZAR_LIDERANCA', $1::jsonb) RETURNING id",
        json.dumps({"modo": "relatorio"}),
    )


async def test_sync_sem_chamado_e_invisivel_para_o_staff(conn: asyncpg.Connection, seed: Seed):
    if not await _aplicada(conn):
        pytest.skip("0093 não aplicada neste banco")
    job_id = await _sync(conn)
    async with as_user(conn, seed.staff_ti) as c:
        assert await c.fetchval("SELECT count(*) FROM automacao_jobs WHERE id = $1", job_id) == 0


async def test_staff_nao_cria_sync_pela_tela(conn: asyncpg.Connection, seed: Seed):
    if not await _aplicada(conn):
        pytest.skip("0093 não aplicada neste banco")
    async with as_user(conn, seed.staff_ti) as c:
        async with savepoint(c):
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await c.execute(
                    "INSERT INTO automacao_jobs (tipo, payload, aprovado_por) VALUES ('SINCRONIZAR_LIDERANCA', '{}'::jsonb, $1)",
                    seed.staff_ti,
                )


async def test_so_a_sync_fica_sem_chamado(conn: asyncpg.Connection, seed: Seed):
    if not await _aplicada(conn):
        pytest.skip("0093 não aplicada neste banco")
    async with savepoint(conn):
        with pytest.raises(asyncpg.CheckViolationError):
            await conn.execute("INSERT INTO automacao_jobs (tipo, payload) VALUES ('CRIACAO', '{}'::jsonb)")


async def test_uma_sync_ativa_por_vez(conn: asyncpg.Connection, seed: Seed):
    if not await _aplicada(conn):
        pytest.skip("0093 não aplicada neste banco")
    await _sync(conn)
    async with savepoint(conn):
        with pytest.raises(asyncpg.UniqueViolationError):
            await _sync(conn)


async def test_gerenciados_so_pela_conexao_administrativa(conn: asyncpg.Connection, seed: Seed):
    if not await _aplicada(conn):
        pytest.skip("0093 não aplicada neste banco")
    async with as_user(conn, seed.staff_ti) as c:
        async with savepoint(c):
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await c.fetch("SELECT email FROM automacao_lideranca_gerenciada")
