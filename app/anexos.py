"""Fluxo de anexos compartilhado entre Portal e Workspace (Seção 3.9 / C2).

Validação server-side (tamanho/allow-list/magic bytes), envio ao Storage privado
e geração *on-demand* de signed URLs (TTL 1h, nunca cacheadas). Portal (cliente)
e Workspace (staff) reutilizam estas funções para não duplicar a lógica de upload.
"""

from __future__ import annotations

import logging

from fastapi import Request, UploadFile

from app.auth.session import current_access_token
from app.security.uploads import UploadInvalido, validar_anexo
from app.storage import AnexosStorage, StorageError, ensure_storage

log = logging.getLogger("app.anexos")

# Limite de anexos por mensagem (bound de trabalho/carga). Vale para **todos** os
# tipos da allow-list (Seção 3.9) — o teto por arquivo continua sendo
# ``anexo_max_bytes`` (10MB). Exposto ao Jinja como global ``MAX_ANEXOS``
# (``app/templating.py``) para os textos de UI não repetirem o número.
MAX_ANEXOS = 20


def access_token(request: Request) -> str | None:
    """JWT do usuário — necessário para o Storage sob RLS (ver ``current_access_token``)."""
    return current_access_token(request)


async def assinar_anexos(request: Request, mensagens: list[dict]) -> None:
    """Gera signed URLs (TTL 1h) *on-demand* para cada anexo (C2 — nunca cacheado).

    Muta as mensagens in-place adicionando ``url`` a cada anexo. Falha graciosa:
    sem Storage/token, ``url`` fica ``None`` e o template mostra 'indisponível'.
    """
    storage = await ensure_storage()
    token = access_token(request)
    for m in mensagens:
        for anexo in m.get("anexos") or []:
            anexo["url"] = None
            path = anexo.get("path")
            if storage and token and path:
                anexo["url"] = await storage.signed_url(token, path)


async def validar_uploads(arquivos: list[UploadFile], *, max_bytes: int) -> list:
    """Lê e valida (contagem/tamanho/allow-list/magic bytes) os arquivos, **sem**
    tocar no Storage. Assim a validação pode barrar a operação antes de qualquer
    efeito colateral. Levanta :class:`UploadInvalido` em qualquer violação."""
    reais = [a for a in arquivos if a and a.filename]
    if not reais:
        return []
    if len(reais) > MAX_ANEXOS:
        raise UploadInvalido(f"Máximo de {MAX_ANEXOS} anexos.")
    validados = []
    for arquivo in reais:
        # Leitura limitada a max_bytes+1 para rejeitar excesso sem carregar ilimitado.
        conteudo = await arquivo.read(max_bytes + 1)
        validados.append(validar_anexo(arquivo.filename, conteudo, max_bytes=max_bytes))
    return validados


async def enviar_uploads(
    request: Request, validados: list, *, empresa_id: str, chamado_id: str
) -> list[dict]:
    """Envia os arquivos já validados ao Storage e devolve os metadados
    persistíveis ``{path, nome, mime, tamanho}``. Levanta :class:`UploadInvalido`."""
    if not validados:
        return []
    storage = await ensure_storage()
    token = access_token(request)
    if storage is None or token is None:
        raise UploadInvalido("Envio de anexos indisponível no momento.")
    anexos: list[dict] = []
    for validado in validados:
        path = AnexosStorage.path(empresa_id, chamado_id, validado.nome_objeto)
        try:
            await storage.upload(token, path, validado.conteudo, validado.mime)
        except StorageError as exc:
            raise UploadInvalido("Falha ao enviar o anexo. Tente novamente.") from exc
        anexos.append(
            {
                "path": path,
                "nome": validado.nome_original,
                "mime": validado.mime,
                "tamanho": validado.tamanho,
            }
        )
    return anexos


async def processar_uploads(
    request: Request,
    arquivos: list[UploadFile],
    *,
    empresa_id: str,
    chamado_id: str,
    max_bytes: int,
) -> list[dict]:
    """Valida e envia num passo (usado quando o chamado já existe — resposta)."""
    validados = await validar_uploads(arquivos, max_bytes=max_bytes)
    return await enviar_uploads(
        request, validados, empresa_id=empresa_id, chamado_id=chamado_id
    )
