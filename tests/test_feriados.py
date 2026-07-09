"""Testes do módulo de feriados nacionais (Fase 5) — puro, sem banco."""

from __future__ import annotations

from datetime import date

from app.domain.feriados import feriados_nacionais, proximos_anos


def test_proximos_anos_inclui_ano_atual_e_seguintes():
    assert proximos_anos(2026, 3) == [2026, 2027, 2028, 2029]


def test_feriados_nacionais_traz_datas_conhecidas_ordenadas():
    feriados = feriados_nacionais([2026])
    datas = [d for d, _ in feriados]
    assert datas == sorted(datas)
    assert date(2026, 1, 1) in datas    # Confraternização Universal
    assert date(2026, 9, 7) in datas    # Independência
    assert date(2026, 12, 25) in datas  # Natal


def test_feriados_nacionais_cobre_mais_de_um_ano():
    feriados = feriados_nacionais([2026, 2027])
    anos = {d.year for d, _ in feriados}
    assert anos == {2026, 2027}
