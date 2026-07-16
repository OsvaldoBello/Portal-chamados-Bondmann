from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import app
from app.notification import gerar_token_resposta, validar_token_resposta
from app.routes.common import extrair_resposta_email

_INBOUND_SECRET = "segredo-dedicado-de-teste-nao-e-session-secret"


def _settings(**overrides) -> Settings:
    base = dict(
        session_secret="segredo-real-de-teste-nao-default",
        csrf_secret="outro-segredo-real-de-teste-nao-default",
        inbound_email_secret=_INBOUND_SECRET,
    )
    base.update(overrides)
    return Settings(**base)


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

    # Gera token usando o segredo dedicado do inbound (Sprint 1 / item 1.3).
    token = gerar_token_resposta("CH-1002", "22222222-2222-2222-2222-222222222222", _INBOUND_SECRET)

    payload = {
        "recipient": f"chamado+CH-1002+{token}@reply.bondmann.com.br",
        "sender": "osvaldo.bello@bondmann.com.br",
        "body-plain": "Minha resposta de teste por e-mail."
    }

    with patch("app.routes.common.get_settings", return_value=_settings()):
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

    with patch("app.routes.common.get_settings", return_value=_settings()):
        with TestClient(app) as client:
            resp = client.post("/api/inbound-email", data=payload)

    assert resp.status_code == 403
    assert resp.json()["error"] == "Assinatura inválida"


def test_inbound_webhook_sem_secret_dedicado_desabilita_rota():
    """Sprint 1 / item 1.3 (M4): sem INBOUND_EMAIL_SECRET configurado, a rota
    rejeita de cara — nunca cai de volta para validar com o SESSION_SECRET."""
    payload = {
        "recipient": "chamado+CH-1002+deadbeefdeadbeef@reply.bondmann.com.br",
        "sender": "osvaldo.bello@bondmann.com.br",
        "body-plain": "Conteúdo",
    }
    with patch("app.routes.common.get_settings", return_value=_settings(inbound_email_secret="")):
        with TestClient(app) as client:
            resp = client.post("/api/inbound-email", data=payload)

    assert resp.status_code == 503


async def test_notificar_nova_mensagem_email_sem_secret_dedicado_nao_gera_reply_to():
    """Sprint 1 / item 1.3 (M4): domínio configurado sem segredo dedicado ⇒
    nenhum reply-to é gerado (nunca assina com o SESSION_SECRET)."""
    from app.notification import notificar_nova_mensagem_email

    settings = _settings(inbound_email_secret="", inbound_email_domain="reply.bondmann.com.br")
    chamado = {
        "id": "11111111-1111-1111-1111-111111111111",
        "codigo": "CH-1002",
        "titulo": "Teste",
        "cliente_id": "22222222-2222-2222-2222-222222222222",
        "operador_id": "33333333-3333-3333-3333-333333333333",
    }

    admin_client = AsyncMock()
    admin_client.auth.admin.get_user_by_id = AsyncMock(
        return_value=type("R", (), {"user": type("U", (), {"email": "operador@bondmann.com.br"})()})()
    )

    with patch("app.notification.get_settings", return_value=settings):
        with patch("app.notification.ensure_admin_client", AsyncMock(return_value=admin_client)):
            with patch("app.notification.enviar_email", new_callable=AsyncMock) as mock_enviar:
                await notificar_nova_mensagem_email(
                    chamado, "22222222-2222-2222-2222-222222222222", "conteúdo"
                )

    assert mock_enviar.called
    assert mock_enviar.call_args.kwargs.get("reply_to") is None


async def test_notificar_nova_mensagem_email_com_secret_dedicado_gera_reply_to():
    from app.notification import notificar_nova_mensagem_email

    settings = _settings(inbound_email_domain="reply.bondmann.com.br")
    chamado = {
        "id": "11111111-1111-1111-1111-111111111111",
        "codigo": "CH-1002",
        "titulo": "Teste",
        "cliente_id": "22222222-2222-2222-2222-222222222222",
        "operador_id": "33333333-3333-3333-3333-333333333333",
    }

    admin_client = AsyncMock()
    admin_client.auth.admin.get_user_by_id = AsyncMock(
        return_value=type("R", (), {"user": type("U", (), {"email": "operador@bondmann.com.br"})()})()
    )

    with patch("app.notification.get_settings", return_value=settings):
        with patch("app.notification.ensure_admin_client", AsyncMock(return_value=admin_client)):
            with patch("app.notification.enviar_email", new_callable=AsyncMock) as mock_enviar:
                await notificar_nova_mensagem_email(
                    chamado, "22222222-2222-2222-2222-222222222222", "conteúdo"
                )

    assert mock_enviar.called
    reply_to = mock_enviar.call_args.kwargs.get("reply_to")
    assert reply_to is not None
    assert reply_to.startswith("chamado+ch-1002+")
    assert reply_to.endswith("@reply.bondmann.com.br")
