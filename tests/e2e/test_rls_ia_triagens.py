"""RLS de `ia_triagens` (migration 0050 — F0 da frente de IA) contra Postgres real.

Contrato (plano_md_mestre_IA.md, Seção 4.1): leitura só para staff no escopo do
departamento de DESTINO do chamado; o AUTOR nunca lê; escrita só pela conexão
administrativa (nenhuma policy de INSERT para `authenticated`). O UNIQUE
(chamado_id, rodada, passe) materializa a idempotência sob retry.
"""

from __future__ import annotations

import asyncpg
import pytest

from tests.e2e.conftest import Seed, as_user

pytestmark = pytest.mark.rls


async def _seed_triagem(conn: asyncpg.Connection, chamado_id) -> int:
    """Insere uma triagem como superusuário — equivalente ao admin_connection()."""
    return await conn.fetchval(
        "INSERT INTO ia_triagens (chamado_id, rodada, passe, acao, modelo) "
        "VALUES ($1, 1, 'UNICO', 'NOTA_INTERNA', 'gpt-5.4-mini') RETURNING id",
        chamado_id,
    )


async def test_staff_do_departamento_le_triagem_do_seu_escopo(
    conn: asyncpg.Connection, seed: Seed
):
    triagem_id = await _seed_triagem(conn, seed.chamado_ti)
    async with as_user(conn, seed.staff_ti):
        ids = {r["id"] for r in await conn.fetch("SELECT id FROM ia_triagens")}
    assert triagem_id in ids


async def test_staff_de_outro_departamento_nao_le(conn: asyncpg.Connection, seed: Seed):
    await _seed_triagem(conn, seed.chamado_ti)
    async with as_user(conn, seed.staff_rh):
        ids = {r["id"] for r in await conn.fetch("SELECT id FROM ia_triagens")}
    assert ids == set()


async def test_autor_do_chamado_nao_le_triagem(conn: asyncpg.Connection, seed: Seed):
    """A triagem é material interno de atendimento — o autor (CLIENTE) nunca vê,
    mesmo sendo dono do chamado (DoD da F0)."""
    await _seed_triagem(conn, seed.chamado_ti)
    async with as_user(conn, seed.autor_ti):
        ids = {r["id"] for r in await conn.fetch("SELECT id FROM ia_triagens")}
    assert ids == set()


async def test_authenticated_nao_escreve_ia_triagens(conn: asyncpg.Connection, seed: Seed):
    """Sem policy de INSERT: nem o staff do próprio departamento grava triagem —
    escrita é exclusiva da conexão administrativa (Seção 4.1)."""
    async with as_user(conn, seed.staff_ti):
        with pytest.raises(asyncpg.InsufficientPrivilegeError):
            async with conn.transaction():  # savepoint: o erro não aborta o teste
                await _seed_triagem(conn, seed.chamado_ti)


async def test_unique_impede_duplicar_rodada_passe(conn: asyncpg.Connection, seed: Seed):
    """Idempotência sob retry (Regra de Ouro do fluxo): a mesma (rodada, passe)
    não entra duas vezes para o mesmo chamado."""
    await _seed_triagem(conn, seed.chamado_ti)
    with pytest.raises(asyncpg.UniqueViolationError):
        async with conn.transaction():
            await _seed_triagem(conn, seed.chamado_ti)
