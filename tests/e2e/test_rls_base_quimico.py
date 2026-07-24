"""e2e RLS da base sigilosa do Químico (F4 — migration 0054, marker ``rls``).

Prova com Postgres REAL as garantias estruturais da Seção 3/C7 do plano IA:

1. O role ``ia_worker`` LÊ as tabelas liberadas (produtos, matérias-primas,
   playbooks, fichas) — GRANT + policy dedicada.
2. ``SELECT`` em ``base_quimico_formulacoes`` com o role ``ia_worker`` FALHA
   com erro de permissão (sem GRANT — não é disciplina de código, é o banco).
3. Usuário ``authenticated`` (claims de cliente/staff) não enxerga NENHUMA
   linha da base (RLS habilitada, zero policies para papéis de usuário).

Requer a migration ``0054_base_quimico.sql`` aplicada no Supabase local; sem
ela, os testes são pulados (skip) com motivo explícito.
"""

from __future__ import annotations

import asyncpg
import pytest

pytestmark = pytest.mark.rls

# Dados SINTÉTICOS — nenhum dado real da base entra no repositório.
_CHAVE = "E2E|S0001|PRODUTO E2E|L1|001"


async def _migration_aplicada(conn: asyncpg.Connection) -> bool:
    return bool(
        await conn.fetchval("SELECT to_regclass('public.base_quimico_formulacoes') IS NOT NULL")
    ) and bool(await conn.fetchval("SELECT 1 FROM pg_roles WHERE rolname = 'ia_worker'"))


async def _seed_base(conn: asyncpg.Connection) -> None:
    await conn.execute(
        """
        INSERT INTO base_quimico_produtos (chave_produto, nome, aplicacao, componentes)
        VALUES ($1, 'PRODUTO E2E', 'aplicação sintética de teste', '["COMPONENTE E2E"]'::jsonb)
        ON CONFLICT (chave_produto) DO NOTHING
        """,
        _CHAVE,
    )
    await conn.execute(
        """
        INSERT INTO base_quimico_fichas (chave_produto, conteudo)
        VALUES ($1, 'FICHA E2E — conteúdo sintético') ON CONFLICT (chave_produto) DO NOTHING
        """,
        _CHAVE,
    )
    await conn.execute(
        """
        INSERT INTO base_quimico_formulacoes (chave_produto, ordem, componente, quantidade)
        VALUES ($1, 1, 'COMPONENTE E2E', 42.0) ON CONFLICT (chave_produto, ordem) DO NOTHING
        """,
        _CHAVE,
    )


async def test_ia_worker_le_o_permitido_e_formulacoes_da_erro_de_permissao(conn):
    if not await _migration_aplicada(conn):
        pytest.skip("migration 0054_base_quimico não aplicada no Supabase local")
    await _seed_base(conn)

    await conn.execute("SET LOCAL ROLE ia_worker")
    # 1) Tabelas liberadas: GRANT + policy dedicada ⇒ leitura funciona.
    assert (
        await conn.fetchval(
            "SELECT count(*) FROM base_quimico_produtos WHERE chave_produto = $1", _CHAVE
        )
        == 1
    )
    assert (
        await conn.fetchval(
            "SELECT count(*) FROM base_quimico_fichas WHERE chave_produto = $1", _CHAVE
        )
        == 1
    )
    # 2) Quantidades: sem GRANT ⇒ ERRO de permissão (C7 — garantia de banco).
    # SAVEPOINT (conn.transaction() aninhado) porque qualquer erro do Postgres aborta a
    # transação inteira, não só o statement — mesmo padrão de tests/e2e/test_rls_matrix.py
    # (commit ba6991d).
    with pytest.raises(asyncpg.exceptions.InsufficientPrivilegeError):
        async with conn.transaction():
            await conn.fetchval("SELECT count(*) FROM base_quimico_formulacoes")


async def test_usuario_autenticado_nao_enxerga_nenhuma_linha_da_base(conn, seed):
    if not await _migration_aplicada(conn):
        pytest.skip("migration 0054_base_quimico não aplicada no Supabase local")
    await _seed_base(conn)

    from tests.e2e.conftest import as_user

    async with as_user(conn, seed.staff_ti):
        # RLS habilitada + zero policies para papéis de usuário ⇒ 0 linhas em
        # todas as tabelas da base (mesmo para o staff mais privilegiado).
        for tabela in (
            "base_quimico_produtos",
            "base_quimico_materias_primas",
            "base_quimico_playbooks",
            "base_quimico_fichas",
            "base_quimico_formulacoes",
        ):
            try:
                # SAVEPOINT por tabela: sem isso, o InsufficientPrivilegeError da 1ª
                # tabela sem GRANT aborta a transação inteira e a 2ª tabela (mesmo
                # que também deva dar erro) quebra com InFailedSQLTransactionError
                # em vez do erro esperado — mesmo padrão de test_rls_matrix.py
                # (commit ba6991d).
                async with conn.transaction():
                    linhas = await conn.fetchval(f"SELECT count(*) FROM {tabela}")  # noqa: S608
            except asyncpg.exceptions.InsufficientPrivilegeError:
                continue  # sem GRANT também serve — inacessível de qualquer jeito
            assert linhas == 0, f"{tabela} visível para usuário autenticado"
