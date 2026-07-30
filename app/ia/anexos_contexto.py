"""Leitura de anexos (imagem/PDF) para o contexto da triagem (F7).

Duas camadas: funções **puras** de processamento de bytes (testáveis sem
I/O — lição do incidente de 2026-07-29, quando um teste com conexão
totalmente mockada travou um valor que nunca passou pelo encoder real do
`asyncpg`) e um **orquestrador** assíncrono que baixa do Storage e nunca
lança (Regra de Ouro #5 — falha de anexo jamais derruba a triagem).

Escopo desta entrega: imagens (``image/jpeg``, ``image/png``) via visão e
PDF (``application/pdf``) via extração de texto. Vídeo, docx/xlsx/pptx e OCR
de PDF escaneado ficam FORA de escopo — o dispatch por mime abaixo é a
fronteira estrutural, não só uma convenção de código.
"""

from __future__ import annotations

import asyncio
import base64
import io
import logging
from dataclasses import dataclass, field
from typing import Any

from PIL import Image
from pypdf import PdfReader

from app.config import Settings
from app.storage import AnexosStorage

log = logging.getLogger("app.ia.anexos_contexto")

_MIME_IMAGEM = {"image/jpeg", "image/png"}
_MIME_PDF = {"application/pdf"}


def redimensionar_imagem(conteudo: bytes, *, max_dimensao: int, qualidade: int) -> str | None:
    """Decodifica, reduz ao maior lado ``max_dimensao`` (nunca amplia) e
    recomprime como JPEG — devolve uma *data URI* base64 pronta para o bloco
    ``image_url`` do modelo. ``None`` em bytes corrompidos/formato não
    suportado (nunca lança — quem chama decide a política de falha)."""
    try:
        imagem = Image.open(io.BytesIO(conteudo))
        imagem.load()  # força a decodificação agora (erros tardios do Pillow)
        imagem = imagem.convert("RGB")
        imagem.thumbnail((max_dimensao, max_dimensao), Image.LANCZOS)
        saida = io.BytesIO()
        imagem.save(saida, format="JPEG", quality=qualidade)
    except Exception:  # noqa: BLE001 — qualquer falha de decode/encode = ignora o anexo
        log.warning("Falha ao redimensionar imagem de anexo", exc_info=True)
        return None
    base64_str = base64.b64encode(saida.getvalue()).decode("ascii")
    return f"data:image/jpeg;base64,{base64_str}"


def extrair_texto_pdf(conteudo: bytes, *, max_paginas: int, max_chars: int) -> str:
    """Extrai texto das primeiras ``max_paginas`` páginas, truncado em
    ``max_chars``. ``""`` quando não há texto extraível (PDF escaneado/só
    imagem — sem OCR nesta entrega) ou o arquivo é inválido; nunca lança."""
    try:
        leitor = PdfReader(io.BytesIO(conteudo))
        partes = [
            (pagina.extract_text() or "").strip()
            for pagina in leitor.pages[:max_paginas]
        ]
    except Exception:  # noqa: BLE001 — PDF corrompido/criptografado = sem texto
        log.warning("Falha ao extrair texto de PDF anexado", exc_info=True)
        return ""
    texto = "\n\n".join(p for p in partes if p)
    if not texto:
        return ""
    if len(texto) > max_chars:
        texto = texto[:max_chars].rstrip() + " […truncado]"
    return texto


@dataclass(frozen=True)
class ResultadoAnexos:
    """Saída pronta para entrar em ambos os passes (Regra de Ouro #4: são
    arquivos que o próprio cliente subiu sobre o próprio problema — não são
    a base sigilosa da Bondmann — então reaproveitar entre Passe A e Passe B
    não vaza nada que o cliente já não tenha)."""

    blocos_imagem: list[dict[str, Any]] = field(default_factory=list)
    texto_pdfs: str = ""
    processados: int = 0
    ignorados: int = 0


async def montar_contexto_anexos(
    storage: AnexosStorage,
    token: str,
    anexos: list[dict[str, Any]],
    settings: Settings,
    *,
    eh_quimico: bool,
) -> ResultadoAnexos:
    """Baixa e processa até os tetos configurados; cada item tem seu próprio
    isolamento de falha — download/decode ruim de um anexo nunca derruba os
    demais nem a triagem (Regra de Ouro #5)."""
    if not settings.ia_triagem_anexos_ativo or not anexos:
        return ResultadoAnexos()

    max_bytes = settings.ia_triagem_anexos_max_bytes_para(eh_quimico=eh_quimico)
    candidatos_imagem: list[dict[str, Any]] = []
    candidatos_pdf: list[dict[str, Any]] = []
    ignorados = 0
    for anexo in anexos:
        mime = anexo.get("mime")
        tamanho = anexo.get("tamanho") or 0
        if mime in _MIME_IMAGEM and len(candidatos_imagem) < settings.ia_triagem_anexos_max_arquivos_imagem:
            alvo = candidatos_imagem
        elif mime in _MIME_PDF and len(candidatos_pdf) < settings.ia_triagem_anexos_max_arquivos_pdf:
            alvo = candidatos_pdf
        else:
            ignorados += 1
            continue
        if tamanho > max_bytes:
            ignorados += 1
            continue
        alvo.append(anexo)

    candidatos = candidatos_imagem + candidatos_pdf
    if not candidatos:
        return ResultadoAnexos(ignorados=ignorados)

    conteudos = await asyncio.gather(
        *(storage.download(token, a["path"]) for a in candidatos),
        return_exceptions=True,
    )

    detail = settings.ia_triagem_anexos_detail_normalizado
    blocos_imagem: list[dict[str, Any]] = []
    partes_pdf: list[str] = []
    processados = 0
    for anexo, conteudo in zip(candidatos, conteudos):
        if isinstance(conteudo, BaseException) or conteudo is None:
            if isinstance(conteudo, BaseException):
                log.warning("Falha ao baixar anexo %s: %s", anexo.get("path"), conteudo)
            ignorados += 1
            continue
        if anexo.get("mime") in _MIME_IMAGEM:
            data_uri = redimensionar_imagem(
                conteudo,
                max_dimensao=settings.ia_triagem_anexos_max_dimensao_px,
                qualidade=settings.ia_triagem_anexos_qualidade_jpeg,
            )
            if data_uri is None:
                ignorados += 1
                continue
            blocos_imagem.append(
                {"type": "image_url", "image_url": {"url": data_uri, "detail": detail}}
            )
            processados += 1
        else:
            texto = extrair_texto_pdf(
                conteudo,
                max_paginas=settings.ia_triagem_anexos_pdf_max_paginas,
                max_chars=settings.ia_triagem_anexos_pdf_max_chars,
            )
            if not texto:
                ignorados += 1
                continue
            nome = anexo.get("nome") or "anexo.pdf"
            partes_pdf.append(f"### {nome}\n{texto}")
            processados += 1

    return ResultadoAnexos(
        blocos_imagem=blocos_imagem,
        texto_pdfs="\n\n".join(partes_pdf),
        processados=processados,
        ignorados=ignorados,
    )
