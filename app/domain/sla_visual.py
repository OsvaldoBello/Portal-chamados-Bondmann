"""Estado visual do SLA (Fase 4 — indicador por cores).

Regra (Seção 6, Fase 4 do plano mestre), sobre o prazo de **resolução**:
- **resolvido**: chamado já resolvido (`resolvido_em` setado) → neutro.
- **danger**: vencido, OU faltando **< 10%** da janela (vermelho, piscante).
- **warn**: faltando **< 25%** da janela (amarelo).
- **ok**: caso contrário (verde).

Puro e testável: recebe os timestamps e "agora" (injeta nos testes). Timestamps
em UTC (timezone-aware). A fração é sobre a janela `created_at → limite_resolucao`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional


@dataclass(frozen=True)
class EstadoSLA:
    estado: str   # 'ok' | 'warn' | 'danger' | 'resolvido' | 'indefinido'
    texto: str    # ex.: "2h 10m restantes", "Vencido há 35m", "Resolvido"
    pulsar: bool   # vermelho piscante quando crítico/vencido


@dataclass(frozen=True)
class BarraSLA:
    """Barra de progresso do prazo: enche conforme o tempo passa.

    - ``pct``: quanto da janela já foi consumida (0–100) — o "quanto cheia".
    - ``estado``: cor da barra (verde no início; **amarela a partir da metade**
      do prazo; **vermelha** perto de estourar/estourada). Segue a descrição do
      produto (verde → amarelo na metade → vermelho no fim).
    """
    estado: str   # 'ok' | 'warn' | 'danger' | 'resolvido' | 'indefinido'
    pct: int      # 0..100 (fração já decorrida da janela)
    texto: str
    pulsar: bool


def humanizar_delta(segundos: int) -> str:
    """Formata uma duração (segundos, sempre >= 0) como '1d 4h', '2h 10m', '35m'."""
    segundos = max(0, int(segundos))
    dias, resto = divmod(segundos, 86400)
    horas, resto = divmod(resto, 3600)
    minutos = resto // 60
    if dias:
        return f"{dias}d {horas}h"
    if horas:
        return f"{horas}h {minutos}m"
    return f"{minutos}m"


def estado_sla(
    created_at: Optional[datetime],
    limite_resolucao: Optional[datetime],
    resolvido_em: Optional[datetime] = None,
    agora: Optional[datetime] = None,
) -> EstadoSLA:
    agora = agora or datetime.now(timezone.utc)

    if resolvido_em is not None:
        return EstadoSLA("resolvido", "Resolvido", False)
    if limite_resolucao is None or created_at is None:
        return EstadoSLA("indefinido", "Sem prazo", False)

    restante = (limite_resolucao - agora).total_seconds()
    if restante <= 0:
        return EstadoSLA("danger", f"Vencido há {humanizar_delta(-restante)}", True)

    total = (limite_resolucao - created_at).total_seconds()
    frac = restante / total if total > 0 else 0.0
    texto = f"{humanizar_delta(restante)} restantes"
    if frac < 0.10:
        return EstadoSLA("danger", texto, True)
    if frac < 0.25:
        return EstadoSLA("warn", texto, False)
    return EstadoSLA("ok", texto, False)


def barra_sla(
    created_at: Optional[datetime],
    limite_resolucao: Optional[datetime],
    resolvido_em: Optional[datetime] = None,
    agora: Optional[datetime] = None,
) -> BarraSLA:
    """Barra de progresso do prazo de resolução (verde→amarelo na metade→vermelho).

    ``pct`` é a fração já decorrida (0–100); a cor vem da fração **restante**:
    verde enquanto sobra > 50%, amarelo entre 10% e 50%, vermelho abaixo de 10%
    ou vencido (piscando)."""
    agora = agora or datetime.now(timezone.utc)

    if resolvido_em is not None:
        return BarraSLA("resolvido", 100, "Resolvido", False)
    if limite_resolucao is None or created_at is None:
        return BarraSLA("indefinido", 0, "Sem prazo", False)

    total = (limite_resolucao - created_at).total_seconds()
    restante = (limite_resolucao - agora).total_seconds()
    if restante <= 0:
        return BarraSLA("danger", 100, f"Vencido há {humanizar_delta(-restante)}", True)

    frac_rem = restante / total if total > 0 else 0.0
    pct = int(round((1.0 - frac_rem) * 100))
    pct = max(0, min(100, pct))
    texto = f"{humanizar_delta(restante)} restantes"
    if frac_rem < 0.10:
        return BarraSLA("danger", pct, texto, True)
    if frac_rem <= 0.50:  # "amarela a partir da metade" do prazo
        return BarraSLA("warn", pct, texto, False)
    return BarraSLA("ok", pct, texto, False)
