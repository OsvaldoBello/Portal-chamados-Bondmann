"""Métricas mínimas em memória por-processo (Sprint 2 / item 2.6).

Sem Prometheus nem outro backend externo — contadores simples e uma janela
deslizante de latências por rota, no mesmo espírito do cache/rate-limit
locais (Seção 2.3/2.4 do plano mestre): suficiente para o critério de go-live
("48h sem 5xx" verificável sem grep manual de log) e para observar a taxa de
304 do polling e a saturação do pool asyncpg. Não sobrevive a restart nem soma
entre réplicas — mesma ressalva já registrada para cache/rate-limit: migrar
para um backend compartilhado (Redis) se houver >1 réplica.
"""

from __future__ import annotations

import threading
from collections import deque

_MAX_AMOSTRAS_POR_ROTA = 500

_lock = threading.Lock()
_contagem_status: dict[int, int] = {}
_latencias_por_rota: dict[str, deque[float]] = {}
_polling_304 = 0
_polling_total = 0


def registrar_request(path: str, status: int, duration_ms: float, *, is_polling: bool = False) -> None:
    """Chamado uma vez por request pelo ``RequestContextMiddleware``."""
    global _polling_304, _polling_total
    with _lock:
        _contagem_status[status] = _contagem_status.get(status, 0) + 1
        amostras = _latencias_por_rota.setdefault(path, deque(maxlen=_MAX_AMOSTRAS_POR_ROTA))
        amostras.append(duration_ms)
        if is_polling:
            _polling_total += 1
            if status == 304:
                _polling_304 += 1


def _p95(amostras: deque[float]) -> float:
    if not amostras:
        return 0.0
    ordenado = sorted(amostras)
    idx = max(0, int(len(ordenado) * 0.95) - 1)
    return round(ordenado[idx], 2)


def snapshot() -> dict:
    """Estado atual — consumido por ``GET /metrics``."""
    with _lock:
        total = sum(_contagem_status.values())
        total_5xx = sum(v for k, v in _contagem_status.items() if k >= 500)
        p95_por_rota = {
            path: _p95(amostras) for path, amostras in _latencias_por_rota.items() if amostras
        }
        taxa_304 = (_polling_304 / _polling_total) if _polling_total else None
        return {
            "requests_total": total,
            "requests_5xx_total": total_5xx,
            "status_counts": dict(sorted(_contagem_status.items())),
            "latency_p95_ms_por_rota": p95_por_rota,
            "polling_304": {
                "total": _polling_total,
                "hits": _polling_304,
                "taxa": round(taxa_304, 4) if taxa_304 is not None else None,
            },
        }


def reset() -> None:
    """Zera os contadores. Só para uso em teste (isolamento entre casos)."""
    global _polling_304, _polling_total
    with _lock:
        _contagem_status.clear()
        _latencias_por_rota.clear()
        _polling_304 = 0
        _polling_total = 0
