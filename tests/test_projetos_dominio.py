"""Testes unitários do domínio de projetos e ordenação universal SLA + Prioridade."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.domain.projetos import (
    calcular_estimativa_novo_projeto,
    chave_ordenacao_sla_prioridade,
    ordenar_chamados_sla_prioridade,
)

AGORA = datetime(2026, 10, 9, 10, 0, 0, tzinfo=UTC)  # Sexta-feira 10:00


def test_chave_ordenacao_direta():
    c = {"limite_resolucao": AGORA + timedelta(hours=5), "prioridade": "ALTA"}
    k = chave_ordenacao_sla_prioridade(c, agora=AGORA)
    assert k[0] == 0
    assert k[1] == 0
    assert k[2] == 2


def test_ordenacao_vencido_primeiro():
    c_vencido = {"id": "1", "limite_resolucao": AGORA - timedelta(hours=2), "prioridade": "BAIXA"}
    c_futuro = {"id": "2", "limite_resolucao": AGORA + timedelta(hours=10), "prioridade": "URGENTE"}
    ordenados = ordenar_chamados_sla_prioridade([c_futuro, c_vencido], agora=AGORA)
    assert ordenados[0]["id"] == "1"
    assert ordenados[1]["id"] == "2"


def test_desempate_prioridade_janela_24h():
    c_alta = {"id": "1", "limite_resolucao": AGORA + timedelta(hours=5), "prioridade": "ALTA"}
    c_urgente = {"id": "2", "limite_resolucao": AGORA + timedelta(hours=8), "prioridade": "URGENTE"}
    c_baixa = {"id": "3", "limite_resolucao": AGORA + timedelta(hours=2), "prioridade": "BAIXA"}
    # Todos dentro da mesma janela de 24h: URGENTE (id 2) > ALTA (id 1) > BAIXA (id 3)
    ordenados = ordenar_chamados_sla_prioridade([c_baixa, c_alta, c_urgente], agora=AGORA)
    assert [c["id"] for c in ordenados] == ["2", "1", "3"]


def test_diferenca_maior_24h_respeita_sla():
    c_amanha_baixa = {"id": "1", "limite_resolucao": AGORA + timedelta(hours=12), "prioridade": "BAIXA"}
    c_proxima_semana_urgente = {
        "id": "2",
        "limite_resolucao": AGORA + timedelta(hours=96),
        "prioridade": "URGENTE",
    }
    ordenados = ordenar_chamados_sla_prioridade([c_proxima_semana_urgente, c_amanha_baixa], agora=AGORA)
    assert ordenados[0]["id"] == "1"
    assert ordenados[1]["id"] == "2"


def test_sem_prazo_vai_para_o_final():
    c_sem_prazo = {"id": "1", "limite_resolucao": None, "prioridade": "URGENTE"}
    c_com_prazo = {"id": "2", "limite_resolucao": AGORA + timedelta(days=5), "prioridade": "BAIXA"}
    ordenados = ordenar_chamados_sla_prioridade([c_sem_prazo, c_com_prazo], agora=AGORA)
    assert ordenados[0]["id"] == "2"
    assert ordenados[1]["id"] == "1"


def test_multiplos_sem_prazo_ordenados_por_prioridade():
    c1 = {"id": "1", "limite_resolucao": None, "prioridade": "BAIXA"}
    c2 = {"id": "2", "limite_resolucao": None, "prioridade": "ALTA"}
    c3 = {"id": "3", "limite_resolucao": None, "sem_prazo": True, "prioridade": "URGENTE"}
    ordenados = ordenar_chamados_sla_prioridade([c1, c2, c3], agora=AGORA)
    assert [c["id"] for c in ordenados] == ["3", "2", "1"]


def test_estimativa_novo_projeto():
    ativos = [{"id": f"{i}"} for i in range(5)]
    metricas = {"total_concluidos": 54, "media_dias_reais": 7.5, "media_dias_sla": 12.5}
    est = calcular_estimativa_novo_projeto(ativos, metricas, agora=AGORA)
    assert est["posicao_fila"] == 6
    assert est["tma_dias_uteis"] == 7.5
    assert est["data_prevista"] is not None
    # Como AGORA é sexta-feira, a data prevista deve pular o fim de semana
    assert est["data_prevista"] > AGORA + timedelta(days=7)
