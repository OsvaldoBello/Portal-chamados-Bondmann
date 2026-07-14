"""Webhook do WhatsApp Cloud API (Sprint 0 / item 0.3): endurecimento do
POST /api/webhooks/whatsapp — rejeição sem secret em produção, validação de
assinatura HMAC e log sem PII."""

import hashlib
import hmac
import json
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import app

_PAYLOAD = {
    "object": "whatsapp_business_account",
    "entry": [
        {
            "id": "1234567890",
            "changes": [
                {
                    "field": "messages",
                    "value": {
                        "messaging_product": "whatsapp",
                        "messages": [{"id": "wamid.TESTE123", "type": "text"}],
                    },
                }
            ],
        }
    ],
}


def _settings(**overrides) -> Settings:
    base = dict(
        session_secret="segredo-real-de-teste-nao-default",
        csrf_secret="outro-segredo-real-de-teste-nao-default",
        whatsapp_app_secret="",
    )
    base.update(overrides)
    return Settings(**base)


def test_sem_secret_em_producao_rejeita():
    with patch("app.routes.whatsapp.get_settings", return_value=_settings(environment="production")):
        with TestClient(app) as client:
            resp = client.post("/api/webhooks/whatsapp", json=_PAYLOAD)
    assert resp.status_code == 503


def test_sem_secret_em_dev_aceita_sem_validar():
    with patch("app.routes.whatsapp.get_settings", return_value=_settings(environment="development")):
        with TestClient(app) as client:
            resp = client.post("/api/webhooks/whatsapp", json=_PAYLOAD)
    assert resp.status_code == 200
    assert resp.json()["success"] is True


def test_assinatura_invalida_rejeita():
    settings = _settings(environment="production", whatsapp_app_secret="app-secret-da-meta")
    with patch("app.routes.whatsapp.get_settings", return_value=settings):
        with TestClient(app) as client:
            resp = client.post(
                "/api/webhooks/whatsapp",
                json=_PAYLOAD,
                headers={"X-Hub-Signature-256": "sha256=assinatura-forjada"},
            )
    assert resp.status_code == 403


def test_assinatura_valida_aceita():
    settings = _settings(environment="production", whatsapp_app_secret="app-secret-da-meta")
    with patch("app.routes.whatsapp.get_settings", return_value=settings):
        with TestClient(app) as client:
            raw = json.dumps(_PAYLOAD).encode("utf-8")
            assinatura = hmac.new(b"app-secret-da-meta", raw, hashlib.sha256).hexdigest()
            resp = client.post(
                "/api/webhooks/whatsapp",
                content=raw,
                headers={
                    "X-Hub-Signature-256": f"sha256={assinatura}",
                    "Content-Type": "application/json",
                },
            )
    assert resp.status_code == 200
    assert resp.json()["success"] is True
