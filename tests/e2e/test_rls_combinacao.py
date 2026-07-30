"""e2e da combinação de chamados (migration 0065, marker ``rls``).

O que só existe no BANCO e por isso não dá para provar com `FakeRepo`:

1. Os guarda-corpos de integridade do trigger ``enforce_combinacao_chamados``
   (sem corrente, mesmo departamento, sem auto-referência) — o Python checa os
   mesmos casos para dar mensagem, mas quem *garante* é o Postgres.
2. **A parte de segurança:** a trava de coluna do CLIENTE
   (``enforce_cliente_so_avaliacao``) é uma lista explícita de colunas, então
   toda coluna nova nasce liberada para o autor até ser adicionada à lista. Sem
   isso, um funcionário poderia apontar o próprio chamado resolvido para
   qualquer chamado do setor — e, como a rota coloca o autor do duplicado em
   cópia no principal, se auto-conceder leitura de um chamado de terceiros.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

import asyncpg
import pytest

from tests.e2e.conftest import Seed, as_user

pytestmark = pytest.mark.rls


async def _migration_aplicada(conn: asyncpg.Connection) -> bool:
    return bool(
        await conn.fetchval(
            "SELECT to_regprocedure('public.enforce_combinacao_chamados()') IS NOT NULL"
        )
    )


async def _principal_de(conn: asyncpg.Connection, chamado_id) -> object | None:
    return await conn.fetchval(
        "SELECT chamado_principal_id FROM chamados WHERE id = $1", chamado_id
    )


@asynccontextmanager
async def savepoint(conn: asyncpg.Connection):
    """Erro do Postgres aborta a transação INTEIRA, não só o statement — sem um
    SAVEPOINT (``conn.transaction()`` aninhado) o teste não consegue continuar
    depois de um `pytest.raises`. Mesmo padrão de `tests/e2e/test_rls_matrix.py`.
    """
    tx = conn.transaction()
    await tx.start()
    try:
        yield
    finally:
        await tx.rollback()


async def test_staff_do_setor_combina_e_o_trigger_carimba_autoria(
    conn: asyncpg.Connection, seed: Seed
):
    if not await _migration_aplicada(conn):
        pytest.skip("migration 0065_combinacao_chamados não aplicada no Supabase local")

    async with as_user(conn, seed.staff_ti) as c:
        await c.execute(
            "UPDATE chamados SET chamado_principal_id = $2 WHERE id = $1",
            seed.chamado_ti,
            seed.chamado_ti_auto,
        )

    linha = await conn.fetchrow(
        "SELECT chamado_principal_id, combinado_em, combinado_por FROM chamados WHERE id = $1",
        seed.chamado_ti,
    )
    assert linha["chamado_principal_id"] == seed.chamado_ti_auto
    # Carimbo é do banco, não do app: quem combinou não pode ser forjado no POST.
    assert linha["combinado_em"] is not None
    assert linha["combinado_por"] == seed.staff_ti


async def test_desfazer_limpa_o_carimbo(conn: asyncpg.Connection, seed: Seed):
    if not await _migration_aplicada(conn):
        pytest.skip("migration 0065_combinacao_chamados não aplicada no Supabase local")

    async with as_user(conn, seed.staff_ti) as c:
        await c.execute(
            "UPDATE chamados SET chamado_principal_id = $2 WHERE id = $1",
            seed.chamado_ti, seed.chamado_ti_auto,
        )
        await c.execute(
            "UPDATE chamados SET chamado_principal_id = NULL WHERE id = $1", seed.chamado_ti
        )

    linha = await conn.fetchrow(
        "SELECT chamado_principal_id, combinado_em, combinado_por FROM chamados WHERE id = $1",
        seed.chamado_ti,
    )
    assert linha["chamado_principal_id"] is None
    assert linha["combinado_em"] is None and linha["combinado_por"] is None


async def test_nao_combina_entre_departamentos(conn: asyncpg.Connection, seed: Seed):
    """Combinar um chamado do RH dentro de um do TI moveria observadores para um
    chamado fora do alcance de quem combinou — e furaria o escopo de setor."""
    if not await _migration_aplicada(conn):
        pytest.skip("migration 0065_combinacao_chamados não aplicada no Supabase local")

    async with savepoint(conn):
        with pytest.raises(asyncpg.RaiseError, match="mesmo departamento"):
            await conn.execute(
                "UPDATE chamados SET chamado_principal_id = $2 WHERE id = $1",
                seed.chamado_rh,
                seed.chamado_ti,
            )


async def test_nao_forma_corrente_de_combinacoes(conn: asyncpg.Connection, seed: Seed):
    """A -> B já combinado em C: recusado nas duas direções (nem B vira
    principal de A, nem A aponta para B)."""
    if not await _migration_aplicada(conn):
        pytest.skip("migration 0065_combinacao_chamados não aplicada no Supabase local")

    dup = await conn.fetchval(
        """INSERT INTO chamados (empresa_id, cliente_id, departamento_id, titulo, descricao,
                                 telefone_contato)
           VALUES ($1, $2, $3, 'terceiro chamado', 'x', '(51) 3333-4444') RETURNING id""",
        seed.empresa_id, seed.autor_ti, seed.dept_ti,
    )
    await conn.execute(
        "UPDATE chamados SET chamado_principal_id = $2 WHERE id = $1",
        seed.chamado_ti, seed.chamado_ti_auto,
    )

    # chamado_ti já é duplicado -> não pode ser principal de ninguém.
    async with savepoint(conn):
        with pytest.raises(asyncpg.RaiseError, match="duplicado de outro"):
            await conn.execute(
                "UPDATE chamados SET chamado_principal_id = $2 WHERE id = $1", dup, seed.chamado_ti
            )
    # ...e um chamado que já é principal não pode virar duplicado.
    async with savepoint(conn):
        with pytest.raises(asyncpg.RaiseError, match="principal de outros"):
            await conn.execute(
                "UPDATE chamados SET chamado_principal_id = $2 WHERE id = $1",
                seed.chamado_ti_auto, dup,
            )


async def test_chamado_nao_combina_consigo_mesmo(conn: asyncpg.Connection, seed: Seed):
    if not await _migration_aplicada(conn):
        pytest.skip("migration 0065_combinacao_chamados não aplicada no Supabase local")

    async with savepoint(conn):
        with pytest.raises(asyncpg.IntegrityConstraintViolationError):
            await conn.execute(
                "UPDATE chamados SET chamado_principal_id = $1 WHERE id = $1", seed.chamado_ti
            )


async def test_cliente_nao_consegue_combinar_o_proprio_chamado(
    conn: asyncpg.Connection, seed: Seed
):
    """O ponto sensível da 0065 (ver docstring do módulo): o autor tem UPDATE
    permitido no próprio chamado RESOLVIDO (avaliação/reabertura) — a coluna
    nova NÃO pode viajar junto."""
    if not await _migration_aplicada(conn):
        pytest.skip("migration 0065_combinacao_chamados não aplicada no Supabase local")

    await conn.execute(
        "UPDATE chamados SET status = 'RESOLVIDO', resolvido_em = now() WHERE id = $1",
        seed.chamado_ti,
    )

    # `as_user` por FORA do savepoint: o `RESET ROLE` de saída dele rodaria numa
    # transação abortada se a ordem fosse a inversa.
    async with as_user(conn, seed.autor_ti) as c:
        async with savepoint(conn):
            with pytest.raises(asyncpg.PostgresError):
                await c.execute(
                    "UPDATE chamados SET chamado_principal_id = $2 WHERE id = $1",
                    seed.chamado_ti,
                    seed.chamado_ti_auto,
                )

    assert await _principal_de(conn, seed.chamado_ti) is None
