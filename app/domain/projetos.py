"""Domínio de projetos e algoritmo universal de ordenação (SLA + Prioridade)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import math
from typing import Any

PESOS_PRIORIDADE = {
    "URGENTE": 1,
    "ALTA": 2,
    "MEDIA": 3,
    "BAIXA": 4,
}


def chave_ordenacao_sla_prioridade(chamado: dict[str, Any], agora: datetime) -> tuple:
    """Gera chave de ordenação estável baseada em SLA (primário) e Prioridade (secundário em 24h).

    1. tem_prazo: 0 para chamados com limite_resolucao definido, 1 para sem prazo (vão pro fim).
    2. bloco_24h: agrupa por janela de 86400s (24h) a partir do momento atual.
    3. peso_prioridade: URGENTE (1) > ALTA (2) > MEDIA (3) > BAIXA (4).
    4. segundos_restantes: desempate fino pelo prazo exato.
    5. created_at: desempate cronológico.
    """
    limite = chamado.get("limite_resolucao")
    sem_prazo = chamado.get("sem_prazo") or False

    if not limite or sem_prazo:
        # Sem prazo: bloco infinito, desempate por prioridade e depois criação
        prioridade = str(chamado.get("prioridade") or "MEDIA").upper()
        peso_pr = PESOS_PRIORIDADE.get(prioridade, 3)
        created_at = chamado.get("created_at") or agora
        return (1, 999999, peso_pr, 0.0, created_at)

    delta_segundos = (limite - agora).total_seconds()
    # Janela de 24h (86400s)
    bloco_24h = math.floor(delta_segundos / 86400.0)

    prioridade = str(chamado.get("prioridade") or "MEDIA").upper()
    peso_pr = PESOS_PRIORIDADE.get(prioridade, 3)
    created_at = chamado.get("created_at") or agora

    return (0, bloco_24h, peso_pr, delta_segundos, created_at)


def ordenar_chamados_sla_prioridade(
    chamados: list[dict[str, Any]], agora: datetime | None = None
) -> list[dict[str, Any]]:
    """Ordena uma lista de chamados utilizando o critério de SLA + Prioridade em 24h."""
    agora = agora or datetime.now(UTC)
    return sorted(chamados, key=lambda c: chave_ordenacao_sla_prioridade(c, agora))


def calcular_estimativa_novo_projeto(
    projetos_ativos: list[dict[str, Any]],
    metricas_concluidos: dict[str, Any] | None,
    agora: datetime | None = None,
) -> dict[str, Any]:
    """Calcula a estimativa de tempo e posição para um novo projeto na fila."""
    agora = agora or datetime.now(UTC)
    posicao_fila = len(projetos_ativos) + 1

    tma_dias = 7.5
    if metricas_concluidos and metricas_concluidos.get("media_dias_reais"):
        tma_dias = float(metricas_concluidos["media_dias_reais"])

    # Estimativa de dias úteis baseada no TMA histórico e na vazão da fila
    # Cada projeto ativo à frente adiciona uma fração ponderada de tempo útil
    dias_uteis_estimados = max(round(tma_dias + (len(projetos_ativos) * 1.5)), 3)

    # Projetar data pulando fins de semana (sábado e domingo)
    cur = agora
    dias_adicionados = 0
    while dias_adicionados < dias_uteis_estimados:
        cur += timedelta(days=1)
        # 5 = Sábado, 6 = Domingo
        if cur.weekday() < 5:
            dias_adicionados += 1

    return {
        "posicao_fila": posicao_fila,
        "tma_dias_uteis": tma_dias,
        "dias_uteis_estimados": dias_uteis_estimados,
        "data_prevista": cur,
        "total_ativos": len(projetos_ativos),
    }
