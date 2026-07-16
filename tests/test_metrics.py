"""Métricas em memória por-processo (Sprint 2 / item 2.6) — puro, sem banco."""

from app import metrics


def setup_function() -> None:
    metrics.reset()


def test_snapshot_vazio():
    corpo = metrics.snapshot()
    assert corpo["requests_total"] == 0
    assert corpo["requests_5xx_total"] == 0
    assert corpo["polling_304"]["taxa"] is None


def test_conta_status_e_5xx():
    metrics.registrar_request("/health", 200, 5.0)
    metrics.registrar_request("/workspace/fila", 500, 12.0)
    metrics.registrar_request("/workspace/fila", 500, 8.0)

    corpo = metrics.snapshot()
    assert corpo["requests_total"] == 3
    assert corpo["requests_5xx_total"] == 2
    assert corpo["status_counts"] == {200: 1, 500: 2}


def test_p95_por_rota_calculado_sobre_amostras_da_rota():
    for i in range(1, 101):
        metrics.registrar_request("/workspace/fila/fragmento", 200, float(i))

    corpo = metrics.snapshot()
    # 100 amostras 1..100 -> p95 = amostra de índice 94 (0-based) = 95.
    assert corpo["latency_p95_ms_por_rota"]["/workspace/fila/fragmento"] == 95.0


def test_taxa_304_do_polling_isolada_de_rotas_nao_polling():
    metrics.registrar_request("/workspace/fila/fragmento", 200, 5.0, is_polling=True)
    metrics.registrar_request("/workspace/fila/fragmento", 304, 1.0, is_polling=True)
    metrics.registrar_request("/workspace/fila/fragmento", 304, 1.0, is_polling=True)
    # Rota fora do polling não deve contaminar a taxa.
    metrics.registrar_request("/health", 304, 1.0, is_polling=False)

    corpo = metrics.snapshot()
    assert corpo["polling_304"]["total"] == 3
    assert corpo["polling_304"]["hits"] == 2
    assert corpo["polling_304"]["taxa"] == round(2 / 3, 4)


def test_janela_de_amostras_por_rota_e_limitada():
    for i in range(600):
        metrics.registrar_request("/x", 200, float(i))
    # Não cresce sem limite (Seção 2.6: em memória, sem backend externo).
    with metrics._lock:
        assert len(metrics._latencias_por_rota["/x"]) == metrics._MAX_AMOSTRAS_POR_ROTA
