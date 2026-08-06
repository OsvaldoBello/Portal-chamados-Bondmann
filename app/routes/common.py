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

import json
import logging
import re
from email.utils import parseaddr

from fastapi import APIRouter, BackgroundTasks, Depends, Request
from fastapi.responses import JSONResponse

from app.anexos import MAX_ANEXOS
from app.auth.dependencies import CurrentUser, get_current_user
from app.auth.session import current_access_token
from app.config import get_settings
from app.ia import triagem
from app.repositories.chamados import ChamadosRepo, get_chamados_repo
from app.security.uploads import UploadInvalido, validar_anexo
from app.storage import AnexosStorage, StorageError, ensure_storage
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
    token = current_access_token(request)
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


# Delimitadores que marcam o INÍCIO da citação histórica (tudo dali pra baixo é lixo).
_RE_CITACAO = [
    r"^\s*on\s+.*\s+wrote:\s*$",
    r"^\s*em\s+.*\s+escreveu:\s*$",
    r"^\s*-+\s*Original Message\s*-+",
    r"^\s*-+\s*Mensagem Original\s*-+",
    r"^\s*De:\s*.+",
    r"^\s*From:\s*.+",
    r"^\s*Enviada em:\s*.+",
    r"^\s*Sent:\s*.+",
    r"^_{5,}\s*$",                       # linha de sublinhados do Outlook
    r"^\s*--\s*$",                       # delimitador RFC de assinatura
    r"^\s*(Obter|Baixar) o Outlook",     # rodapé "Obter o Outlook para..."
    r"^\s*Get Outlook for",
    r"^\s*(Enviado do meu|Sent from my)\b",
]

# Artefatos da conversão HTML→texto de assinaturas (imagens embutidas e links).
_RE_CID = re.compile(r"\[cid:[^\]]+\]", re.IGNORECASE)
_RE_URL_ANGULO = re.compile(r"<https?://[^>]+>", re.IGNORECASE)


def extrair_resposta_email(texto: str, nome_remetente: str | None = None) -> str:
    """Extrai apenas a resposta recente do e-mail, removendo a citação histórica
    e a assinatura (best-effort). O Outlook não usa o delimitador ``-- ``, então,
    além dos delimitadores conhecidos, usamos o **nome do remetente** como âncora:
    numa assinatura corporativa a linha do nome quase sempre inicia o bloco.
    """
    if not texto:
        return ""

    # Normaliza a âncora do nome (ex.: "Felipe Schöffler") para comparação simples.
    nome_norm = None
    if nome_remetente:
        nome_norm = re.sub(r"\s+", " ", nome_remetente).strip().casefold()
        if len(nome_norm) < 3:
            nome_norm = None

    linhas_resultado = []
    viu_conteudo = False
    for linha in texto.splitlines():
        # Corta na citação histórica.
        if any(re.search(p, linha, re.IGNORECASE) for p in _RE_CITACAO):
            break
        # Corta na assinatura: linha == nome do remetente (após já haver conteúdo).
        if nome_norm and viu_conteudo:
            linha_norm = re.sub(r"\s+", " ", linha).strip().casefold()
            if linha_norm == nome_norm:
                break
        linhas_resultado.append(linha)
        if linha.strip():
            viu_conteudo = True

    resultado = "\n".join(linhas_resultado)
    # Remove artefatos de imagens embutidas e links entre <> deixados pela assinatura.
    resultado = _RE_CID.sub("", resultado)
    resultado = _RE_URL_ANGULO.sub("", resultado)
    # Colapsa 3+ quebras de linha em no máximo duas.
    resultado = re.sub(r"\n{3,}", "\n\n", resultado)
    return resultado.strip()


async def _extrair_anexos_inbound(data: dict) -> list:
    """Lê e valida os arquivos anexados no webhook (campos ``attachment-1..N``
    do Mailgun, ``attachment-count`` informa o total). Sem isso, uma imagem
    anexada na resposta por e-mail era descartada silenciosamente (só o texto
    ia pro chat — às vezes com um link/cid solto da assinatura).

    Best-effort: um arquivo inválido (tipo não permitido, tamanho excedido) é
    apenas descartado com log — não derruba o processamento da resposta.
    """
    try:
        total = int(data.get("attachment-count") or 0)
    except (TypeError, ValueError):
        total = 0

    settings = get_settings()
    validados = []
    for i in range(1, min(total, MAX_ANEXOS) + 1):
        arquivo = data.get(f"attachment-{i}")
        filename = getattr(arquivo, "filename", None)
        if arquivo is None or not filename:
            continue
        conteudo = await arquivo.read()
        try:
            validados.append(
                validar_anexo(filename, conteudo, max_bytes=settings.anexo_max_bytes)
            )
        except UploadInvalido as exc:
            log.warning(f"Anexo inbound rejeitado ({filename}): {exc}")
    return validados


async def _enviar_anexos_inbound(validados: list, *, empresa_id: str, chamado_id: str) -> list[dict]:
    """Envia os anexos já validados ao Storage privado usando a service_role key.

    Rota de webhook server-to-server (sem sessão de usuário/cookie JWT), então
    não há como autenticar o upload como o remetente faria pelo Portal/Workspace
    (Seção 1.4 / 3.9): usamos a chave admin para este caso pontual e auditado,
    já autorizado pela verificação HMAC do token de resposta feita antes."""
    if not validados:
        return []
    settings = get_settings()
    if not settings.supabase_service_role_key:
        log.warning("Anexos inbound descartados: SUPABASE_SERVICE_ROLE_KEY não configurada.")
        return []
    storage = await ensure_storage()
    if storage is None:
        log.warning("Anexos inbound descartados: Storage não configurado.")
        return []
    anexos: list[dict] = []
    for v in validados:
        path = AnexosStorage.path(empresa_id, chamado_id, v.nome_objeto)
        try:
            await storage.upload(settings.supabase_service_role_key, path, v.conteudo, v.mime)
        except StorageError as exc:
            log.warning(f"Falha ao subir anexo inbound ({v.nome_original}): {exc}")
            continue
        anexos.append({"path": path, "nome": v.nome_original, "mime": v.mime, "tamanho": v.tamanho})
    return anexos


@router.post("/api/inbound-email")
async def inbound_email(
    request: Request,
    background_tasks: BackgroundTasks
):
    """Recebe e-mails de resposta do usuário via webhook do provedor transacional.
    Verifica o token HMAC de segurança e insere a mensagem diretamente no chat do chamado.

    Sprint 1 / item 1.3 (M4): exige INBOUND_EMAIL_SECRET dedicado — sem ele a
    rota fica desabilitada (nunca reaproveita o SESSION_SECRET, que autentica
    sessões de usuário, para validar tokens expostos publicamente em endereços
    de e-mail).
    """
    from app.db import admin_connection
    from app.notification import validar_token_resposta

    settings = get_settings()
    if not settings.inbound_email_secret:
        log.warning("Inbound de e-mail: INBOUND_EMAIL_SECRET ausente — rota desabilitada.")
        return JSONResponse({"error": "Inbound de e-mail não configurado"}, status_code=503)

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

    content = data.get("stripped-text") or data.get("body-plain") or data.get("text") or ""

    if not recipient or not sender:
        log.warning(f"Inbound webhook received incomplete data: recipient={recipient}, sender={sender}")
        return JSONResponse({"error": "Dados incompletos"}, status_code=400)

    # Extrai o nome de exibição e o endereço puro do remetente. O nome ("Felipe
    # Schöffler") vira âncora para cortar a assinatura no parser abaixo.
    sender_nome, sender_email = parseaddr(str(sender))
    sender_email = sender_email.strip().lower()

    # Identifica o chamado e o token na caixa de entrada
    # Regex casa: chamado+<codigo>+<token>@
    match = re.search(r"chamado\+([a-zA-Z0-9\-]+)\+([a-f0-9]+)@", str(recipient), re.IGNORECASE)
    if not match:
        log.warning(f"Inbound recipient does not match route pattern: {recipient}")
        return JSONResponse({"error": "Destinatário inválido"}, status_code=400)

    codigo = match.group(1).upper().strip()
    token = match.group(2).strip()

    cleaned_content = extrair_resposta_email(str(content), nome_remetente=sender_nome) if content else ""

    # Anexos (ex.: imagem colada/anexada na resposta) — lidos e validados antes
    # de tocar no banco; um arquivo inválido não derruba a resposta em texto.
    anexos_validados = await _extrair_anexos_inbound(data)

    if not cleaned_content and not anexos_validados:
        log.warning("Cleaned inbound email content is empty and no valid attachments.")
        return JSONResponse({"error": "Conteúdo vazio"}, status_code=400)

    # Conecta no banco sem claims (modo Admin) para tratar a verificação e a inserção
    async with admin_connection() as conn:
        # 1. Recupera informações do chamado
        chamado_row = await conn.fetchrow(
            """
            SELECT c.id, c.codigo, c.titulo, c.cliente_id, c.operador_id, c.empresa_id,
                   d.nome AS departamento
              FROM chamados c
              LEFT JOIN departamentos d ON d.id = c.departamento_id
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
        if not validar_token_resposta(codigo, sender_id, token, settings.inbound_email_secret):
            log.warning(f"Invalid reply signature token for user {sender_id} on ticket {codigo}.")
            return JSONResponse({"error": "Assinatura inválida"}, status_code=403)

        # 4.5. Envia os anexos validados ao Storage (imagem etc. vira anexo real
        # no chat, não só um link/cid solto vindo da assinatura do e-mail).
        anexos = await _enviar_anexos_inbound(
            anexos_validados, empresa_id=str(chamado.get("empresa_id") or ""), chamado_id=str(chamado["id"])
        )

        # 5. Insere a resposta no chat do chamado (como mensagem pública)
        msg_row = await conn.fetchrow(
            """
            INSERT INTO mensagens (chamado_id, remetente_id, conteudo, is_interna, anexos)
            VALUES ($1::uuid, $2::uuid, $3, false, $4::jsonb)
            RETURNING id, created_at
            """,
            chamado["id"],
            sender_id,
            cleaned_content,
            json.dumps(anexos),
        )

        log.info(f"Processed inbound reply from {sender_email} on ticket {codigo}: msg {msg_row['id']}")

        # "Em cópia" (Fase 8): quem acompanha o chamado também é avisado da
        # resposta chegada por e-mail, mesma regra do fluxo pelo chat.
        observadores_rows = await conn.fetch(
            "SELECT perfil_id FROM chamados_observadores WHERE chamado_id = $1::uuid",
            chamado["id"],
        )

    # 6. Notifica o outro participante (e quem está em cópia) via e-mail
    from app.notification import agendar_notificacao_email
    await agendar_notificacao_email(
        background_tasks,
        chamado,
        sender_id,
        cleaned_content or "[Imagem anexada]",
        observadores=[str(r["perfil_id"]) for r in observadores_rows],
    )

    # 7. Re-triagem por IA (F2): resposta do AUTOR por e-mail num depto habilitado
    # reagenda a triagem — o motor revalida rodada/estado antes de agir.
    if is_client and triagem.deve_triar(chamado.get("departamento"), settings):
        triagem.agendar_triagem(str(chamado["id"]))

    return JSONResponse({"success": True, "message_id": str(msg_row["id"])})


def register_common_routes(app) -> None:
    app.include_router(router)
