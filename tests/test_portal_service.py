"""Testes do PortalService (Sprint 2 / item 2.2, M2) — puros, sem banco."""

from __future__ import annotations

from datetime import timedelta

from app.services.portal import PortalService

DEPARTAMENTOS = [
    {"id": "d1", "nome": "TI"},
    {"id": "d2", "nome": "RH"},
    {"id": "d3", "nome": "Marketing"},
]
MARKETING_ID = "d3"
OUTRO_ID = "d1"


def test_pode_avaliar_autor_resolvido():
    assert PortalService.pode_avaliar(
        {"status": "RESOLVIDO", "cliente_id": "u1"}, "u1"
    )


def test_pode_avaliar_falso_se_nao_resolvido():
    assert not PortalService.pode_avaliar(
        {"status": "EM_ATENDIMENTO", "cliente_id": "u1"}, "u1"
    )


def test_pode_avaliar_falso_se_nao_autor():
    assert not PortalService.pode_avaliar(
        {"status": "RESOLVIDO", "cliente_id": "u1"}, "u2"
    )


def test_marketing_dep_id_encontra_por_nome_case_insensitive():
    deps = [{"id": "x1", "nome": "marketing"}]
    assert PortalService.marketing_dep_id(deps) == "x1"


def test_marketing_dep_id_vazio_se_ausente():
    assert PortalService.marketing_dep_id([{"id": "d1", "nome": "TI"}]) == ""


def test_regras_marketing_fora_do_marketing_preserva_prioridade():
    r = PortalService.regras_marketing(
        departamento_id=OUTRO_ID,
        setores_ativos=DEPARTAMENTOS,
        prioridade="ALTA",
        sem_prazo_marcado=False,
        data_entrega="",
    )
    assert not r.is_marketing
    assert r.prioridade == "ALTA"
    assert r.erro is None


def test_regras_marketing_sem_prazo_marcado_forca_baixa():
    r = PortalService.regras_marketing(
        departamento_id=MARKETING_ID,
        setores_ativos=DEPARTAMENTOS,
        prioridade="ALTA",
        sem_prazo_marcado=True,
        data_entrega="",
    )
    assert r.is_marketing and r.sem_prazo
    assert r.prioridade == "BAIXA"
    assert r.data_entrega is None
    assert r.erro is None


def test_regras_marketing_sem_data_entrega_da_erro():
    r = PortalService.regras_marketing(
        departamento_id=MARKETING_ID,
        setores_ativos=DEPARTAMENTOS,
        prioridade="ALTA",
        sem_prazo_marcado=False,
        data_entrega="",
    )
    assert r.is_marketing
    assert r.erro is not None
    assert "48h" in r.erro or "data limite" in r.erro.lower()


def test_regras_marketing_data_invalida_da_erro():
    r = PortalService.regras_marketing(
        departamento_id=MARKETING_ID,
        setores_ativos=DEPARTAMENTOS,
        prioridade="ALTA",
        sem_prazo_marcado=False,
        data_entrega="não-é-data",
    )
    assert r.erro == "Data de entrega inválida."


def test_regras_marketing_data_abaixo_do_minimo_da_erro():
    ontem = (PortalService.data_entrega_min() - timedelta(days=1)).isoformat()
    r = PortalService.regras_marketing(
        departamento_id=MARKETING_ID,
        setores_ativos=DEPARTAMENTOS,
        prioridade="ALTA",
        sem_prazo_marcado=False,
        data_entrega=ontem,
    )
    assert r.erro is not None
    assert "48h" in r.erro


def test_regras_marketing_data_valida_aceita_e_forca_media():
    minimo = PortalService.data_entrega_min()
    r = PortalService.regras_marketing(
        departamento_id=MARKETING_ID,
        setores_ativos=DEPARTAMENTOS,
        prioridade="ALTA",
        sem_prazo_marcado=False,
        data_entrega=minimo.isoformat(),
    )
    assert r.is_marketing and not r.sem_prazo
    assert r.prioridade == "MEDIA"
    assert r.data_entrega == minimo
    assert r.erro is None
