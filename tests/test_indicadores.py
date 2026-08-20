"""Testes dos resumos "fáceis de ler" do Painel Admin (2026-08-20) — puros."""

from __future__ import annotations

from app.domain.indicadores import resumir_satisfacao, resumir_tempo_conclusao


def test_resumir_satisfacao_agrupa_1_a_5_em_3_faixas():
    r = resumir_satisfacao({1: 1, 2: 1, 3: 2, 4: 3, 5: 3})
    assert r.satisfeito == 6      # 4★ + 5★
    assert r.neutro == 2          # 3★
    assert r.insatisfeito == 2    # 1★ + 2★
    assert r.total == 10
    assert r.pct_satisfeito == 60.0
    assert r.pct_neutro == 20.0
    assert r.pct_insatisfeito == 20.0


def test_resumir_satisfacao_tolera_notas_ausentes():
    r = resumir_satisfacao({4: 1, 5: 1})  # sem 1/2/3 no dict
    assert r.neutro == 0
    assert r.insatisfeito == 0
    assert r.satisfeito == 2
    assert r.total == 2
    assert r.pct_satisfeito == 100.0


def test_resumir_satisfacao_sem_avaliacoes_nao_divide_por_zero():
    r = resumir_satisfacao({})
    assert r.total == 0
    assert r.pct_satisfeito == 0.0
    assert r.pct_neutro == 0.0
    assert r.pct_insatisfeito == 0.0


def test_resumir_tempo_conclusao_calcula_percentuais():
    r = resumir_tempo_conclusao(mesmo_dia=6, dia_seguinte=3, dois_dias_mais=1)
    assert r.total == 10
    assert r.pct_mesmo_dia == 60.0
    assert r.pct_dia_seguinte == 30.0
    assert r.pct_dois_dias_mais == 10.0


def test_resumir_tempo_conclusao_sem_resolvidos_nao_divide_por_zero():
    r = resumir_tempo_conclusao(mesmo_dia=0, dia_seguinte=0, dois_dias_mais=0)
    assert r.total == 0
    assert r.pct_mesmo_dia == 0.0
    assert r.pct_dia_seguinte == 0.0
    assert r.pct_dois_dias_mais == 0.0
