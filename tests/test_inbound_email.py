import pytest
from unittest.mock import AsyncMock, patch
from fastapi.testclient import TestClient

from app.main import app
from app.routes.common import extrair_resposta_email
from app.notification import gerar_token_resposta, validar_token_resposta
from app.config import get_settings


def test_extrair_resposta_email():
    # Teste para Gmail
    gmail_reply = (
        "Muito obrigado pelo retorno! Vou testar agora.\n\n"
        "On Tue, Jul 7, 2026 at 10:15 AM, <cotacoes@bondmann.com.br> wrote:\n"
        "> Olá Osvaldo,\n"
        "> Este é um e-mail de teste."
    )
    assert extrair_resposta_email(gmail_reply) == "Muito obrigado pelo retorno! Vou testar agora."

    # Teste para Outlook
    outlook_reply = (
        "Olá, já respondi a sua pergunta.\n\n"
        "De: cotacoes@bondmann.com.br <cotacoes@bondmann.com.br>\n"
        "Enviado: terça-feira, 7 de julho de 2026 10:15\n"
        "Para: osvaldo.bello@bondmann.com.br"
    )
    assert extrair_resposta_email(outlook_reply) == "Olá, já respondi a sua pergunta."

    # Teste para Mensagem Original
    brazilian_reply = (
        "Ajustado conforme solicitado.\n\n"
        "-----Mensagem Original-----\n"
        "De: \"Suporte Bondmann\" <cotacoes@bondmann.com.br>"
    )
    assert extrair_resposta_email(brazilian_reply) == "Ajustado conforme solicitado."

    # Teste texto vazio ou nulo
    assert extrair_resposta_email("") == ""
    assert extrair_resposta_email(None) == ""


def test_hmac_token_validation():
    secret = "my-secret-key"
    codigo = "CH-1002"
    user_id = "d0be13f5-7c3b-489e-9e7f-6821217e14ba"

    token = gerar_token_resposta(codigo, user_id, secret)
    assert len(token) == 16
    assert validar_token_resposta(codigo, user_id, token, secret) is True
    assert validar_token_resposta("CH-9999", user_id, token, secret) is False
    assert validar_token_resposta(codigo, "other-user-id", token, secret) is False


@patch("app.db.admin_connection")
@patch("app.notification.notificar_nova_mensagem_email", new_callable=AsyncMock)
def test_inbound_webhook_valid_token(mock_notify, mock_admin_conn):
    # Setup mock do banco de dados
    mock_conn = AsyncMock()
    
    # 1ª consulta: dados do chamado
    chamado_row = {
        "id": "11111111-1111-1111-1111-111111111111",
        "codigo": "CH-1002",
        "titulo": "Integração Mídia",
        "cliente_id": "22222222-2222-2222-2222-222222222222",
        "operador_id": "33333333-3333-3333-3333-333333333333",
        "empresa_id": "55555555-5555-5555-5555-555555555555",
    }
    # 2ª consulta: perfil do usuário pelo email
    profile_row = {
        "id": "22222222-2222-2222-2222-222222222222",
        "role": "CLIENTE",
    }
    # 3ª consulta: inserção da mensagem
    insert_row = {
        "id": "44444444-4444-4444-4444-444444444444",
        "created_at": "2026-07-07T10:00:00Z"
    }

    mock_conn.fetchrow.side_effect = [chamado_row, profile_row, insert_row]
    
    # Mock do context manager
    class MockCM:
        async def __aenter__(self):
            return mock_conn
        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass

    mock_admin_conn.return_value = MockCM()

    # Gera token usando o secret ativo das configurações de teste
    settings = get_settings()
    secret = settings.inbound_email_secret or settings.session_secret
    token = gerar_token_resposta("CH-1002", "22222222-2222-2222-2222-222222222222", secret)

    payload = {
        "recipient": f"chamado+CH-1002+{token}@reply.bondmann.com.br",
        "sender": "osvaldo.bello@bondmann.com.br",
        "body-plain": "Minha resposta de teste por e-mail."
    }

    with TestClient(app) as client:
        resp = client.post("/api/inbound-email", data=payload)
    
    assert resp.status_code == 200
    assert resp.json()["success"] is True
    assert resp.json()["message_id"] == "44444444-4444-4444-4444-444444444444"


@patch("app.db.admin_connection")
def test_inbound_webhook_invalid_token(mock_admin_conn):
    mock_conn = AsyncMock()
    chamado_row = {
        "id": "11111111-1111-1111-1111-111111111111",
        "codigo": "CH-1002",
        "titulo": "Integração Mídia",
        "cliente_id": "22222222-2222-2222-2222-222222222222",
        "operador_id": "33333333-3333-3333-3333-333333333333",
    }
    profile_row = {
        "id": "22222222-2222-2222-2222-222222222222",
        "role": "CLIENTE",
    }
    mock_conn.fetchrow.side_effect = [chamado_row, profile_row]

    class MockCM:
        async def __aenter__(self):
            return mock_conn
        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass

    mock_admin_conn.return_value = MockCM()

    # O token precisa conter caracteres hexadecimais válidos [a-f0-9] para passar no Regex
    payload = {
        "recipient": "chamado+CH-1002+deadbeefdeadbeef@reply.bondmann.com.br",
        "sender": "osvaldo.bello@bondmann.com.br",
        "body-plain": "Conteúdo"
    }

    with TestClient(app) as client:
        resp = client.post("/api/inbound-email", data=payload)
    
    assert resp.status_code == 403
    assert resp.json()["error"] == "Assinatura inválida"
