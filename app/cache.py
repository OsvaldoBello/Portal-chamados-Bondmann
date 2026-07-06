"""Cache em memória por-processo com TTL (Seção 2.3 do plano mestre).

Para catálogos de **leitura frequente e escrita rara** (categorias, departamentos).
Como o portal é interno de **org única** e esses catálogos são **globais**
(RLS ``USING (true)`` para todo autenticado), a chave é global — não há
vazamento cross-tenant possível. Invalidação **explícita na escrita**.
Para >1 réplica com invalidação consistente, migrar para Redis compartilhado.
"""

from __future__ import annotations

import threading
import time
from typing import Any

_lock = threading.Lock()
_store: dict[str, tuple[Any, float]] = {}


def get(key: str) -> Any | None:
    """Valor cacheado se presente e não expirado; senão ``None``."""
    with _lock:
        item = _store.get(key)
        if item is None:
            return None
        value, expires = item
        if time.monotonic() >= expires:
            _store.pop(key, None)
            return None
        return value


def set(key: str, value: Any, ttl: float) -> None:
    """Guarda ``value`` sob ``key`` por ``ttl`` segundos."""
    with _lock:
        _store[key] = (value, time.monotonic() + ttl)


def invalidate(key: str) -> None:
    """Expurga uma chave (chamar na escrita do recurso correspondente)."""
    with _lock:
        _store.pop(key, None)


def invalidate_prefix(prefix: str) -> None:
    """Expurga todas as chaves que começam com ``prefix``.

    Usado por catálogos cacheados por-escopo (ex.: categorias por departamento,
    chave ``categorias_ativas:<dep>``), onde a escrita deve limpar todas as
    variantes de uma vez."""
    with _lock:
        for k in [k for k in _store if k.startswith(prefix)]:
            _store.pop(k, None)


def clear() -> None:
    """Limpa todo o cache (útil em testes)."""
    with _lock:
        _store.clear()
