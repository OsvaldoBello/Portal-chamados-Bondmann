"""Cliente de WhatsApp com dois provedores — Meta Cloud API e WUZAPI (whatsmeow).

PROPOSTA (docs/wuzapi/): substitui `app/whatsapp_client.py` quando a migração
for aprovada. Copiar por cima do arquivo atual, aplicar o patch de
`app/config.py` (ver docs/pesquisa_wuzapi_migracao.md, Eixo 5) e nada mais
muda: as três funções públicas de hoje
(`enviar_mensagem_texto`, `enviar_documento`, `baixar_midia`) mantêm nome e
assinatura, então `app/ia/whatsapp_intake.py` continua importando as mesmas
coisas. O provedor é escolhido por `WHATSAPP_PROVIDER=meta|wuzapi`.

Diferenças que o adaptador esconde do resto da app:

- **Mídia recebida.** A Meta manda um `media_id` opaco e resolve o download em
  dois GETs. O wuzapi manda o descritor de criptografia inteiro no webhook
  (`url` + `mediaKey` + `fileEncSHA256` + …) e expõe `POST /chat/downloadX`
  para decriptar. Para caber na coluna `whatsapp_mensagens_recebidas.midia_id`
  (text) sem migration, o descritor é serializado num token opaco
  `wuz:<base64url(json)>` — ver `token_midia()` / `baixar_midia()`.
- **Documento por URL.** A Meta baixa a URL pública sozinha (`document.link`).
  O wuzapi só aceita base64, então o cliente lê o arquivo do disco (quando a
  URL é um estático da própria app) ou baixa e converte.
- **Presença ("digitando…").** Não existe na Cloud API; no wuzapi é
  `POST /chat/presence`. Exposta aqui como o context manager `digitando()`.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import mimetypes
import random
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Protocol

import httpx

from app.config import get_settings

log = logging.getLogger("app.whatsapp_client")

# Raiz dos estáticos servidos pela app — usada para ler o PDF do formulário do
# disco em vez de baixar da própria URL pública (o wuzapi exige base64).
_STATIC_ROOT = Path(__file__).resolve().parent / "static"


class WhatsAppNaoConfigurado(RuntimeError):
    """Credenciais do provedor ativo ausentes (kill switch implícito)."""


class WhatsAppEnvioFalhou(RuntimeError):
    """O provedor recusou o envio (erro 4xx/5xx ou `success: false`)."""

    def __init__(self, status_code: int, detalhe: str):
        super().__init__(f"Envio WhatsApp falhou ({status_code}): {detalhe}")
        self.status_code = status_code
        self.detalhe = detalhe


# ---------------------------------------------------------------------------
# Interface
# ---------------------------------------------------------------------------


class WhatsAppClient(Protocol):
    """Contrato mínimo que o intake e as notificações consomem."""

    async def enviar_texto(self, destinatario: str, corpo: str) -> dict: ...

    async def enviar_documento(
        self,
        destinatario: str,
        link: str,
        *,
        nome_arquivo: str | None = None,
        legenda: str | None = None,
    ) -> dict: ...

    async def baixar_midia(self, referencia: str) -> tuple[bytes, str] | None: ...

    async def presenca(self, destinatario: str, estado: str, *, media: str = "") -> None: ...


# ---------------------------------------------------------------------------
# HTTP compartilhado (só o wuzapi; a Meta segue com client por chamada)
# ---------------------------------------------------------------------------

_http: httpx.AsyncClient | None = None
_http_lock = asyncio.Lock()


async def _cliente_http() -> httpx.AsyncClient:
    """`AsyncClient` reaproveitado — o wuzapi é chamado muitas vezes por
    conversa (presença a cada ~8s durante o LLM), abrir conexão nova a cada
    chamada seria desperdício de handshake numa rede interna."""
    global _http
    if _http is None or _http.is_closed:
        async with _http_lock:
            if _http is None or _http.is_closed:
                _http = httpx.AsyncClient(
                    timeout=httpx.Timeout(get_settings().wuzapi_timeout_s),
                    limits=httpx.Limits(max_keepalive_connections=5, max_connections=10),
                )
    return _http


async def fechar_cliente_http() -> None:
    """Chamar no shutdown do lifespan (`app/main.py`)."""
    global _http
    if _http is not None and not _http.is_closed:
        await _http.aclose()
    _http = None


# ---------------------------------------------------------------------------
# Meta Cloud API
# ---------------------------------------------------------------------------


class MetaClient:
    """WhatsApp Cloud API (Graph API) — comportamento idêntico ao de hoje."""

    def _credenciais(self) -> tuple[str, str, str]:
        settings = get_settings()
        if not settings.whatsapp_access_token or not settings.whatsapp_phone_number_id:
            raise WhatsAppNaoConfigurado(
                "WHATSAPP_ACCESS_TOKEN e/ou WHATSAPP_PHONE_NUMBER_ID não configurados."
            )
        base = f"https://graph.facebook.com/{settings.whatsapp_api_version}"
        return base, settings.whatsapp_phone_number_id, settings.whatsapp_access_token

    async def _postar(self, payload: dict, *, timeout: float) -> dict:
        base, phone_id, token = self._credenciais()
        headers = {"Authorization": f"Bearer {token}"}
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(f"{base}/{phone_id}/messages", json=payload, headers=headers)
        if resp.status_code >= 400:
            log.warning("WhatsApp: envio recusado pela Graph API (status=%s)", resp.status_code)
            raise WhatsAppEnvioFalhou(resp.status_code, resp.text)
        return resp.json()

    async def enviar_texto(self, destinatario: str, corpo: str) -> dict:
        data = await self._postar(
            {
                "messaging_product": "whatsapp",
                "to": destinatario,
                "type": "text",
                "text": {"body": corpo},
            },
            timeout=10.0,
        )
        log.info(
            "WhatsApp: mensagem enviada (message_id=%s)",
            (data.get("messages") or [{}])[0].get("id"),
        )
        return data

    async def enviar_documento(
        self,
        destinatario: str,
        link: str,
        *,
        nome_arquivo: str | None = None,
        legenda: str | None = None,
    ) -> dict:
        documento: dict[str, str] = {"link": link}
        if nome_arquivo:
            documento["filename"] = nome_arquivo
        if legenda:
            documento["caption"] = legenda
        data = await self._postar(
            {
                "messaging_product": "whatsapp",
                "to": destinatario,
                "type": "document",
                "document": documento,
            },
            timeout=15.0,
        )
        log.info(
            "WhatsApp: documento enviado (message_id=%s)",
            (data.get("messages") or [{}])[0].get("id"),
        )
        return data

    async def baixar_midia(self, referencia: str) -> tuple[bytes, str] | None:
        settings = get_settings()
        if not settings.whatsapp_access_token:
            return None

        headers = {"Authorization": f"Bearer {settings.whatsapp_access_token}"}
        base = f"https://graph.facebook.com/{settings.whatsapp_api_version}"
        limite = settings.whatsapp_intake_midia_max_bytes

        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                meta = await client.get(f"{base}/{referencia}", headers=headers)
                if meta.status_code >= 400:
                    log.warning("WhatsApp: metadados de mídia recusados (status=%s)", meta.status_code)
                    return None
                info = meta.json()
                url = info.get("url")
                mime = str(info.get("mime_type") or "application/octet-stream")
                if not url:
                    return None

                declarado = int(info.get("file_size") or 0)
                if declarado and declarado > limite:
                    log.info("WhatsApp: mídia ignorada (tamanho declarado acima do teto)")
                    return None

                async with client.stream("GET", url, headers=headers) as resp:
                    if resp.status_code >= 400:
                        log.warning("WhatsApp: download de mídia recusado (status=%s)", resp.status_code)
                        return None
                    partes: list[bytes] = []
                    total = 0
                    async for pedaco in resp.aiter_bytes():
                        total += len(pedaco)
                        if total > limite:
                            log.info("WhatsApp: mídia ignorada (excedeu o teto durante o download)")
                            return None
                        partes.append(pedaco)
        except (httpx.HTTPError, ValueError, KeyError) as exc:
            log.warning("WhatsApp: falha ao baixar mídia (%s)", type(exc).__name__)
            return None

        return b"".join(partes), mime

    async def presenca(self, destinatario: str, estado: str, *, media: str = "") -> None:
        """A Cloud API não expõe "digitando…" — no-op (o adaptador segue
        funcionando com o provedor Meta ligado, só sem humanização)."""
        return None


# ---------------------------------------------------------------------------
# WUZAPI (whatsmeow)
# ---------------------------------------------------------------------------

_PREFIXO_MIDIA = "wuz:"


def token_midia(descritor: dict[str, Any]) -> str:
    """Serializa o descritor de mídia do webhook num token opaco.

    Cabe na coluna `midia_id` (text) sem migration, e volta em
    `baixar_midia()`. Os campos vêm crus do `imageMessage`/`documentMessage`
    do webhook: `Url`, `Mimetype`, `MediaKey`, `FileSHA256`, `FileEncSHA256`,
    `FileLength` — é exatamente o corpo que `POST /chat/downloadX` espera."""
    bruto = json.dumps(descritor, separators=(",", ":")).encode("utf-8")
    return _PREFIXO_MIDIA + base64.urlsafe_b64encode(bruto).decode("ascii")


def _descritor_do_token(referencia: str) -> dict[str, Any] | None:
    if not referencia.startswith(_PREFIXO_MIDIA):
        return None
    try:
        bruto = base64.urlsafe_b64decode(referencia[len(_PREFIXO_MIDIA):].encode("ascii"))
        descritor = json.loads(bruto)
    except (ValueError, json.JSONDecodeError):
        return None
    return descritor if isinstance(descritor, dict) else None


def _base64_do_payload(dado: Any) -> bytes | None:
    """Aceita tanto `"data:image/jpeg;base64,AAAA"` quanto `"AAAA"` puro."""
    if not isinstance(dado, str) or not dado:
        return None
    if dado.startswith("data:"):
        _, _, dado = dado.partition(",")
    try:
        return base64.b64decode(dado, validate=False)
    except (ValueError, TypeError):
        return None


class WuzapiClient:
    """Cliente REST do wuzapi (Go/whatsmeow) rodando na rede interna.

    Autenticação por header `Token: <token do usuário do wuzapi>` (o
    `Authorization` de admin nunca sai daqui — criar/derrubar sessão é
    operação manual, não de runtime)."""

    def _credenciais(self) -> tuple[str, str]:
        settings = get_settings()
        if not settings.wuzapi_base_url or not settings.wuzapi_token:
            raise WhatsAppNaoConfigurado("WUZAPI_BASE_URL e/ou WUZAPI_TOKEN não configurados.")
        return settings.wuzapi_base_url.rstrip("/"), settings.wuzapi_token

    async def _postar(
        self, caminho: str, corpo: dict[str, Any], *, timeout: float | None = None
    ) -> dict:
        base, token = self._credenciais()
        client = await _cliente_http()
        kwargs: dict[str, Any] = {}
        if timeout is not None:
            kwargs["timeout"] = timeout
        resp = await client.post(
            f"{base}{caminho}", json=corpo, headers={"Token": token}, **kwargs
        )
        if resp.status_code >= 400:
            log.warning("WhatsApp: wuzapi recusou %s (status=%s)", caminho, resp.status_code)
            raise WhatsAppEnvioFalhou(resp.status_code, resp.text)
        try:
            data = resp.json()
        except ValueError:
            raise WhatsAppEnvioFalhou(resp.status_code, "resposta não-JSON do wuzapi") from None
        # 200 com `success: false` acontece (ex.: número não está no WhatsApp).
        if isinstance(data, dict) and data.get("success") is False:
            raise WhatsAppEnvioFalhou(resp.status_code, str(data.get("error") or data))
        return data

    async def enviar_texto(self, destinatario: str, corpo: str) -> dict:
        data = await self._postar("/chat/send/text", {"Phone": destinatario, "Body": corpo})
        log.info("WhatsApp: mensagem enviada (id=%s)", (data.get("data") or {}).get("Id"))
        return data

    async def enviar_documento(
        self,
        destinatario: str,
        link: str,
        *,
        nome_arquivo: str | None = None,
        legenda: str | None = None,
    ) -> dict:
        conteudo, mime = await self._resolver_arquivo(link)
        nome = nome_arquivo or link.rsplit("/", 1)[-1] or "documento"
        payload = {
            "Phone": destinatario,
            "FileName": nome,
            "Document": f"data:{mime};base64,{base64.b64encode(conteudo).decode('ascii')}",
        }
        if legenda:
            payload["Caption"] = legenda
        data = await self._postar("/chat/send/document", payload, timeout=30.0)
        log.info("WhatsApp: documento enviado (id=%s)", (data.get("data") or {}).get("Id"))
        return data

    async def _resolver_arquivo(self, link: str) -> tuple[bytes, str]:
        """Bytes + MIME do documento a enviar.

        O wuzapi não aceita URL (a Meta aceitava): ou o arquivo é um estático
        da própria app — caso de todos os formulários em branco, então lê do
        disco e evita uma volta pela internet pública — ou é baixado por HTTP
        com o mesmo teto de tamanho da mídia de entrada."""
        settings = get_settings()
        caminho_url = httpx.URL(link).path
        if caminho_url.startswith("/static/"):
            local = (_STATIC_ROOT / caminho_url[len("/static/"):]).resolve()
            # Barra path traversal: só serve o que está sob app/static/.
            if local.is_file() and local.is_relative_to(_STATIC_ROOT.resolve()):
                mime = mimetypes.guess_type(local.name)[0] or "application/octet-stream"
                return local.read_bytes(), mime

        client = await _cliente_http()
        resp = await client.get(link, timeout=30.0)
        if resp.status_code >= 400:
            raise WhatsAppEnvioFalhou(resp.status_code, f"não foi possível baixar {caminho_url}")
        if len(resp.content) > settings.whatsapp_intake_midia_max_bytes:
            raise WhatsAppEnvioFalhou(413, "documento acima do teto configurado")
        mime = resp.headers.get("content-type", "").split(";")[0].strip() or "application/octet-stream"
        return resp.content, mime

    async def baixar_midia(self, referencia: str) -> tuple[bytes, str] | None:
        """Decripta a mídia recebida via `POST /chat/downloadX`.

        `referencia` é o token de `token_midia()`. Nunca lança — mídia é
        contexto opcional do chamado (mesma tolerância do provedor Meta)."""
        settings = get_settings()
        descritor = _descritor_do_token(referencia)
        if descritor is None:
            log.warning("WhatsApp: referência de mídia em formato inesperado — download ignorado.")
            return None

        mime = str(descritor.get("Mimetype") or "application/octet-stream")
        declarado = int(descritor.get("FileLength") or 0)
        if declarado and declarado > settings.whatsapp_intake_midia_max_bytes:
            log.info("WhatsApp: mídia ignorada (tamanho declarado acima do teto)")
            return None

        if mime.startswith("image/"):
            caminho = "/chat/downloadimage"
        elif mime.startswith("audio/"):
            caminho = "/chat/downloadaudio"
        elif mime.startswith("video/"):
            caminho = "/chat/downloadvideo"
        else:
            caminho = "/chat/downloaddocument"

        try:
            data = await self._postar(caminho, descritor, timeout=60.0)
        except (WhatsAppNaoConfigurado, WhatsAppEnvioFalhou, httpx.HTTPError) as exc:
            log.warning("WhatsApp: falha ao baixar mídia (%s)", type(exc).__name__)
            return None

        # O envelope do wuzapi é {"code":200,"success":true,"data":{...}}; o
        # campo com o binário varia de versão — aceita os nomes conhecidos.
        corpo = data.get("data") if isinstance(data.get("data"), dict) else data
        conteudo = None
        for chave in ("Data", "data", "Base64", "base64", "Content"):
            conteudo = _base64_do_payload((corpo or {}).get(chave))
            if conteudo:
                break
        if not conteudo:
            log.warning("WhatsApp: resposta de download sem binário reconhecível.")
            return None
        if len(conteudo) > settings.whatsapp_intake_midia_max_bytes:
            log.info("WhatsApp: mídia ignorada (excedeu o teto após o download)")
            return None
        return conteudo, str((corpo or {}).get("Mimetype") or mime)

    async def presenca(self, destinatario: str, estado: str, *, media: str = "") -> None:
        """`composing` / `paused` (media="" para texto, "audio" para gravação).

        Nunca lança: presença é cosmética, falha nela não pode derrubar a
        resposta que o usuário está esperando."""
        try:
            await self._postar(
                "/chat/presence",
                {"Phone": destinatario, "State": estado, "Media": media},
                timeout=5.0,
            )
        except (WhatsAppNaoConfigurado, WhatsAppEnvioFalhou, httpx.HTTPError) as exc:
            log.debug("WhatsApp: presença '%s' não aplicada (%s)", estado, type(exc).__name__)

    async def marcar_lida(self, destinatario: str, ids: list[str]) -> None:
        """Tique azul nas mensagens do usuário — um humano lê antes de
        responder; o bot que nunca marca lida destoa do padrão da conta."""
        if not ids:
            return
        try:
            await self._postar(
                "/chat/markread",
                {"Id": ids, "ChatPhone": destinatario, "SenderPhone": destinatario},
                timeout=5.0,
            )
        except (WhatsAppNaoConfigurado, WhatsAppEnvioFalhou, httpx.HTTPError) as exc:
            log.debug("WhatsApp: markread ignorado (%s)", type(exc).__name__)


# ---------------------------------------------------------------------------
# Seleção do provedor + API pública (assinaturas preservadas)
# ---------------------------------------------------------------------------


def get_client() -> WhatsAppClient:
    """Cliente do provedor ativo. Sem cache: `get_settings()` já é cacheado e
    os clientes não guardam estado (o pool HTTP é global)."""
    provedor = (get_settings().whatsapp_provider or "meta").strip().lower()
    if provedor == "wuzapi":
        return WuzapiClient()
    if provedor == "meta":
        return MetaClient()
    raise WhatsAppNaoConfigurado(
        f"WHATSAPP_PROVIDER inválido: {provedor!r} (use 'meta' ou 'wuzapi')."
    )


async def enviar_mensagem_texto(destinatario: str, corpo: str) -> dict:
    """Envia uma mensagem de texto livre (`destinatario` em E.164 sem "+")."""
    return await get_client().enviar_texto(destinatario, corpo)


async def enviar_documento(
    destinatario: str, link: str, *, nome_arquivo: str | None = None, legenda: str | None = None
) -> dict:
    """Envia um documento — o modelo em branco de um formulário obrigatório."""
    return await get_client().enviar_documento(
        destinatario, link, nome_arquivo=nome_arquivo, legenda=legenda
    )


async def baixar_midia(media_id: str) -> tuple[bytes, str] | None:
    """Baixa uma mídia recebida — ``(conteudo, mime)`` ou ``None``."""
    return await get_client().baixar_midia(media_id)


# ---------------------------------------------------------------------------
# Humanização da resposta (só tem efeito no provedor wuzapi)
# ---------------------------------------------------------------------------

# O indicador "digitando…" do WhatsApp expira sozinho em ~10s; enquanto o LLM
# processa (p95 de ~8s hoje), o estado precisa ser renovado.
_INTERVALO_KEEPALIVE_S = 8.0


@asynccontextmanager
async def digitando(destinatario: str, *, media: str = ""):
    """Mantém "digitando…" (ou "gravando áudio…") enquanto o bloco roda.

    Uso:

        async with digitando(telefone):
            resposta = await chamar_llm(...)
        await enviar_mensagem_texto(telefone, resposta)

    Sai sempre em `paused`, inclusive se o bloco levantar — deixar o contato
    "digitando" para sempre é justamente o artefato de bot que se quer evitar.
    """
    cliente = get_client()
    await cliente.presenca(destinatario, "composing", media=media)

    async def _renovar() -> None:
        while True:
            await asyncio.sleep(_INTERVALO_KEEPALIVE_S)
            await cliente.presenca(destinatario, "composing", media=media)

    tarefa = asyncio.create_task(_renovar())
    try:
        yield
    finally:
        tarefa.cancel()
        try:
            await tarefa
        except asyncio.CancelledError:
            pass
        await cliente.presenca(destinatario, "paused", media=media)


async def responder_humanizado(destinatario: str, texto: str) -> dict:
    """Envia com pausa de digitação proporcional ao tamanho do texto.

    Duas mensagens seguidas saindo com 30ms de diferença é assinatura de
    automação. O atraso vai de `WHATSAPP_DIGITACAO_MIN_S` a
    `WHATSAPP_DIGITACAO_MAX_S`, escalando com o comprimento (uma resposta de
    duas linhas "digita" mais rápido que um roteiro de cinco perguntas)."""
    settings = get_settings()
    minimo = settings.whatsapp_digitacao_min_s
    maximo = max(minimo, settings.whatsapp_digitacao_max_s)
    # ~25 caracteres por segundo é digitação humana rápida; o teto evita que
    # uma mensagem longa deixe a pessoa esperando.
    estimado = min(maximo, minimo + len(texto) / 25.0)
    atraso = random.uniform(minimo, estimado) if estimado > minimo else minimo

    cliente = get_client()
    await cliente.presenca(destinatario, "composing")
    try:
        await asyncio.sleep(atraso)
        return await cliente.enviar_texto(destinatario, texto)
    finally:
        await cliente.presenca(destinatario, "paused")
