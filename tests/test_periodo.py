"""Janela de período dos filtros (app/domain/periodo.py).

O que estes testes protegem: o dia que o usuário escolhe no filtro é o dia que
ele lê no cartão — e o cartão é renderizado em **America/Sao_Paulo**
(`app/templating.py::fmt_dt`), enquanto a sessão do Postgres em produção está
em **UTC**. Comparar `created_at` (timestamptz) com `::date` cru deslocava as
duas bordas do período em 3h.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

from app.domain.periodo import TZ_BR, janela_utc, periodo_invertido


def test_sem_periodo_nao_impoe_limite():
    assert janela_utc(None, None) == (None, None)


def test_inicio_e_meia_noite_de_brasilia_em_utc():
    inicio, fim = janela_utc(date(2026, 8, 3), None)
    # 00:00 de 03/08 em Brasília (UTC-3) = 03:00Z do mesmo dia.
    assert inicio == datetime(2026, 8, 3, 3, 0, tzinfo=UTC)
    assert fim is None


def test_fim_e_exclusivo_e_inclui_o_dia_inteiro():
    _, fim = janela_utc(None, date(2026, 8, 3))
    # Fim exclusivo = 00:00 de 04/08 em Brasília = 03:00Z do dia 4.
    assert fim == datetime(2026, 8, 4, 3, 0, tzinfo=UTC)


def test_chamado_da_noite_cai_no_dia_que_a_tela_mostra():
    """O caso concreto que o `::date` errava: chamado aberto às 21h30 de 03/08
    em Brasília é gravado como 04/08 00:30Z. O cartão diz "Solicitado em
    03/08"; o filtro "de 03/08 até 03/08" precisa incluí-lo — e o filtro do dia
    04, excluí-lo."""
    aberto = datetime(2026, 8, 3, 21, 30, tzinfo=TZ_BR).astimezone(UTC)
    assert aberto.date() == date(2026, 8, 4)  # o dia em UTC é OUTRO

    inicio, fim = janela_utc(date(2026, 8, 3), date(2026, 8, 3))
    assert inicio <= aberto < fim

    inicio4, fim4 = janela_utc(date(2026, 8, 4), date(2026, 8, 4))
    assert not (inicio4 <= aberto < fim4)


def test_borda_da_manha_continua_dentro_do_dia():
    aberto = datetime(2026, 8, 3, 0, 5, tzinfo=TZ_BR).astimezone(UTC)
    inicio, fim = janela_utc(date(2026, 8, 3), date(2026, 8, 3))
    assert inicio <= aberto < fim


def test_ultimo_segundo_do_dia_entra_e_o_primeiro_do_seguinte_nao():
    inicio, fim = janela_utc(date(2026, 7, 31), date(2026, 7, 31))
    ultimo = datetime(2026, 7, 31, 23, 59, 59, tzinfo=TZ_BR).astimezone(UTC)
    primeiro_do_proximo = datetime(2026, 8, 1, 0, 0, 0, tzinfo=TZ_BR).astimezone(UTC)
    assert inicio <= ultimo < fim
    assert primeiro_do_proximo >= fim


def test_mes_fechado_cobre_todos_os_dias():
    inicio, fim = janela_utc(date(2026, 7, 1), date(2026, 7, 31))
    assert inicio == datetime(2026, 7, 1, 3, 0, tzinfo=UTC)
    assert fim == datetime(2026, 8, 1, 3, 0, tzinfo=UTC)


def test_periodo_invertido():
    assert periodo_invertido(date(2026, 8, 10), date(2026, 8, 1)) is True
    assert periodo_invertido(date(2026, 8, 1), date(2026, 8, 10)) is False
    assert periodo_invertido(date(2026, 8, 1), date(2026, 8, 1)) is False
    assert periodo_invertido(None, date(2026, 8, 1)) is False
    assert periodo_invertido(date(2026, 8, 1), None) is False
    assert periodo_invertido(None, None) is False
