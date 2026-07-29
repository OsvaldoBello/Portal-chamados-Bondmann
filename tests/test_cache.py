"""Cache em memória com TTL (`app/cache.py`, Seção 2.3).

É o cache dos catálogos (categorias/departamentos): leitura frequente, escrita
rara, invalidação EXPLÍCITA na escrita. Um bug aqui não aparece como erro — o
portal só continua servindo catálogo velho depois de o admin editar —, então o
que se prova é justamente a expiração e as duas formas de invalidar.
"""

from __future__ import annotations

import pytest

from app import cache


@pytest.fixture(autouse=True)
def _limpa():
    cache.clear()
    yield
    cache.clear()


def test_guarda_e_devolve_ate_o_ttl():
    cache.set("categorias", [{"id": "c1"}], ttl=60)
    assert cache.get("categorias") == [{"id": "c1"}]


def test_chave_inexistente_e_none():
    assert cache.get("nunca-gravada") is None


def test_valor_expirado_some(monkeypatch):
    """Relógio monotônico controlado — sem `sleep` no teste."""
    agora = [1000.0]
    monkeypatch.setattr(cache.time, "monotonic", lambda: agora[0])

    cache.set("categorias", "valor", ttl=90)
    agora[0] += 89.9
    assert cache.get("categorias") == "valor"
    agora[0] += 0.2  # passou do TTL
    assert cache.get("categorias") is None
    assert "categorias" not in cache._store  # expirado é expurgado, não só ignorado


def test_invalidate_derruba_so_a_chave_pedida():
    cache.set("a", 1, ttl=60)
    cache.set("b", 2, ttl=60)
    cache.invalidate("a")
    cache.invalidate("inexistente")  # no-op, não explode
    assert cache.get("a") is None
    assert cache.get("b") == 2


def test_invalidate_prefix_limpa_todas_as_variantes_do_escopo():
    """Catálogo cacheado por escopo: editar categorias precisa limpar TODOS os
    departamentos de uma vez, não só o que por acaso foi lido por último."""
    cache.set("categorias_ativas:dep-1", ["x"], ttl=60)
    cache.set("categorias_ativas:dep-2", ["y"], ttl=60)
    cache.set("departamentos_ativos", ["z"], ttl=60)
    cache.invalidate_prefix("categorias_ativas:")
    assert cache.get("categorias_ativas:dep-1") is None
    assert cache.get("categorias_ativas:dep-2") is None
    assert cache.get("departamentos_ativos") == ["z"]


def test_clear_zera_tudo():
    cache.set("a", 1, ttl=60)
    cache.clear()
    assert cache._store == {}
