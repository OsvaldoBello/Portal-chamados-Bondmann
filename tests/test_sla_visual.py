"""Testes do indicador visual de SLA (Fase 4) — puros."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.domain.sla_visual import estado_sla, humanizar_delta

BASE = datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)


def test_humanizar_delta():
    assert humanizar_delta(0) == "0m"
    assert humanizar_delta(35 * 60) == "35m"
    assert humanizar_delta(2 * 3600 + 10 * 60) == "2h 10m"
    assert humanizar_delta(28 * 3600) == "1d 4h"
    assert humanizar_delta(-500) == "0m"  # nunca negativo


def _janela(criado_h_atras, prazo_em_h):
    created = BASE - timedelta(hours=criado_h_atras)
    limite = BASE + timedelta(hours=prazo_em_h)
    return created, limite


def test_resolvido_e_neutro():
    s = estado_sla(BASE, BASE + timedelta(hours=1), resolvido_em=BASE, agora=BASE)
    assert s.estado == "resolvido" and not s.pulsar


def test_ok_verde_muito_tempo():
    created, limite = _janela(1, 23)  # 1h decorrida de 24h -> ~96% restante
    s = estado_sla(created, limite, agora=BASE)
    assert s.estado == "ok" and not s.pulsar


def test_warn_amarelo_abaixo_de_25pct():
    created, limite = _janela(18, 2)  # janela 20h, restam 2h -> 10%..25%? 2/20=10% -> danger
    # ajusta p/ cair na faixa warn (entre 10% e 25%): restam 4h de 20h = 20%
    created, limite = _janela(16, 4)
    s = estado_sla(created, limite, agora=BASE)
    assert s.estado == "warn"


def test_danger_abaixo_de_10pct():
    created, limite = _janela(19, 1)  # janela 20h, resta 1h = 5%
    s = estado_sla(created, limite, agora=BASE)
    assert s.estado == "danger" and s.pulsar


def test_vencido_e_danger_pulsante():
    s = estado_sla(BASE - timedelta(hours=10), BASE - timedelta(hours=1), agora=BASE)
    assert s.estado == "danger" and s.pulsar
    assert "Vencido" in s.texto


def test_sem_prazo_indefinido():
    s = estado_sla(BASE, None, agora=BASE)
    assert s.estado == "indefinido"
