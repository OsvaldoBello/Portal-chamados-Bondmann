import logging
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from app.config import get_settings
from app.auth.supabase_client import ensure_admin_client

log = logging.getLogger("app.notification")

def enviar_email_smtp(para: str, assunto: str, corpo: str) -> None:
    """Dispara um e-mail utilizando os parâmetros SMTP configurados no ambiente.
    Caso não esteja configurado, realiza um log para desenvolvimento local/mock.
    """
    settings = get_settings()
    if not (settings.smtp_host and settings.smtp_user and settings.smtp_password):
        log.info(f"[EMAIL NOTIFICATION MOCK] To: {para} | Subject: {assunto} | Body: {corpo.strip()}")
        return

    try:
        msg = MIMEMultipart()
        msg['From'] = settings.smtp_from
        msg['To'] = para
        msg['Subject'] = assunto
        msg.attach(MIMEText(corpo, 'plain', 'utf-8'))

        with smtplib.SMTP(settings.smtp_host, settings.smtp_port) as server:
            server.starttls()
            server.login(settings.smtp_user, settings.smtp_password)
            server.send_message(msg)
        log.info(f"[EMAIL NOTIFICATION SUCCESS] Email successfully sent to {para}")
    except Exception as e:
        log.error(f"[EMAIL NOTIFICATION ERROR] Failed to send email to {para}: {e}")


async def notificar_nova_mensagem_email(chamado: dict, remetente_id: str, conteudo: str) -> None:
    """Identifica o destinatário (cliente ou operador) e envia uma notificação por e-mail
    quando uma nova mensagem é postada no chat do chamado.
    """
    cliente_id = str(chamado.get("cliente_id"))
    operador_id = str(chamado.get("operador_id")) if chamado.get("operador_id") else None

    # Se a mensagem foi enviada pelo cliente, o destinatário é o operador.
    # Caso contrário, o destinatário é o cliente.
    destinatario_id = operador_id if str(remetente_id) == cliente_id else cliente_id

    if not destinatario_id:
        return

    # Buscar e-mail do destinatário usando a API de administração do Supabase.
    client = await ensure_admin_client()
    if not client:
        log.warning(f"Could not load email for user {destinatario_id}: Supabase admin client is not configured.")
        return

    try:
        res = await client.auth.admin.get_user_by_id(destinatario_id)
        u = getattr(res, "user", res)
        email = getattr(u, "email", None)
        if not email:
            log.warning(f"User {destinatario_id} has no registered email.")
            return
    except Exception as e:
        log.error(f"Error fetching user email for {destinatario_id}: {e}")
        return

    codigo = chamado.get("codigo", "")
    titulo = chamado.get("titulo", "")
    assunto = f"[Portal Bondmann] Nova mensagem no chamado {codigo}"
    corpo = (
        f"Olá,\n\n"
        f"Você recebeu uma nova mensagem no chat do chamado {codigo} ({titulo}):\n\n"
        f"\"{conteudo}\"\n\n"
        f"Para visualizar e responder, acesse o Portal de Chamados Bondmann.\n\n"
        f"Atenciosamente,\n"
        f"Portal de Chamados Bondmann Química\n"
    )
    enviar_email_smtp(email, assunto, corpo)
