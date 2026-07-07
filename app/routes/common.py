"""Rotas transversais a qualquer papel autenticado (ex.: sino de notificações).

O sino da topbar carrega este fragmento por HTMX ao ser aberto. A lista é
**escopada pela RLS** (funcionário vê os próprios; staff vê o seu setor; TI tudo),
então uma única rota serve portal, workspace e admin.

`/realtime/config` entrega ao browser a config mínima do Realtime (URL do
projeto + anon key + JWT do próprio usuário, lido do cookie no servidor) para o
sino em tempo real (`notificacoes.js`) e o chat. Só dados do próprio usuário —
mesma decisão da Seção 6.2 (o browser recebe URL+anon key+JWT do usuário).
"""

from __future__ import annotations

import logging
import re
from email.utils import parseaddr

from fastapi import APIRouter, Depends, Request, BackgroundTasks
from fastapi.responses import JSONResponse

from app.auth.dependencies import CurrentUser, get_current_user
from app.auth.session import ACCESS_COOKIE
from app.config import get_settings
from app.repositories.chamados import ChamadosRepo, get_chamados_repo
from app.templating import render

log = logging.getLogger("app.routes.common")

router = APIRouter(tags=["common"])


@router.get("/notificacoes")
async def notificacoes(
    request: Request,
    user: CurrentUser = Depends(get_current_user),
    repo: ChamadosRepo = Depends(get_chamados_repo),
):
    itens = await repo.notificacoes(user.claims)
    # O staff abre no workspace (atendimento); o funcionário no portal (detalhe).
    base = "/workspace/chamados" if user.role in ("OPERADOR", "ADMIN") else "/portal/chamados"
    return render(request, "_notificacoes.html", {"itens": itens, "base": base})


@router.get("/realtime/config")
async def realtime_config(
    request: Request,
    user: CurrentUser = Depends(get_current_user),
):
    """Config do Realtime para o browser (sino em tempo real + chat).

    Devolve ``{}`` quando o Supabase não está configurado ou não há sessão —
    o cliente degrada para o sino por clique / polling. O token é o do próprio
    usuário (cookie httpOnly, lido no servidor); a RLS aplica na entrega."""
    settings = get_settings()
    token = request.cookies.get(ACCESS_COOKIE)
    if not (settings.supabase_url and settings.supabase_anon_key and token):
        return JSONResponse({})
    return JSONResponse(
        {
            "url": settings.supabase_url,
            "key": settings.supabase_anon_key,
            "token": token,
            "uid": user.id,
        },
        headers={"Cache-Control": "no-store"},
    )


def extrair_resposta_email(texto: str) -> str:
    """Extrai apenas a resposta recente do e-mail do usuário,
    removendo a citação histórica e a assinatura.
    """
    if not texto:
        return ""
    
    linhas = texto.splitlines()
    linhas_resultado = []
    
    re_headers = [
        r"^\s*on\s+.*,\s+.*wrote:\s*$",
        r"^\s*em\s+.*,\s+.*escreveu:\s*$",
        r"^---+Original Message---+",
        r"^---+Mensagem Original---+",
        r"^\s*De:\s*.*",
        r"^\s*From:\s*.*",
        r"^________________________________",
        r"^--\s*$"
    ]
    
    for linha in linhas:
        matched = False
        for pattern in re_headers:
            if re.search(pattern, linha, re.IGNORECASE):
                matched = True
                break
        if matched:
            break
        linhas_resultado.append(linha)
        
    resultado = "\n".join(linhas_resultado).strip()
    return resultado


@router.post("/api/inbound-email")
async def inbound_email(
    request: Request,
    background_tasks: BackgroundTasks
):
    """Recebe e-mails de resposta do usuário via webhook do provedor transacional.
    Verifica o token HMAC de segurança e insere a mensagem diretamente no chat do chamado.
    """
    from app.db import admin_connection
    from app.notification import validar_token_resposta, notificar_nova_mensagem_email

    # Aceita payloads JSON ou Form-Data do Mailgun/SendGrid
    content_type = request.headers.get("content-type", "")
    data = {}
    if "application/json" in content_type:
        try:
            data = await request.json()
        except Exception:
            pass
    else:
        try:
            form = await request.form()
            data = dict(form)
        except Exception:
            pass

    recipient = data.get("recipient") or data.get("to")
    if not recipient and "to" in data:
        recipient = data["to"]
        
    sender = data.get("sender") or data.get("from")
    if not sender and "from" in data:
        sender = data["from"]

    content = data.get("stripped-text") or data.get("body-plain") or data.get("text")

    if not recipient or not sender or not content:
        log.warning(f"Inbound webhook received incomplete data: recipient={recipient}, sender={sender}")
        return JSONResponse({"error": "Dados incompletos"}, status_code=400)

    # Extrai o endereço de e-mail puro do remetente
    _, sender_email = parseaddr(str(sender))
    sender_email = sender_email.strip().lower()

    # Identifica o chamado e o token na caixa de entrada
    # Regex casa: chamado+<codigo>+<token>@
    match = re.search(r"chamado\+([a-zA-Z0-9\-]+)\+([a-f0-9]+)@", str(recipient), re.IGNORECASE)
    if not match:
        log.warning(f"Inbound recipient does not match route pattern: {recipient}")
        return JSONResponse({"error": "Destinatário inválido"}, status_code=400)

    codigo = match.group(1).upper().strip()
    token = match.group(2).strip()

    cleaned_content = extrair_resposta_email(str(content))
    if not cleaned_content:
        log.warning("Cleaned inbound email content is empty.")
        return JSONResponse({"error": "Conteúdo vazio"}, status_code=400)

    # Conecta no banco sem claims (modo Admin) para tratar a verificação e a inserção
    async with admin_connection() as conn:
        # 1. Recupera informações do chamado
        chamado_row = await conn.fetchrow(
            """
            SELECT c.id, c.codigo, c.titulo, c.cliente_id, c.operador_id
              FROM chamados c
             WHERE c.codigo = $1
            """,
            codigo
        )
        if not chamado_row:
            log.warning(f"Inbound ticket not found: {codigo}")
            return JSONResponse({"error": "Chamado não encontrado"}, status_code=400)
        
        chamado = dict(chamado_row)
        cliente_id = chamado["cliente_id"]
        operador_id = chamado["operador_id"]

        # 2. Localiza o perfil do usuário pelo e-mail
        profile_row = await conn.fetchrow(
            """
            SELECT p.id, p.role
              FROM perfis p
              JOIN auth.users u ON u.id = p.id
             WHERE LOWER(u.email) = $1
            """,
            sender_email
        )
        if not profile_row:
            log.warning(f"Sender email {sender_email} is not associated with any profile.")
            return JSONResponse({"error": "Remetente não cadastrado"}, status_code=400)
        
        sender_id = profile_row["id"]

        # 3. Verifica se o remetente é o cliente ou o operador deste chamado
        is_client = (str(sender_id) == str(cliente_id))
        is_operator = (operador_id and str(sender_id) == str(operador_id))

        if not is_client and not is_operator:
            log.warning(f"Sender {sender_email} ({sender_id}) is not a participant of ticket {codigo}.")
            return JSONResponse({"error": "Remetente não autorizado"}, status_code=400)

        # 4. Valida a assinatura HMAC de segurança
        settings = get_settings()
        secret = settings.inbound_email_secret or settings.session_secret
        if not validar_token_resposta(codigo, sender_id, token, secret):
            log.warning(f"Invalid reply signature token for user {sender_id} on ticket {codigo}.")
            return JSONResponse({"error": "Assinatura inválida"}, status_code=403)

        # 5. Insere a resposta no chat do chamado (como mensagem pública)
        msg_row = await conn.fetchrow(
            """
            INSERT INTO mensagens (chamado_id, remetente_id, conteudo, is_interna, anexos)
            VALUES ($1::uuid, $2::uuid, $3, false, '[]'::jsonb)
            RETURNING id, created_at
            """,
            chamado["id"],
            sender_id,
            cleaned_content
        )

        log.info(f"Processed inbound reply from {sender_email} on ticket {codigo}: msg {msg_row['id']}")

    # 6. Notifica o outro participante via e-mail em background task
    background_tasks.add_task(
        notificar_nova_mensagem_email,
        chamado,
        sender_id,
        cleaned_content
    )

    return JSONResponse({"success": True, "message_id": str(msg_row["id"])})


def register_common_routes(app) -> None:
    app.include_router(router)
