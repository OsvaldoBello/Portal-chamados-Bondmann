"""e2e do SLA da coluna "Projetos" (migrations 0064/0066, marker ``rls``).

Não é RLS — é regra de negócio que só existe em TRIGGER (o prazo nunca passa
pelo Python: `AtendimentoRepo.alterar_status` faz um `UPDATE chamados SET
status`, e é o banco que escreve `limite_resolucao`). Por isso mora aqui, junto
da suíte que roda contra Postgres real: com `FakeRepo` não haveria o que testar.

Prova as três garantias da 0064:
1. Entrar em PROJETOS dá ~1 mês de prazo (o antigo eram as 24h úteis do plano).
2. Mudar a PRIORIDADE de um projeto não derruba esse mês (a `calcular_sla_chamado`
   dispara em `UPDATE OF prioridade` e recalculava tudo antes da 0064).
3. Sair de PROJETOS preserva o prazo do projeto — o mês é o compromisso daquele
   trabalho, não um efeito de estar na coluna.

E as três da 0066, que transformou o mês fixo em prazo configurável:
4. `chamados.prazo_projeto_dias` manda no prazo (2 meses, 70 dias, 4 meses…).
5. Trocar os dias reconta da ENTRADA na coluna (`projeto_em`), não de hoje —
   senão cada ajuste esticaria o projeto em silêncio.
6. Sem prazo próprio, vale `planos_sla.projeto_dias` (30 = o mês da 0064, o que
   mantém o comportamento anterior para quem não configurar nada).
"""

from __future__ import annotations

from datetime import UTC, datetime

import asyncpg
import pytest

from tests.e2e.conftest import Seed

pytestmark = pytest.mark.rls

# A 0064 dá 22 dias ÚTEIS (13.200 minutos de expediente). Em dias corridos isso
# cai entre ~29 e ~34 dias à frente, dependendo de fins de semana e feriados no
# meio — a janela abaixo é folgada de propósito: o teste garante "cerca de um
# mês, e não as 24h do suporte", não a aritmética de `sla_add_minutos_uteis`
# (essa já é exercitada pela 0017 e por `tests/test_feriados.py`).
_MIN_DIAS = 25
_MAX_DIAS = 40


async def _migration_aplicada(conn: asyncpg.Connection) -> bool:
    # A 0066 trocou a assinatura de 1 argumento da 0064 por esta (o mês virou
    # parâmetro) — checar a nova cobre as duas.
    return bool(await conn.fetchval(
        "SELECT to_regprocedure('public.sla_prazo_projeto(timestamptz, integer)') IS NOT NULL"
    ))


async def _limite(conn: asyncpg.Connection, chamado_id) -> datetime:
    return await conn.fetchval("SELECT limite_resolucao FROM chamados WHERE id = $1", chamado_id)


def _dias_ate(limite: datetime) -> float:
    return (limite - datetime.now(UTC)).total_seconds() / 86400


async def test_entrar_em_projetos_da_prazo_de_um_mes(conn: asyncpg.Connection, seed: Seed):
    if not await _migration_aplicada(conn):
        pytest.skip("migration 0066_sla_projetos_prazo_configuravel não aplicada no Supabase local")

    antes = await _limite(conn, seed.chamado_ti)
    assert _dias_ate(antes) < 7, "chamado novo deveria nascer com o prazo curto do suporte"

    await conn.execute(
        "UPDATE chamados SET status = 'PROJETOS' WHERE id = $1", seed.chamado_ti
    )

    depois = await _limite(conn, seed.chamado_ti)
    assert _MIN_DIAS < _dias_ate(depois) < _MAX_DIAS


async def test_mudar_prioridade_do_projeto_nao_derruba_o_mes(conn: asyncpg.Connection, seed: Seed):
    if not await _migration_aplicada(conn):
        pytest.skip("migration 0066_sla_projetos_prazo_configuravel não aplicada no Supabase local")

    await conn.execute("UPDATE chamados SET status = 'PROJETOS' WHERE id = $1", seed.chamado_ti)
    prazo_projeto = await _limite(conn, seed.chamado_ti)

    # URGENTE é o pior caso: metade do prazo de ALTA (0017) — sem o ramo de
    # PROJETOS da 0064, isto voltaria o chamado para poucas horas de prazo.
    await conn.execute(
        "UPDATE chamados SET prioridade = 'URGENTE' WHERE id = $1", seed.chamado_ti
    )

    assert await _limite(conn, seed.chamado_ti) == prazo_projeto


async def test_sair_de_projetos_preserva_o_prazo(conn: asyncpg.Connection, seed: Seed):
    if not await _migration_aplicada(conn):
        pytest.skip("migration 0066_sla_projetos_prazo_configuravel não aplicada no Supabase local")

    await conn.execute("UPDATE chamados SET status = 'PROJETOS' WHERE id = $1", seed.chamado_ti)
    prazo_projeto = await _limite(conn, seed.chamado_ti)

    await conn.execute(
        "UPDATE chamados SET status = 'EM_ATENDIMENTO' WHERE id = $1", seed.chamado_ti
    )

    assert await _limite(conn, seed.chamado_ti) == prazo_projeto


async def test_projeto_vindo_de_aguardando_ganha_o_mes_e_nao_o_prazo_empurrado(
    conn: asyncpg.Connection, seed: Seed
):
    """`sla_pausa_aguardando` (0017/0044) empurra o prazo ao SAIR de AGUARDANDO e
    roda ANTES (ordem alfabética de trigger) — o mês do projeto é a palavra final."""
    if not await _migration_aplicada(conn):
        pytest.skip("migration 0066_sla_projetos_prazo_configuravel não aplicada no Supabase local")

    await conn.execute("UPDATE chamados SET status = 'AGUARDANDO' WHERE id = $1", seed.chamado_ti)
    await conn.execute(
        "UPDATE chamados SET aguardando_desde = now() - interval '3 days' WHERE id = $1",
        seed.chamado_ti,
    )
    await conn.execute("UPDATE chamados SET status = 'PROJETOS' WHERE id = $1", seed.chamado_ti)

    limite = await _limite(conn, seed.chamado_ti)
    assert _MIN_DIAS < _dias_ate(limite) < _MAX_DIAS
    assert await conn.fetchval(
        "SELECT aguardando_desde FROM chamados WHERE id = $1", seed.chamado_ti
    ) is None


async def test_projeto_marcado_sem_prazo_continua_sem_prazo(conn: asyncpg.Connection, seed: Seed):
    """`sem_prazo` (0040) é escolha explícita de "não há contagem" e ganha do mês."""
    if not await _migration_aplicada(conn):
        pytest.skip("migration 0066_sla_projetos_prazo_configuravel não aplicada no Supabase local")

    await conn.execute("UPDATE chamados SET sem_prazo = true WHERE id = $1", seed.chamado_ti)
    assert await _limite(conn, seed.chamado_ti) is None

    await conn.execute("UPDATE chamados SET status = 'PROJETOS' WHERE id = $1", seed.chamado_ti)

    assert await _limite(conn, seed.chamado_ti) is None


async def test_prazo_proprio_do_chamado_manda_no_prazo(conn: asyncpg.Connection, seed: Seed):
    """0066: o operador define os dias daquele projeto — 70 dias em vez do mês."""
    if not await _migration_aplicada(conn):
        pytest.skip("migration 0066_sla_projetos_prazo_configuravel não aplicada no Supabase local")

    await conn.execute(
        "UPDATE chamados SET prazo_projeto_dias = 70 WHERE id = $1", seed.chamado_ti
    )
    await conn.execute("UPDATE chamados SET status = 'PROJETOS' WHERE id = $1", seed.chamado_ti)

    dias = _dias_ate(await _limite(conn, seed.chamado_ti))
    assert 60 < dias < 85, "70 dias corridos, em minutos úteis, cai nesta janela"


async def test_trocar_os_dias_reconta_da_entrada_na_coluna(conn: asyncpg.Connection, seed: Seed):
    """A base é `projeto_em`, não `now()`: um projeto que entrou há duas semanas
    e recebe "30 dias" vence ~15 dias à frente, não daqui a 30. Sem isso, cada
    ajuste de prazo esticaria o projeto em silêncio."""
    if not await _migration_aplicada(conn):
        pytest.skip("migration 0066_sla_projetos_prazo_configuravel não aplicada no Supabase local")

    await conn.execute("UPDATE chamados SET status = 'PROJETOS' WHERE id = $1", seed.chamado_ti)
    await conn.execute(
        "UPDATE chamados SET projeto_em = now() - interval '14 days' WHERE id = $1",
        seed.chamado_ti,
    )

    await conn.execute(
        "UPDATE chamados SET prazo_projeto_dias = 30 WHERE id = $1", seed.chamado_ti
    )

    dias = _dias_ate(await _limite(conn, seed.chamado_ti))
    assert 8 < dias < 22, "30 dias contados de 14 dias atrás, não de hoje"


async def test_sem_prazo_proprio_vale_o_padrao_do_plano(conn: asyncpg.Connection, seed: Seed):
    """`planos_sla.projeto_dias` é o padrão do setor; 30 (o default da coluna)
    reproduz exatamente o mês da 0064 — quem não configurar nada não sente a
    migration. Voltar o campo do chamado para NULL devolve o projeto ao padrão."""
    if not await _migration_aplicada(conn):
        pytest.skip("migration 0066_sla_projetos_prazo_configuravel não aplicada no Supabase local")

    await conn.execute(
        "UPDATE chamados SET prazo_projeto_dias = 120 WHERE id = $1", seed.chamado_ti
    )
    await conn.execute("UPDATE chamados SET status = 'PROJETOS' WHERE id = $1", seed.chamado_ti)
    assert _dias_ate(await _limite(conn, seed.chamado_ti)) > 100

    await conn.execute(
        "UPDATE chamados SET prazo_projeto_dias = NULL WHERE id = $1", seed.chamado_ti
    )

    assert _MIN_DIAS < _dias_ate(await _limite(conn, seed.chamado_ti)) < _MAX_DIAS


async def test_prazo_do_projeto_cai_em_horario_de_expediente(conn: asyncpg.Connection, seed: Seed):
    """Minutos ÚTEIS, não `now() + interval '1 month'`: o prazo nunca vence num
    fim de semana, feriado ou fora da janela 08–18 (America/Sao_Paulo)."""
    if not await _migration_aplicada(conn):
        pytest.skip("migration 0066_sla_projetos_prazo_configuravel não aplicada no Supabase local")

    await conn.execute("UPDATE chamados SET status = 'PROJETOS' WHERE id = $1", seed.chamado_ti)
    limite = await _limite(conn, seed.chamado_ti)

    local = await conn.fetchval(
        "SELECT $1::timestamptz AT TIME ZONE 'America/Sao_Paulo'", limite
    )
    assert local.isoweekday() <= 5
    assert 8 <= local.hour <= 18
    assert not await conn.fetchval("SELECT EXISTS (SELECT 1 FROM feriados WHERE data = $1)", local.date())
