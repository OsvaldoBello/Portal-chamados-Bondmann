"""Indicadores "fáceis de ler" do Painel Admin — resumos agregados que
transformam contagens brutas em algo interpretável num relance (percentual +
contagem lado a lado), sem esconder o número absoluto atrás só de uma cor.

Puro e testável (sem banco): recebe as contagens já agregadas pelo
`AdminRepo` e devolve os dataclasses que os templates (`admin/dashboard.html`
e `app/services/export_html.py`) renderizam — mesma fonte pras duas telas,
mesmo padrão de `app/domain/sla_visual.py`.
"""

from __future__ import annotations

from dataclasses import dataclass


def _pct(parte: int, total: int) -> float:
    return round(100.0 * parte / total, 1) if total else 0.0


@dataclass(frozen=True)
class ResumoSatisfacao:
    """Notas de CSAT (1–5) agrupadas em 3 faixas fáceis de ler: 4–5 estrelas
    = satisfeito, 3 = neutro, 1–2 = insatisfeito — mesma base do gráfico
    "Distribuição do CSAT" (:meth:`AdminRepo.csat_distribuicao`), só que
    somada em 3 baldes em vez de 5 barras."""

    satisfeito: int
    neutro: int
    insatisfeito: int
    total: int
    pct_satisfeito: float
    pct_neutro: float
    pct_insatisfeito: float


def resumir_satisfacao(csat: dict[int, int]) -> ResumoSatisfacao:
    """``csat`` = saída de :meth:`AdminRepo.csat_distribuicao` (nota 1–5 → contagem)."""
    satisfeito = (csat.get(4) or 0) + (csat.get(5) or 0)
    neutro = csat.get(3) or 0
    insatisfeito = (csat.get(1) or 0) + (csat.get(2) or 0)
    total = satisfeito + neutro + insatisfeito
    return ResumoSatisfacao(
        satisfeito=satisfeito,
        neutro=neutro,
        insatisfeito=insatisfeito,
        total=total,
        pct_satisfeito=_pct(satisfeito, total),
        pct_neutro=_pct(neutro, total),
        pct_insatisfeito=_pct(insatisfeito, total),
    )


@dataclass(frozen=True)
class ResumoTempoConclusao:
    """Dias entre abertura e resolução, em 3 faixas (mesmo recorte que o time
    já acompanhava em planilha): mesmo dia, dia seguinte, 2 dias ou mais.
    Base: chamados RESOLVIDOS no período selecionado (mesma âncora do TMA)."""

    mesmo_dia: int
    dia_seguinte: int
    dois_dias_mais: int
    total: int
    pct_mesmo_dia: float
    pct_dia_seguinte: float
    pct_dois_dias_mais: float


def resumir_tempo_conclusao(mesmo_dia: int, dia_seguinte: int, dois_dias_mais: int) -> ResumoTempoConclusao:
    total = mesmo_dia + dia_seguinte + dois_dias_mais
    return ResumoTempoConclusao(
        mesmo_dia=mesmo_dia,
        dia_seguinte=dia_seguinte,
        dois_dias_mais=dois_dias_mais,
        total=total,
        pct_mesmo_dia=_pct(mesmo_dia, total),
        pct_dia_seguinte=_pct(dia_seguinte, total),
        pct_dois_dias_mais=_pct(dois_dias_mais, total),
    )
