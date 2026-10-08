"""Testes de unidade para regras de negócio e validação de atraso do Marketing.

Sprint 2 / Fase 4 (Marketing) — Obrigatoriedade de causa de atraso para conclusão.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.domain.marketing import (
    CAUSAS_ATRASO_MARKETING,
    demanda_marketing_atrasada,
    dias_atraso_marketing,
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


def test_demanda_prazo_longo_novembro_criada_em_julho_nao_atrasada():
    """Cenário relatado pelo usuário: chamado aberto em julho com entrega para novembro.

    Em outubro, mais de 5 dias se passaram desde a criação, mas o prazo
    ainda não venceu. Não deve ser considerada atrasada nem computar dias de atraso.
    """
    julho = datetime(2026, 7, 10, 10, 0, tzinfo=UTC)
    novembro = datetime(2026, 11, 20, 18, 0, tzinfo=UTC)
    outubro = datetime(2026, 10, 8, 14, 0, tzinfo=UTC)

    chamado = {
        "departamento": "Marketing",
        "created_at": julho,
        "limite_resolucao": novembro,
        "sem_prazo": False,
        "resolvido_em": None,
    }

    assert demanda_marketing_atrasada(chamado, agora=outubro) is False
    assert dias_atraso_marketing(chamado, agora=outubro) == 0


def test_demanda_concluida_no_prazo_mesmo_com_ciclo_longo_nao_atrasa():
    """Demanda que levou 60 dias para ser feita, mas foi entregue dentro da data acordada."""
    criacao = datetime(2026, 5, 1, 10, 0, tzinfo=UTC)
    prazo = datetime(2026, 7, 1, 18, 0, tzinfo=UTC)
    conclusao = datetime(2026, 6, 28, 15, 0, tzinfo=UTC)

    chamado = {
        "departamento": "Marketing",
        "created_at": criacao,
        "limite_resolucao": prazo,
        "sem_prazo": False,
        "resolvido_em": conclusao,
    }

    assert demanda_marketing_atrasada(chamado) is False
    assert dias_atraso_marketing(chamado) == 0


def test_demanda_concluida_com_atraso_calcula_dias_sobre_limite():
    """Demanda criada há 120 dias, com prazo há 3 dias e concluída hoje: atraso é de 3 dias, não 120."""
    criacao = datetime(2026, 6, 1, 10, 0, tzinfo=UTC)
    prazo = datetime(2026, 10, 5, 18, 0, tzinfo=UTC)
    conclusao = datetime(2026, 10, 8, 18, 0, tzinfo=UTC)

    chamado = {
        "departamento": "Marketing",
        "created_at": criacao,
        "limite_resolucao": prazo,
        "sem_prazo": False,
        "resolvido_em": conclusao,
    }

    assert demanda_marketing_atrasada(chamado, agora=conclusao) is True
    assert dias_atraso_marketing(chamado, agora=conclusao) == 3


def test_demanda_sem_prazo_nunca_computa_dias_atraso():
    """Demandas marcadas como sem_prazo nunca acumulam dias de atraso."""
    criacao = datetime(2026, 1, 1, 10, 0, tzinfo=UTC)
    agora = datetime(2026, 10, 8, 14, 0, tzinfo=UTC)

    chamado = {
        "departamento": "Marketing",
        "created_at": criacao,
        "limite_resolucao": None,
        "sem_prazo": True,
        "resolvido_em": None,
    }

    assert demanda_marketing_atrasada(chamado, agora=agora) is False
    assert dias_atraso_marketing(chamado, agora=agora) == 0


def test_status_pausado_e_avaliacao_nao_contam_como_atrasados():
    """Status AGUARDANDO (prazo pausado) e RESOLVIDO (aguardando avaliacao) nunca contam como atrasados."""
    passado = datetime(2026, 8, 1, 18, 0, tzinfo=UTC)
    agora = datetime(2026, 10, 8, 14, 0, tzinfo=UTC)

    chamado_pausado = {
        "departamento": "Marketing",
        "status": "AGUARDANDO",
        "limite_resolucao": passado,
        "sem_prazo": False,
        "resolvido_em": None,
    }
    assert demanda_marketing_atrasada(chamado_pausado, agora=agora) is False
    assert dias_atraso_marketing(chamado_pausado, agora=agora) == 0

    chamado_resolvido = {
        "departamento": "Marketing",
        "status": "RESOLVIDO",
        "limite_resolucao": passado,
        "sem_prazo": False,
        "resolvido_em": agora,
    }
    assert demanda_marketing_atrasada(chamado_resolvido, agora=agora) is False
    assert dias_atraso_marketing(chamado_resolvido, agora=agora) == 0


def test_status_ativos_contam_como_atrasados_quando_vencidos():
    """NOVO, A_FAZER, EM_ATENDIMENTO e AGUARDANDO_TERCEIROS contam como atrasados se vencidos."""
    passado = datetime(2026, 10, 1, 18, 0, tzinfo=UTC)
    agora = datetime(2026, 10, 8, 18, 0, tzinfo=UTC)

    for st in ("NOVO", "A_FAZER", "EM_ATENDIMENTO", "AGUARDANDO_TERCEIROS"):
        chamado = {
            "departamento": "Marketing",
            "status": st,
            "limite_resolucao": passado,
            "sem_prazo": False,
            "resolvido_em": None,
        }
        assert demanda_marketing_atrasada(chamado, agora=agora) is True, f"Falhou para status {st}"
        assert dias_atraso_marketing(chamado, agora=agora) == 7, f"Dias incorretos para status {st}"
