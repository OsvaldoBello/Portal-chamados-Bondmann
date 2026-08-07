"""Testes do PortalService (Sprint 2 / item 2.2, M2) — puros, sem banco."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from app.services import portal as portal_service_module
from app.services.portal import PortalService

DEPARTAMENTOS = [
    {"id": "d1", "nome": "TI"},
    {"id": "d2", "nome": "RH"},
    {"id": "d3", "nome": "Marketing"},
]
MARKETING_ID = "d3"
OUTRO_ID = "d1"


def test_pode_avaliar_autor_resolvido_para_outro_departamento():
    assert PortalService.pode_avaliar(
        {
            "status": "RESOLVIDO",
            "cliente_id": "u1",
            "departamento_id": "ti",
            "cliente_departamento_id": "marketing",
        },
        "u1",
    )


def test_pode_avaliar_falso_se_nao_resolvido():
    assert not PortalService.pode_avaliar(
        {"status": "EM_ATENDIMENTO", "cliente_id": "u1"}, "u1"
    )


def test_pode_avaliar_falso_se_nao_autor():
    assert not PortalService.pode_avaliar(
        {"status": "RESOLVIDO", "cliente_id": "u1"}, "u2"
    )


def test_pode_avaliar_falso_se_chamado_para_o_proprio_departamento():
    """Chamado aberto para o próprio departamento do autor (ex.: alguém do
    Marketing pedindo pro Marketing) não precisa avaliação — ali o setor se
    autoatende num quadro estilo Trello, sem uma relação real de "quem
    prestou o serviço" (bug real recorrente no Marketing, BOND-2026-00027)."""
    assert not PortalService.pode_avaliar(
        {
            "status": "RESOLVIDO",
            "cliente_id": "u1",
            "departamento_id": "marketing",
            "cliente_departamento_id": "marketing",
        },
        "u1",
    )


def test_pode_avaliar_verdadeiro_mesmo_se_atendido_por_colega_do_setor():
    """O que importa é o departamento de DESTINO do chamado, não quem
    especificamente atendeu — mesmo resolvido por um colega do próprio setor
    do autor, se o chamado foi endereçado a OUTRO departamento, avalia-se
    normalmente."""
    assert PortalService.pode_avaliar(
        {
            "status": "RESOLVIDO",
            "cliente_id": "u1",
            "operador_id": "colega_do_ti",
            "departamento_id": "ti",
            "cliente_departamento_id": "marketing",
        },
        "u1",
    )


def test_pode_reabrir_autor_com_chamado_resolvido():
    assert PortalService.pode_reabrir({"status": "RESOLVIDO", "cliente_id": "u1"}, "u1")


def test_pode_reabrir_falso_se_nao_resolvido():
    assert not PortalService.pode_reabrir({"status": "EM_ATENDIMENTO", "cliente_id": "u1"}, "u1")


def test_pode_reabrir_falso_se_nao_autor():
    assert not PortalService.pode_reabrir({"status": "RESOLVIDO", "cliente_id": "u1"}, "u2")


def test_pode_reabrir_verdadeiro_mesmo_no_proprio_departamento():
    """Ao contrário de `pode_avaliar`, a reabertura vale mesmo quando o chamado
    foi endereçado ao PRÓPRIO departamento do autor (autoatendimento) — não é
    uma nota de CSAT sobre quem prestou o serviço."""
    assert PortalService.pode_reabrir(
        {
            "status": "RESOLVIDO",
            "cliente_id": "u1",
            "departamento_id": "marketing",
            "cliente_departamento_id": "marketing",
        },
        "u1",
    )


def test_marketing_dep_id_encontra_por_nome_case_insensitive():
    deps = [{"id": "x1", "nome": "marketing"}]
    assert PortalService.marketing_dep_id(deps) == "x1"


def test_marketing_dep_id_vazio_se_ausente():
    assert PortalService.marketing_dep_id([{"id": "d1", "nome": "TI"}]) == ""


def test_representante_pode_marketing_falso_para_representantes():
    assert PortalService.representante_pode_marketing("Representantes") is False
    assert PortalService.representante_pode_marketing("  representantes  ") is False


def test_representante_pode_marketing_verdadeiro_para_supervisao_e_gerencia():
    assert PortalService.representante_pode_marketing("Supervisão de Vendas") is True
    assert PortalService.representante_pode_marketing("Gerentes de vendas") is True


def test_representante_pode_marketing_verdadeiro_para_outros_setores():
    assert PortalService.representante_pode_marketing("TI") is True
    assert PortalService.representante_pode_marketing(None) is True
    assert PortalService.representante_pode_marketing("") is True


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


# --------------------------------------------------------------------------
# data_entrega_min / regras_marketing: o Marketing não atende fins de semana
# --------------------------------------------------------------------------
def _congelar_hoje(monkeypatch, ano, mes, dia):
    """Fixa `datetime.now()` (usado por `data_entrega_min`) numa data fixa."""

    class _DatetimeCongelado(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(ano, mes, dia, 10, 0, tzinfo=tz)

    monkeypatch.setattr(portal_service_module, "datetime", _DatetimeCongelado)


@pytest.mark.parametrize(
    "hoje,minimo_esperado",
    [
        ((2026, 8, 6), "2026-08-11"),  # quinta -> terça (não segunda)
        ((2026, 8, 7), "2026-08-12"),  # sexta -> quarta (não segunda)
        ((2026, 8, 3), "2026-08-05"),  # segunda -> quarta (sem fim de semana no meio)
    ],
)
def test_data_entrega_min_pula_fim_de_semana(monkeypatch, hoje, minimo_esperado):
    _congelar_hoje(monkeypatch, *hoje)
    assert PortalService.data_entrega_min().isoformat() == minimo_esperado


def test_regras_marketing_data_no_sabado_da_erro_mesmo_apos_o_minimo(monkeypatch):
    _congelar_hoje(monkeypatch, 2026, 8, 3)  # segunda; mínimo = quarta (05/08)
    r = PortalService.regras_marketing(
        departamento_id=MARKETING_ID,
        setores_ativos=DEPARTAMENTOS,
        prioridade="ALTA",
        sem_prazo_marcado=False,
        # 08/08/2026 é sábado, está após o mínimo mas cai no fim de semana.
        data_entrega="2026-08-08",
    )
    assert r.erro is not None
    assert "fim de semana" in r.erro.lower() or "dia útil" in r.erro.lower()
    assert r.data_entrega is None


def test_regras_marketing_aberto_na_quinta_nao_aceita_segunda_exige_terca(monkeypatch):
    _congelar_hoje(monkeypatch, 2026, 8, 6)  # quinta
    r = PortalService.regras_marketing(
        departamento_id=MARKETING_ID,
        setores_ativos=DEPARTAMENTOS,
        prioridade="ALTA",
        sem_prazo_marcado=False,
        # 10/08/2026 é a segunda seguinte — não deve ser aceita como mínimo.
        data_entrega="2026-08-10",
    )
    assert r.erro is not None
    assert r.data_entrega is None


def test_regras_marketing_aberto_na_quinta_aceita_terca(monkeypatch):
    _congelar_hoje(monkeypatch, 2026, 8, 6)  # quinta; mínimo = terça (11/08)
    r = PortalService.regras_marketing(
        departamento_id=MARKETING_ID,
        setores_ativos=DEPARTAMENTOS,
        prioridade="ALTA",
        sem_prazo_marcado=False,
        data_entrega="2026-08-11",
    )
    assert r.erro is None
    assert r.data_entrega.isoformat() == "2026-08-11"
