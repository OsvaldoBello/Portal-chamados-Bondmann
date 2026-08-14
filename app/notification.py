import asyncio
import hashlib
import hmac
import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import httpx
from fastapi import BackgroundTasks

from app.auth.supabase_client import ensure_admin_client
from app.config import get_settings

log = logging.getLogger("app.notification")


def gerar_token_resposta(codigo: str, usuario_id: str, secret: str) -> str:
    """Gera um token criptográfico determinístico e curto para identificar e validar
    a resposta de e-mail do usuário para um determinado chamado. ``secret`` deve ser
    o INBOUND_EMAIL_SECRET dedicado (Sprint 1 / item 1.3) — nunca um segredo
    reaproveitado de outro contexto (ex.: SESSION_SECRET).
    """
    msg = f"{codigo.upper().strip()}:{str(usuario_id).strip()}"
    return hmac.new(secret.encode('utf-8'), msg.encode('utf-8'), hashlib.sha256).hexdigest()[:16]


def validar_token_resposta(codigo: str, usuario_id: str, token: str, secret: str) -> bool:
    """Verifica se o token fornecido confere com o token esperado para o chamado e usuário."""
    esperado = gerar_token_resposta(codigo, usuario_id, secret)
    return hmac.compare_digest(esperado, token)


async def enviar_email(para: str, assunto: str, corpo_texto: str, corpo_html: str = None, reply_to: str = None) -> bool:
    """Dispatcher de envio de e-mail. Prefere a API HTTP do Mailgun (sem socket
    bloqueante no event loop); se ela não estiver configurada, cai para SMTP
    (executado fora do event loop para não bloquear). Retorna ``True`` se o
    e-mail saiu.

    Nunca levanta exceção para o chamador — falha de e-mail não deve quebrar o
    fluxo do chat (Seção 6.3). O motivo real vai para o log.
    """
    settings = get_settings()

    if settings.mailgun_ativo:
        try:
            await _enviar_via_mailgun_api(para, assunto, corpo_texto, corpo_html, reply_to)
            return True
        except Exception as e:
            log.error(f"[EMAIL MAILGUN ERROR] Falha ao enviar via Mailgun para {para}: {e}")
            # Cai para SMTP se disponível; senão, encerra com log abaixo.

    if settings.smtp_host and settings.smtp_user and settings.smtp_password:
        # smtplib é bloqueante: roda em thread para não travar o event loop.
        return await asyncio.to_thread(
            enviar_email_smtp, para, assunto, corpo_texto, corpo_html, reply_to
        )

    faltando = []
    if not settings.mailgun_api_key:
        faltando.append("MAILGUN_API_KEY")
    if not settings.mailgun_domain:
        faltando.append("MAILGUN_DOMAIN")
    log.warning(
        f"[EMAIL NÃO CONFIGURADO] E-mail NÃO enviado para {para}. "
        f"Configure o Mailgun (faltando: {', '.join(faltando) or 'nenhuma'}) "
        f"ou as credenciais SMTP. Assunto: {assunto}"
    )
    return False


async def _enviar_via_mailgun_api(para: str, assunto: str, corpo_texto: str, corpo_html: str = None, reply_to: str = None) -> None:
    """Envia o e-mail pela API HTTP do Mailgun (POST multipart, HTTP basic auth).
    Assíncrono via httpx — sem socket SMTP bloqueante. Levanta em caso de falha
    para o dispatcher decidir o fallback.
    """
    settings = get_settings()
    url = f"{settings.mailgun_base_url.rstrip('/')}/v3/{settings.mailgun_domain}/messages"
    data = {
        "from": settings.email_from,
        "to": para,
        "subject": assunto,
        "text": corpo_texto,
    }
    if corpo_html:
        data["html"] = corpo_html
    if reply_to:
        data["h:Reply-To"] = reply_to

    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(url, auth=("api", settings.mailgun_api_key), data=data)
        resp.raise_for_status()
    log.info(f"[EMAIL MAILGUN SUCCESS] E-mail enviado para {para} (HTTP {resp.status_code})")


def enviar_email_smtp(para: str, assunto: str, corpo_texto: str, corpo_html: str = None, reply_to: str = None) -> bool:
    """Envia um e-mail via SMTP (fallback). Bloqueante — chamar via
    ``asyncio.to_thread``. Retorna ``True`` em sucesso, ``False`` em falha.
    """
    settings = get_settings()
    if not (settings.smtp_host and settings.smtp_user and settings.smtp_password):
        log.warning(f"[EMAIL SMTP MOCK] SMTP não configurado. E-mail NÃO enviado para {para} | Subject: {assunto}")
        return False

    try:
        msg = MIMEMultipart('alternative')
        msg['From'] = settings.email_from
        msg['To'] = para
        msg['Subject'] = assunto
        if reply_to:
            msg['Reply-To'] = reply_to

        msg.attach(MIMEText(corpo_texto, 'plain', 'utf-8'))
        if corpo_html:
            msg.attach(MIMEText(corpo_html, 'html', 'utf-8'))

        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=15) as server:
            server.starttls()
            server.login(settings.smtp_user, settings.smtp_password)
            server.send_message(msg)
        log.info(f"[EMAIL SMTP SUCCESS] Email successfully sent to {para}")
        return True
    except Exception as e:
        log.error(f"[EMAIL SMTP ERROR] Failed to send email to {para}: {e}")
        return False


async def notificar_nova_mensagem_email(
    chamado: dict, remetente_id: str, conteudo: str, observadores: list[str] | None = None
) -> None:
    """Identifica o destinatário (cliente ou operador) e envia uma notificação por e-mail
    com layout HTML organizado e cabeçalho Reply-To para processamento inbound.

    ``observadores`` (Fase 8 — "em cópia"): perfil_ids de quem acompanha o
    chamado sem poder responder por ele. Eles também recebem um aviso por
    e-mail (até aqui só o sino/Realtime avisava, e só pra quem já estivesse
    com a tela do Workspace/Portal aberta) — sempre link de leitura (rota do
    Portal, que qualquer perfil com RLS de observador consegue abrir) e sem
    Reply-To: quem está em cópia não responde pelo chamado, nem por e-mail.
    """
    cliente_id = str(chamado.get("cliente_id"))
    operador_id = str(chamado.get("operador_id")) if chamado.get("operador_id") else None

    # Se a mensagem foi enviada pelo cliente, o destinatário é o operador.
    # Caso contrário, o destinatário é o cliente.
    destinatario_id = operador_id if str(remetente_id) == cliente_id else cliente_id

    client = await ensure_admin_client()
    if not client:
        log.warning("Could not load email recipients: Supabase admin client is not configured.")
        return

    if destinatario_id:
        await _notificar_destinatario_mensagem(client, chamado, destinatario_id, operador_id, conteudo)

    ja_notificados = {str(remetente_id), destinatario_id} if destinatario_id else {str(remetente_id)}
    for observador_id in {str(o) for o in (observadores or []) if o}:
        if observador_id in ja_notificados:
            continue
        await _notificar_observador_mensagem(client, chamado, observador_id, conteudo)


async def _notificar_destinatario_mensagem(
    client, chamado: dict, destinatario_id: str, operador_id: str | None, conteudo: str
) -> None:
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
    chamado_id = chamado.get("id")

    # Gerar a URL correta com base no destinatário (rotas reais da app: o
    # detalhe do chamado é resolvido por id/uuid, não pelo código BOND-...).
    site_url = settings.site_url.rstrip("/")
    if str(destinatario_id) == operador_id:
        url = f"{site_url}/workspace/chamados/{chamado_id}"
    else:
        url = f"{site_url}/portal/chamados/{chamado_id}"

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
        <h2 class="title" style="margin-top: 0; margin-bottom: 8px;">Nova mensagem no chamado {codigo}</h2>
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

    reply_to = None
    if settings.inbound_email_domain and settings.inbound_email_secret:
        token = gerar_token_resposta(codigo, destinatario_id, settings.inbound_email_secret)
        reply_to = f"chamado+{codigo.lower()}+{token}@{settings.inbound_email_domain}"
    elif settings.inbound_email_domain:
        # Sprint 1 / item 1.3 (M4): sem segredo dedicado não geramos reply-to —
        # nunca reaproveitamos o SESSION_SECRET para assinar tokens expostos
        # publicamente no endereço de e-mail.
        log.warning(
            "Inbound de e-mail: INBOUND_EMAIL_DOMAIN configurado sem "
            "INBOUND_EMAIL_SECRET dedicado — reply-to desativado nesta notificação."
        )

    await enviar_email(email, assunto, corpo_texto, corpo_html, reply_to=reply_to)


async def _notificar_observador_mensagem(client, chamado: dict, observador_id: str, conteudo: str) -> None:
    """Avisa quem está "em cópia" (Fase 8) de uma nova mensagem pública no
    chamado. Link sempre pela rota do Portal — a RLS de observador dá
    visibilidade ali independente do departamento/role de quem está em cópia
    (diferente do Workspace, restrito ao staff do próprio setor). Sem
    Reply-To: em cópia é só leitura, nunca responde pelo chamado."""
    try:
        res = await client.auth.admin.get_user_by_id(observador_id)
        u = getattr(res, "user", res)
        email = getattr(u, "email", None)
        if not email:
            log.warning(f"Observador {observador_id} sem e-mail cadastrado.")
            return
    except Exception as e:
        log.error(f"Erro ao buscar e-mail do observador {observador_id}: {e}")
        return

    settings = get_settings()
    codigo = chamado.get("codigo", "")
    titulo = chamado.get("titulo", "")
    chamado_id = chamado.get("id")
    site_url = settings.site_url.rstrip("/")
    url = f"{site_url}/portal/chamados/{chamado_id}"

    assunto = f"[Portal Bondmann] Nova mensagem no chamado {codigo} (você está em cópia)"

    corpo_texto = (
        f"Olá,\n\n"
        f"Você está em cópia no chamado {codigo} ({titulo}) e há uma nova mensagem:\n\n"
        f"\"{conteudo}\"\n\n"
        f"Para acompanhar, acesse: {url}\n\n"
        f"Atenciosamente,\n"
        f"Portal de Chamados Bondmann Química\n"
    )

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
    .wrapper {{ width: 100%; background-color: #f8fafc; padding: 30px 15px; }}
    .container {{
      max-width: 600px;
      margin: 0 auto;
      background-color: #ffffff;
      border-radius: 12px;
      overflow: hidden;
      box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.05), 0 2px 4px -2px rgba(0, 0, 0, 0.05);
      border: 1px solid #e2e8f0;
    }}
    .header {{ background-color: #1e293b; padding: 24px; text-align: center; }}
    .header-logo {{ color: #ffffff; font-weight: 800; font-size: 20px; letter-spacing: 0.05em; }}
    .header-sub {{ color: #1d9e75; font-size: 10px; font-weight: bold; letter-spacing: 0.3em; margin-top: 4px; }}
    .content {{ padding: 32px 24px; }}
    .title {{ font-size: 18px; font-weight: 700; color: #0f172a; margin-top: 0; margin-bottom: 8px; }}
    .subtitle {{ font-size: 13px; color: #64748b; margin-bottom: 24px; }}
    .cc-tag {{
      display: inline-block;
      background-color: #f1f5f9;
      color: #475569;
      font-size: 11px;
      font-weight: 700;
      letter-spacing: 0.03em;
      border-radius: 999px;
      padding: 3px 10px;
      margin-bottom: 16px;
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
    .btn-container {{ text-align: center; margin-bottom: 24px; }}
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
        <div class="cc-tag">EM CÓPIA</div>
        <h2 class="title" style="margin-top: 0; margin-bottom: 8px;">Nova mensagem no chamado {codigo}</h2>
        <div class="subtitle">Assunto: <strong>{titulo}</strong></div>

        <p style="margin-top:0; font-size:14px; color: #475569;">Você está em cópia neste chamado e recebeu uma nova mensagem:</p>

        <div class="message-box">
          "{conteudo}"
        </div>

        <div class="btn-container">
          <a href="{url}" class="btn">Acompanhar chamado</a>
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

    await enviar_email(email, assunto, corpo_texto, corpo_html)


async def notificar_novo_usuario_email(nome: str, email: str) -> None:
    """E-mail de boas-vindas ao criar uma conta pelo painel Admin (2026-07-21):
    instrui o primeiro acesso via "Esqueci minha senha" (o TI cria a conta com
    uma senha provisória — trocar por uma própria é o próprio fluxo de OTP de
    recuperação do GoTrue, já existente em ``/esqueci-senha``, em vez de um
    link mágico separado pra manter)."""
    settings = get_settings()
    site_url = settings.site_url.rstrip("/")
    url_login = f"{site_url}/login"
    url_senha = f"{site_url}/esqueci-senha"

    assunto = "[Portal Bondmann] Sua conta foi criada — primeiro acesso"

    corpo_texto = (
        f"Olá, {nome}!\n\n"
        f"Uma conta foi criada para você no Portal de Chamados Bondmann Química.\n\n"
        f"Para o primeiro acesso:\n"
        f"1. Acesse {url_login}\n"
        f"2. Clique em \"Esqueci minha senha\" ({url_senha}) e informe o e-mail {email}\n"
        f"3. Defina sua própria senha a partir do código enviado por e-mail\n\n"
        f"Atenciosamente,\n"
        f"Portal de Chamados Bondmann Química\n"
    )

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
    .wrapper {{ width: 100%; background-color: #f8fafc; padding: 30px 15px; }}
    .container {{
      max-width: 600px;
      margin: 0 auto;
      background-color: #ffffff;
      border-radius: 12px;
      overflow: hidden;
      box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.05), 0 2px 4px -2px rgba(0, 0, 0, 0.05);
      border: 1px solid #e2e8f0;
    }}
    .header {{ background-color: #1e293b; padding: 24px; text-align: center; }}
    .header-logo {{ color: #ffffff; font-weight: 800; font-size: 20px; letter-spacing: 0.05em; }}
    .header-sub {{ color: #1d9e75; font-size: 10px; font-weight: bold; letter-spacing: 0.3em; margin-top: 4px; }}
    .content {{ padding: 32px 24px; }}
    .title {{ font-size: 18px; font-weight: 700; color: #0f172a; margin-top: 0; margin-bottom: 8px; }}
    .steps {{
      background-color: #f1f5f9;
      border-left: 4px solid #1d9e75;
      border-radius: 4px;
      padding: 16px;
      margin-bottom: 24px;
      font-size: 14px;
      line-height: 1.8;
      color: #334155;
    }}
    .btn-container {{ text-align: center; margin-bottom: 24px; }}
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
        <h2 class="title">Bem-vindo(a), {nome}!</h2>
        <p style="margin-top:0; font-size:14px; color: #475569;">Sua conta no Portal de Chamados foi criada. Para o primeiro acesso:</p>
        <div class="steps">
          1. Acesse o portal<br>
          2. Clique em <strong>"Esqueci minha senha"</strong><br>
          3. Informe o e-mail <strong>{email}</strong> e defina sua própria senha
        </div>
        <div class="btn-container">
          <a href="{url_senha}" class="btn">Definir minha senha</a>
        </div>
        <p style="font-size: 12px; color: #94a3b8; margin-bottom: 0;">Se o botão não funcionar, copie e cole o link no seu navegador:<br><a href="{url_senha}" style="color: #1d9e75; text-decoration: none;">{url_senha}</a></p>
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

    await enviar_email(email, assunto, corpo_texto, corpo_html)


async def enviar_codigo_mfa_email(email: str, codigo: str) -> bool:
    """Código de 6 dígitos da verificação em duas etapas por e-mail
    (``app/auth/mfa_email.py``). Enviado com ``await`` direto (sem
    ``BackgroundTasks``) — ao contrário das notificações abaixo, o usuário
    precisa do código na hora, não "eventualmente"."""
    assunto = "[Portal Bondmann] Seu código de verificação"

    corpo_texto = (
        f"Seu código de verificação em duas etapas é: {codigo}\n\n"
        f"Ele expira em 10 minutos e serve só para confirmar o acesso à sua "
        f"conta no Portal de Chamados Bondmann Química. Não compartilhe este "
        f"código com ninguém.\n\n"
        f"Se você não pediu este código, ignore este e-mail.\n\n"
        f"Atenciosamente,\n"
        f"Portal de Chamados Bondmann Química\n"
    )

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
    .wrapper {{ width: 100%; background-color: #f8fafc; padding: 30px 15px; }}
    .container {{
      max-width: 600px;
      margin: 0 auto;
      background-color: #ffffff;
      border-radius: 12px;
      overflow: hidden;
      box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.05), 0 2px 4px -2px rgba(0, 0, 0, 0.05);
      border: 1px solid #e2e8f0;
    }}
    .header {{ background-color: #1e293b; padding: 24px; text-align: center; }}
    .header-logo {{ color: #ffffff; font-weight: 800; font-size: 20px; letter-spacing: 0.05em; }}
    .header-sub {{ color: #1d9e75; font-size: 10px; font-weight: bold; letter-spacing: 0.3em; margin-top: 4px; }}
    .content {{ padding: 32px 24px; }}
    .title {{ font-size: 18px; font-weight: 700; color: #0f172a; margin-top: 0; margin-bottom: 8px; }}
    .codigo-box {{
      background-color: #f1f5f9;
      border-left: 4px solid #1d9e75;
      border-radius: 4px;
      padding: 20px;
      margin-bottom: 24px;
      text-align: center;
    }}
    .codigo {{
      font-family: "SF Mono", "Courier New", monospace;
      font-size: 32px;
      font-weight: 700;
      letter-spacing: 0.3em;
      color: #0f172a;
    }}
    .aviso {{ font-size: 12px; color: #94a3b8; margin-top: 8px; }}
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
        <h2 class="title">Seu código de verificação</h2>
        <p style="margin-top:0; font-size:14px; color: #475569;">Use o código abaixo para confirmar o acesso à sua conta:</p>
        <div class="codigo-box">
          <div class="codigo">{codigo}</div>
          <p class="aviso">Expira em 10 minutos. Não compartilhe este código com ninguém.</p>
        </div>
        <p style="font-size: 12px; color: #94a3b8; margin-bottom: 0;">Se você não pediu este código, pode ignorar este e-mail com segurança.</p>
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

    return await enviar_email(email, assunto, corpo_texto, corpo_html)


async def agendar_notificacao_email(
    background_tasks: BackgroundTasks,
    chamado: dict,
    remetente_id: str,
    conteudo: str,
    observadores: list[str] | None = None,
) -> None:
    """Dispara a notificação de e-mail de forma assíncrona via BackgroundTasks.

    Processo persistente (Railway — Sprint 1 / item 1.8, M10: alvo único de
    deploy, decisão 2026-07-15): BackgroundTasks roda com garantia de conclusão
    após a resposta, sem o modo inline que a Vercel serverless exigia (função
    efêmera derrubada logo após responder, o que perdia o envio)."""
    log.info("[NOTIFICATION] Queueing background task for email notification")
    background_tasks.add_task(notificar_nova_mensagem_email, chamado, remetente_id, conteudo, observadores)


async def notificar_novo_chamado_email(chamado: dict, destinatarios_ids: list[str]) -> None:
    """Avisa a equipe do departamento de destino quando um chamado novo entra
    na fila. Antes desta função só existia notificação de e-mail para
    respostas/mensagens — a abertura de um chamado dependia inteiramente do
    sino/Realtime do Workspace, então o setor só sabia de um chamado NOVO
    se alguém estivesse com a tela aberta no momento (motivo do TI relatar
    que não recebia aviso nenhum de chamado novo)."""
    ids = {str(d) for d in destinatarios_ids if d}
    if not ids:
        return

    client = await ensure_admin_client()
    if not client:
        log.warning("Novo chamado: Supabase admin client não configurado, notificação da equipe pulada.")
        return

    settings = get_settings()
    codigo = chamado.get("codigo", "")
    titulo = chamado.get("titulo", "")
    chamado_id = chamado.get("id")
    departamento_nome = chamado.get("departamento_nome") or ""
    site_url = settings.site_url.rstrip("/")
    url = f"{site_url}/workspace/chamados/{chamado_id}"

    assunto = f"[Portal Bondmann] Novo chamado na fila do {departamento_nome}: {codigo}"

    corpo_texto = (
        f"Olá,\n\n"
        f"Um novo chamado entrou na fila do setor {departamento_nome}:\n\n"
        f"{codigo} — {titulo}\n\n"
        f"Para visualizar e atender, acesse: {url}\n\n"
        f"Atenciosamente,\n"
        f"Portal de Chamados Bondmann Química\n"
    )

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
    .wrapper {{ width: 100%; background-color: #f8fafc; padding: 30px 15px; }}
    .container {{
      max-width: 600px;
      margin: 0 auto;
      background-color: #ffffff;
      border-radius: 12px;
      overflow: hidden;
      box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.05), 0 2px 4px -2px rgba(0, 0, 0, 0.05);
      border: 1px solid #e2e8f0;
    }}
    .header {{ background-color: #1e293b; padding: 24px; text-align: center; }}
    .header-logo {{ color: #ffffff; font-weight: 800; font-size: 20px; letter-spacing: 0.05em; }}
    .header-sub {{ color: #1d9e75; font-size: 10px; font-weight: bold; letter-spacing: 0.3em; margin-top: 4px; }}
    .content {{ padding: 32px 24px; }}
    .title {{ font-size: 18px; font-weight: 700; color: #0f172a; margin-top: 0; margin-bottom: 8px; }}
    .subtitle {{ font-size: 13px; color: #64748b; margin-bottom: 24px; }}
    .btn-container {{ text-align: center; margin-bottom: 24px; }}
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
        <h2 class="title" style="margin-top: 0; margin-bottom: 8px;">Novo chamado na fila</h2>
        <div class="subtitle">Setor: <strong>{departamento_nome}</strong></div>

        <p style="margin-top:0; font-size:14px; color: #475569;">{codigo} — {titulo}</p>

        <div class="btn-container">
          <a href="{url}" class="btn">Visualizar e atender</a>
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

    for destinatario_id in ids:
        try:
            res = await client.auth.admin.get_user_by_id(destinatario_id)
            u = getattr(res, "user", res)
            email = getattr(u, "email", None)
            if not email:
                log.warning(f"Staff {destinatario_id} sem e-mail cadastrado.")
                continue
        except Exception as e:
            log.error(f"Erro ao buscar e-mail do staff {destinatario_id}: {e}")
            continue
        await enviar_email(email, assunto, corpo_texto, corpo_html)


async def agendar_notificacao_novo_chamado(
    background_tasks: BackgroundTasks, chamado: dict, destinatarios_ids: list[str]
) -> None:
    """Agenda (via BackgroundTasks) o aviso de chamado novo para a equipe do
    setor de destino — mesmo padrão de :func:`agendar_notificacao_email`."""
    if not destinatarios_ids:
        return
    log.info("[NOTIFICATION] Queueing background task for new-ticket team notification")
    background_tasks.add_task(notificar_novo_chamado_email, chamado, destinatarios_ids)
