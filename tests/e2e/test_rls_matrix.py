"""Suíte e2e de RLS (Sprint 1 / item 1.7, M9) — matriz de visibilidade contra Supabase local real.

Cobre a matriz descrita em `plano_melhorias_auditoria.md` (item 1.7): autor · staff
RH/Marketing · líder de setor (0028) · TI pós-0020 · autoatendimento generalizado a
todos os setores (0038/0042/0047) · nota interna invisível ao autor · upload de avatar
(1º envio e reenvio, regressão 0037) · Realtime não entrega `is_interna` ao cliente.

Cada teste roda contra o Postgres real com as policies de RLS aplicadas — não contra
`FakeRepo`/mocks — para pegar exatamente a classe de bug que motivou este item: mock
verde com o comportamento real do banco divergente (caso 0028/chamados_departamento).
"""

from __future__ import annotations

import asyncpg
import pytest

from tests.e2e.conftest import Seed, as_user, rowcount

pytestmark = pytest.mark.rls


# ============================================================
# Autor
# ============================================================
async def test_autor_ve_apenas_os_proprios_chamados(conn: asyncpg.Connection, seed: Seed):
    async with as_user(conn, seed.autor_ti):
        ids = {r["id"] for r in await conn.fetch("SELECT id FROM chamados")}
    assert ids == {seed.chamado_ti}


# ============================================================
# Staff RH / Marketing — visibilidade restrita ao próprio setor
# ============================================================
async def test_staff_rh_ve_fila_do_setor_mas_nao_de_outro_setor(conn: asyncpg.Connection, seed: Seed):
    async with as_user(conn, seed.staff_rh):
        ids = {r["id"] for r in await conn.fetch("SELECT id FROM chamados")}
    assert seed.chamado_rh in ids
    assert seed.chamado_rh_auto in ids  # autor do próprio setor RH
    assert seed.chamado_ti not in ids
    assert seed.chamado_marketing_auto not in ids


async def test_staff_marketing_ve_fila_do_setor_mas_nao_de_outro_setor(
    conn: asyncpg.Connection, seed: Seed
):
    async with as_user(conn, seed.staff_marketing):
        ids = {r["id"] for r in await conn.fetch("SELECT id FROM chamados")}
    assert seed.chamado_marketing_auto in ids
    assert seed.chamado_ti not in ids
    assert seed.chamado_rh not in ids


# ============================================================
# Líder de setor (0028) — acompanha (só leitura) os chamados abertos pela
# própria equipe, mesmo num setor sem fila de atendimento (Comercial).
# ============================================================
async def test_lider_de_setor_acompanha_chamados_da_equipe_mesmo_sem_fila(
    conn: asyncpg.Connection, seed: Seed
):
    async with as_user(conn, seed.lider_comercial):
        ids = {r["id"] for r in await conn.fetch("SELECT id FROM chamados")}
    # Vê o chamado aberto por um funcionário do PRÓPRIO setor (Comercial), mesmo o
    # chamado sendo destinado a outro departamento (RH) — é o autor que importa, não
    # o destino (0028).
    assert seed.chamado_rh in ids
    # Não vê chamados de gente de fora do Comercial.
    assert seed.chamado_ti not in ids


async def test_lider_de_setor_nao_atende_so_acompanha(conn: asyncpg.Connection, seed: Seed):
    """0028 é leitura, não atendimento: Comercial não tem fila, então o líder não pode
    UPDATE um chamado que só enxerga por ser autor da própria equipe (RLS de UPDATE
    continua exigindo que o DESTINO do chamado seja o setor do staff)."""
    async with as_user(conn, seed.lider_comercial):
        status = await conn.execute(
            "UPDATE chamados SET prioridade = 'URGENTE' WHERE id = $1", seed.chamado_rh
        )
    assert rowcount(status) == 0


# ============================================================
# TI pós-0020 — deixou de ter "acesso total" de VISIBILIDADE; só vê o próprio setor.
# ============================================================
async def test_ti_pos_0020_nao_ve_chamados_de_outros_setores(conn: asyncpg.Connection, seed: Seed):
    async with as_user(conn, seed.staff_ti):
        ids = {r["id"] for r in await conn.fetch("SELECT id FROM chamados")}
    assert seed.chamado_ti in ids
    assert seed.chamado_ti_auto in ids  # também é do setor TI
    assert seed.chamado_rh not in ids
    assert seed.chamado_marketing_auto not in ids


# ============================================================
# Exceções de autoatendimento — generalizada a TODOS os departamentos pela 0047
# (originalmente só Marketing/0038 e RH/0042, via coluna
# `departamentos.autoatendimento`): o autor pode assumir o próprio chamado quando
# o setor de destino é o mesmo do autor. Decisão de produto 2026-07-20: TI (e
# qualquer outro setor) precisa poder abrir chamado pra si mesmo e vê-lo aparecer
# no Kanban/fila — ex.: a diretoria pede pro TI abrir uma demanda em seu nome, sem
# precisar logar na plataforma. Sem essa exceção não há mais nenhum setor onde a
# trava geral de segregação de função (0029) se aplique sozinha.
# ============================================================
async def test_exececao_marketing_autoatendimento(conn: asyncpg.Connection, seed: Seed):
    async with as_user(conn, seed.staff_marketing):
        status = await conn.execute(
            "UPDATE chamados SET operador_id = $1 WHERE id = $2",
            seed.staff_marketing,
            seed.chamado_marketing_auto,
        )
    assert rowcount(status) == 1


async def test_excecao_rh_autoatendimento(conn: asyncpg.Connection, seed: Seed):
    async with as_user(conn, seed.staff_rh):
        status = await conn.execute(
            "UPDATE chamados SET operador_id = $1 WHERE id = $2",
            seed.staff_rh,
            seed.chamado_rh_auto,
        )
    assert rowcount(status) == 1


async def test_excecao_ti_autoatendimento(conn: asyncpg.Connection, seed: Seed):
    """0047 generaliza a exceção pro TI também — mesmo comportamento de
    Marketing/RH acima, TI deixa de ser bloqueado pela segregação de função
    (0029) quando é autor E destino do próprio chamado."""
    async with as_user(conn, seed.staff_ti):
        status = await conn.execute(
            "UPDATE chamados SET operador_id = $1 WHERE id = $2",
            seed.staff_ti,
            seed.chamado_ti_auto,
        )
    assert rowcount(status) == 1


# ============================================================
# Nota interna invisível ao autor (e, por construção, ao Realtime — ver docstring).
# ============================================================
async def test_nota_interna_invisivel_ao_autor_mas_visivel_ao_staff(
    conn: asyncpg.Connection, seed: Seed
):
    """`mensagens_select` é a MESMA policy usada pelo Realtime do Supabase para
    decidir, por assinante, se um evento `postgres_changes` é entregue (a replicação
    aplica a RLS da tabela como o usuário conectado antes de fazer o broadcast). Ou
    seja: provar aqui que a policy nega `is_interna = true` ao autor via SELECT direto
    é a MESMA garantia de que o Realtime não entrega esse evento ao cliente — não há
    um mecanismo de filtragem separado para o canal Realtime que precise de um teste
    à parte (WebSocket) além deste."""
    async with as_user(conn, seed.autor_ti):
        ids = {r["id"] for r in await conn.fetch("SELECT id FROM mensagens WHERE chamado_id = $1", seed.chamado_ti)}
    assert ids == {seed.msg_publica}
    assert seed.msg_interna not in ids

    async with as_user(conn, seed.staff_ti):
        ids = {r["id"] for r in await conn.fetch("SELECT id FROM mensagens WHERE chamado_id = $1", seed.chamado_ti)}
    assert ids == {seed.msg_publica, seed.msg_interna}


# ============================================================
# Upload de avatar — 1º envio (INSERT) e reenvio (UPDATE, regressão da 0037).
# ============================================================
async def test_upload_avatar_primeiro_envio_e_reenvio(conn: asyncpg.Connection, seed: Seed):
    path = f"{seed.autor_ti}/avatar.png"

    async with as_user(conn, seed.autor_ti):
        status = await conn.execute(
            "INSERT INTO storage.objects (bucket_id, name) VALUES ('avatares', $1)", path
        )
        assert rowcount(status) == 1

        # Reenvio: o Storage roda isto como UPDATE (x-upsert: true) sobre a MESMA
        # linha — é exatamente o cenário que a 0037 corrigiu (sem `avatares_select_own`
        # o dono não enxergava a própria linha para o UPDATE mirar, e o reenvio
        # silenciosamente afetava 0 linhas).
        status = await conn.execute(
            "UPDATE storage.objects SET updated_at = now() WHERE bucket_id = 'avatares' AND name = $1",
            path,
        )
    assert rowcount(status) == 1

    # Outro usuário não pode reenviar por cima do avatar alheio.
    async with as_user(conn, seed.staff_ti):
        status = await conn.execute(
            "UPDATE storage.objects SET updated_at = now() WHERE bucket_id = 'avatares' AND name = $1",
            path,
        )
    assert rowcount(status) == 0
