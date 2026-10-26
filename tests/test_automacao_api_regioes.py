from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import app

TOKEN = "tok-automacao-teste-valido"


def test_sync_sap_regioes_sem_token_retorna_401(monkeypatch):
    monkeypatch.setattr(get_settings(), "automacao_worker_token", TOKEN)
    client = TestClient(app)
    resp = client.post("/api/automacao/sap-regioes/sync", json=[])
    assert resp.status_code == 401


def test_sync_sap_regioes_com_token_invalido_retorna_401(monkeypatch):
    monkeypatch.setattr(get_settings(), "automacao_worker_token", TOKEN)
    client = TestClient(app)
    resp = client.post(
        "/api/automacao/sap-regioes/sync",
        headers={"X-Automacao-Token": "token-errado"},
        json=[],
    )
    assert resp.status_code == 401


def test_sync_sap_regioes_sucesso(monkeypatch):
    monkeypatch.setattr(get_settings(), "automacao_worker_token", TOKEN)
    client = TestClient(app)
    payload = [
        {
            "email": "helmano.silva@bondmann.com.br",
            "card_code": "RH2021",
            "card_name": "HELMANO SILVA",
            "tipo": "supervisor",
            "rotulo": "6 regiões vinculadas",
            "regioes": ["136-VALE DO ACO", "069-DIVINOPOLIS"],
            "detalhe": "136-VALE DO ACO, 069-DIVINOPOLIS",
            "eh_multiplo": True,
        }
    ]

    with patch("app.routes.automacao_api.salvar_sap_regioes_em_lote", new_callable=AsyncMock) as mock_salvar:
        mock_salvar.return_value = 1
        resp = client.post(
            "/api/automacao/sap-regioes/sync",
            headers={"X-Automacao-Token": TOKEN},
            json=payload,
        )
        assert resp.status_code == 200
        assert resp.json() == {"ok": True, "processados": 1}
        mock_salvar.assert_awaited_once_with(payload)
