"""Testes de unidade para regras de negócio e validação de atraso do Marketing.

Sprint 2 / Fase 4 (Marketing) — Obrigatoriedade de causa de atraso para conclusão.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.domain.marketing import (
    CAUSAS_ATRASO_MARKETING,
    demanda_marketing_atrasada,
    validar_conclusao_marketing,
)


def test_constantes_causas_marketing():
    assert CAUSAS_ATRASO_MARKETING == (
        "SEM CAUSA REGISTRADA",
        "AGUARDANDO DEFINIÇÃO INTERNA",
        "DEPENDÊNCIA DE EXECUÇÃO INTERNA",
        "DEPENDÊNCIA DE TERCEIROS",
    )


def test_nao_marketing_nunca_atrasa_por_esta_regra():
    agora = datetime.now(UTC)
    chamado_ti = {
        "departamento": "TI",
        "limite_resolucao": agora - timedelta(days=2),
        "sem_prazo": False,
    }
    assert demanda_marketing_atrasada(chamado_ti, agora=agora) is False
    assert validar_conclusao_marketing(chamado_ti, agora=agora) is None


def test_marketing_sem_prazo_nunca_atrasa():
    agora = datetime.now(UTC)
    chamado = {
        "departamento": "Marketing",
        "sem_prazo": True,
        "limite_resolucao": None,
    }
    assert demanda_marketing_atrasada(chamado, agora=agora) is False
    assert validar_conclusao_marketing(chamado, agora=agora) is None


def test_marketing_com_limite_futuro_nao_atrasado():
    agora = datetime.now(UTC)
    chamado = {
        "departamento": "Marketing",
        "sem_prazo": False,
        "limite_resolucao": agora + timedelta(hours=4),
    }
    assert demanda_marketing_atrasada(chamado, agora=agora) is False
    assert validar_conclusao_marketing(chamado, agora=agora) is None


def test_marketing_com_limite_passado_atrasado():
    agora = datetime.now(UTC)
    chamado = {
        "departamento": "Marketing",
        "sem_prazo": False,
        "limite_resolucao": agora - timedelta(hours=1),
    }
    assert demanda_marketing_atrasada(chamado, agora=agora) is True


def test_validar_conclusao_marketing_atrasado_sem_causa():
    agora = datetime.now(UTC)
    chamado = {
        "departamento": "Marketing",
        "sem_prazo": False,
        "limite_resolucao": agora - timedelta(days=1),
        "causa_atraso": None,
    }
    erro = validar_conclusao_marketing(chamado, causa_atraso="", agora=agora)
    assert erro is not None
    assert "obrigatório" in erro.lower()
    assert "causa" in erro.lower()


def test_validar_conclusao_marketing_atrasado_com_causa_invalida():
    agora = datetime.now(UTC)
    chamado = {
        "departamento": "Marketing",
        "sem_prazo": False,
        "limite_resolucao": agora - timedelta(days=1),
    }
    erro = validar_conclusao_marketing(chamado, causa_atraso="MOTIVO DESCONHECIDO", agora=agora)
    assert erro is not None
    assert "inválida" in erro.lower() or "permitidas" in erro.lower()


@pytest.mark.parametrize("causa", CAUSAS_ATRASO_MARKETING)
def test_validar_conclusao_marketing_atrasado_com_causas_validas(causa):
    agora = datetime.now(UTC)
    chamado = {
        "departamento": "Marketing",
        "sem_prazo": False,
        "limite_resolucao": agora - timedelta(days=1),
        "causa_atraso": None,
    }
    erro = validar_conclusao_marketing(chamado, causa_atraso=causa, agora=agora)
    assert erro is None


def test_validar_conclusao_marketing_usa_causa_previa_se_nao_enviada():
    agora = datetime.now(UTC)
    chamado = {
        "departamento": "Marketing",
        "sem_prazo": False,
        "limite_resolucao": agora - timedelta(days=1),
        "causa_atraso": "DEPENDÊNCIA DE TERCEIROS",
    }
    erro = validar_conclusao_marketing(chamado, causa_atraso=None, agora=agora)
    assert erro is None
