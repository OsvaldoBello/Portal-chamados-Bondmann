"""Feriados nacionais via biblioteca `holidays` (Fase 5 — 2026-07-09).

Antes, `feriados` (migration 0017) só existia como uma lista de `INSERT`
literais (2026–2028) que precisava ser editada à mão a cada virada de ano. Este
módulo gera a mesma informação de forma dinâmica a partir da biblioteca
`holidays`, e o admin (TI) sincroniza com um botão em vez de escrever SQL.
"""

from __future__ import annotations

from datetime import date

import holidays


def feriados_nacionais(anos: list[int]) -> list[tuple[date, str]]:
    """Feriados nacionais do Brasil para os anos informados, ordenados por data.

    ``⚠️ A VALIDAR``: só cobre o calendário NACIONAL — pontos facultativos
    (Carnaval, Corpus Christi) e feriados municipais/estaduais ficam fora,
    mesma ressalva que já existia no seed manual da 0017. Se a empresa precisar
    de feriados locais, cadastre-os direto na tabela ``feriados`` — a
    sincronização nunca apaga nem sobrescreve o que já existe (``ON CONFLICT
    DO NOTHING``), só adiciona o que falta.
    """
    br = holidays.country_holidays("BR", years=anos)
    return sorted(br.items())


def proximos_anos(a_partir_de: int, quantidade: int = 3) -> list[int]:
    """Ano atual + N seguintes (mesmo horizonte do antigo seed manual: ~3 anos)."""
    return list(range(a_partir_de, a_partir_de + quantidade + 1))
