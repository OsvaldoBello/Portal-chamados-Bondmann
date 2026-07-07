import logging
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from app.config import get_settings
from app.auth.supabase_client import ensure_admin_client

log = logging.getLogger("app.notification")

def enviar_email_smtp(para: str, assunto: str, corpo_texto: str, corpo_html: str = None) -> None:
    """Dispara um e-mail utilizando os parâmetros SMTP configurados no ambiente.
    Caso não esteja configurado, realiza um log para desenvolvimento local/mock.
    """
    settings = get_settings()
    if not (settings.smtp_host and settings.smtp_user and settings.smtp_password):
        log.info(f"[EMAIL NOTIFICATION MOCK] To: {para} | Subject: {assunto} | Body: {corpo_texto.strip()}")
        return

    try:
        msg = MIMEMultipart('alternative')
        msg['From'] = settings.smtp_from
        msg['To'] = para
        msg['Subject'] = assunto

        msg.attach(MIMEText(corpo_texto, 'plain', 'utf-8'))
        if corpo_html:
            msg.attach(MIMEText(corpo_html, 'html', 'utf-8'))

        with smtplib.SMTP(settings.smtp_host, settings.smtp_port) as server:
            server.starttls()
            server.login(settings.smtp_user, settings.smtp_password)
            server.send_message(msg)
        log.info(f"[EMAIL NOTIFICATION SUCCESS] Email successfully sent to {para}")
    except Exception as e:
        log.error(f"[EMAIL NOTIFICATION ERROR] Failed to send email to {para}: {e}")


async def notificar_nova_mensagem_email(chamado: dict, remetente_id: str, conteudo: str) -> None:
    """Identifica o destinatário (cliente ou operador) e envia uma notificação por e-mail
    com layout HTML organizado quando uma nova mensagem é postada no chat do chamado.
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

    settings = get_settings()
    codigo = chamado.get("codigo", "")
    titulo = chamado.get("titulo", "")
    
    # Gerar a URL correta com base no destinatário
    site_url = settings.site_url.rstrip("/")
    if str(destinatario_id) == operador_id:
        url = f"{site_url}/workspace/atendimento?codigo={codigo}"
    else:
        url = f"{site_url}/chamado/{codigo}"

    assunto = f"[Portal Bondmann] Nova mensagem no chamado {codigo}"

    # Versão em texto puro (fallback)
    corpo_texto = (
        f"Olá,\n\n"
        f"Você recebeu uma nova mensagem no chat do chamado {codigo} ({titulo}):\n\n"
        f"\"{conteudo}\"\n\n"
        f"Para visualizar e responder, acesse: {url}\n\n"
        f"Atenciosamente,\n"
        f"Portal de Chamados Bondmann Química\n"
    )

    # Versão HTML organizada e responsiva
    corpo_html = f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <style>
    body {{
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
      background-color: #f8fafc;
      color: #334155;
      margin: 0;
      padding: 0;
      -webkit-font-smoothing: antialiased;
    }}
    .wrapper {{
      width: 100%;
      background-color: #f8fafc;
      padding: 30px 15px;
    }}
    .container {{
      max-width: 600px;
      margin: 0 auto;
      background-color: #ffffff;
      border-radius: 12px;
      overflow: hidden;
      box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.05), 0 2px 4px -2px rgba(0, 0, 0, 0.05);
      border: 1px solid #e2e8f0;
    }}
    .header {{
      background-color: #1e293b;
      padding: 24px;
      text-align: center;
    }}
    .header-logo {{
      color: #ffffff;
      font-weight: 800;
      font-size: 20px;
      letter-spacing: 0.05em;
    }}
    .header-sub {{
      color: #1d9e75;
      font-size: 10px;
      font-weight: bold;
      letter-spacing: 0.3em;
      margin-top: 4px;
    }}
    .content {{
      padding: 32px 24px;
    }}
    .title {{
      font-size: 18px;
      font-weight: 700;
      color: #0f172a;
      margin-top: 0;
      margin-bottom: 8px;
    }}
    .subtitle {{
      font-size: 13px;
      color: #64748b;
      margin-bottom: 24px;
    }}
    .message-box {{
      background-color: #f1f5f9;
      border-left: 4px solid #1d9e75;
      border-radius: 4px;
      padding: 16px;
      margin-bottom: 28px;
      font-size: 15px;
      line-height: 1.6;
      color: #334155;
    }}
    .btn-container {{
      text-align: center;
      margin-bottom: 24px;
    }}
    .btn {{
      display: inline-block;
      background-color: #1e293b;
      color: #ffffff !important;
      text-decoration: none;
      padding: 12px 24px;
      font-size: 14px;
      font-weight: 600;
      border-radius: 6px;
      box-shadow: 0 1px 2px 0 rgba(0, 0, 0, 0.05);
    }}
    .footer {{
      background-color: #f8fafc;
      padding: 24px;
      text-align: center;
      font-size: 11px;
      color: #94a3b8;
      border-top: 1px solid #e2e8f0;
    }}
  </style>
</head>
<body>
  <div class="wrapper">
    <div class="container">
      <div class="header">
        <div class="header-logo">BONDMANN</div>
        <div class="header-sub">PORTAL DE CHAMADOS</div>
      </div>
      <div class="content">
        <h2 class="title">Nova mensagem no chamado {codigo}</h2>
        <div class="subtitle">Assunto: <strong>{titulo}</strong></div>
        
        <p style="margin-top:0; font-size:14px; color: #475569;">Você recebeu uma nova mensagem no chat do atendimento:</p>
        
        <div class="message-box">
          "{conteudo}"
        </div>
        
        <div class="btn-container">
          <a href="{url}" class="btn">Visualizar e Responder</a>
        </div>
        
        <p style="font-size: 12px; color: #94a3b8; margin-bottom: 0;">Se o botão não funcionar, copie e cole o link no seu navegador:<br><a href="{url}" style="color: #1d9e75; text-decoration: none;">{url}</a></p>
      </div>
      <div class="footer">
        Este é um e-mail automático enviado pelo Portal de Chamados Bondmann Química.<br>
        Por favor, não responda diretamente a este endereço de e-mail.
      </div>
    </div>
  </div>
</body>
</html>
"""

    enviar_email_smtp(email, assunto, corpo_texto, corpo_html)
