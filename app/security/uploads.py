"""Validação server-side de upload de anexos (Seção 3.9 do plano mestre).

Regras (obrigatórias, decididas no servidor — nunca confiar no cliente):

- **Limite de 10MB** por arquivo (checado no stream, sem carregar ilimitado).
- **Allow-list de tipos:** ``pdf, jpg, png, mp4, docx, xlsx``.
- **MIME real por *magic bytes*** (``python-magic``), não pelo ``Content-Type``
  nem pela extensão enviados pelo cliente.
- **Sanitização do nome:** o objeto persistido usa um **UUID + extensão
  validada**; o nome original só é guardado como metadado (exibição).

A lógica é pura/injetável para permitir unit tests sem I/O de rede.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass

# Extensão canônica -> conjunto de MIMEs aceitos por *magic bytes* para ela.
# docx/xlsx são contêineres ZIP (OOXML): libmagic moderno devolve o tipo OOXML
# específico; versões antigas devolvem application/zip — ambos tolerados **apenas**
# quando a extensão é docx/xlsx (defesa: a extensão sozinha nunca autoriza).
_EXT_MIMES: dict[str, set[str]] = {
    "pdf":  {"application/pdf"},
    "jpg":  {"image/jpeg"},
    "jpeg": {"image/jpeg"},
    "png":  {"image/png"},
    "mp4":  {"video/mp4", "application/mp4"},
    "docx": {
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/zip",
    },
    "xlsx": {
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/zip",
    },
}

# Extensão normalizada quando há sinônimo (jpeg -> jpg no nome final).
_EXT_CANON = {"jpeg": "jpg"}

MAX_BYTES_DEFAULT = 10 * 1024 * 1024

# Assinaturas mínimas para fallback quando libmagic não estiver disponível.
_SIGNATURES: list[tuple[bytes, str]] = [
    (b"%PDF-", "application/pdf"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"PK\x03\x04", "application/zip"),  # docx/xlsx (OOXML) e zip
]


class UploadInvalido(ValueError):
    """Rejeição de upload por regra de validação (tamanho/tipo/nome)."""


@dataclass(frozen=True)
class AnexoValidado:
    nome_original: str   # sanitizado para exibição
    nome_objeto: str     # UUID + extensão canônica (usado no path do Storage)
    ext: str
    mime: str
    tamanho: int
    conteudo: bytes


def _extensao(nome: str) -> str:
    _, _, ext = nome.rpartition(".")
    return ext.lower().strip() if ext and ext != nome else ""


def sanitizar_nome_exibicao(nome: str) -> str:
    """Remove path traversal e caracteres perigosos, preservando algo legível."""
    base = nome.replace("\\", "/").split("/")[-1]        # tira diretórios
    base = base.strip().replace("\x00", "")
    base = re.sub(r"[^A-Za-z0-9._ -]", "_", base)         # allow-list de chars
    base = re.sub(r"\.{2,}", ".", base).strip(". ")       # evita ".."/pontas
    return base[:120] or "arquivo"


def sniff_signature(head: bytes) -> str:
    """Sniffer mínimo por assinatura para os tipos da allow-list (fallback sem
    libmagic). Mantém os testes herméticos e cobre o caso de ausência do libmagic."""
    for assinatura, mime in _SIGNATURES:
        if head.startswith(assinatura):
            return mime
    return "application/octet-stream"


def detectar_mime(head: bytes, *, magic_impl=None) -> str:
    """Detecta o MIME real por *magic bytes*.

    Usa ``python-magic`` quando disponível; caso contrário, :func:`sniff_signature`.
    ``magic_impl`` permite injetar um detector nos testes.
    """
    if magic_impl is not None:
        return magic_impl(head)
    try:
        import magic  # type: ignore

        return magic.from_buffer(head, mime=True)
    except Exception:  # noqa: BLE001 — sem libmagic: cai no sniffer local
        return sniff_signature(head)


def validar_anexo(
    nome: str,
    conteudo: bytes,
    *,
    max_bytes: int = MAX_BYTES_DEFAULT,
    magic_impl=None,
) -> AnexoValidado:
    """Valida um arquivo já lido em memória e devolve os metadados persistíveis.

    Levanta :class:`UploadInvalido` em qualquer violação.
    """
    tamanho = len(conteudo)
    if tamanho == 0:
        raise UploadInvalido("Arquivo vazio.")
    if tamanho > max_bytes:
        limite_mb = max_bytes // (1024 * 1024)
        raise UploadInvalido(f"Arquivo excede o limite de {limite_mb}MB.")

    ext = _extensao(nome)
    if ext not in _EXT_MIMES:
        permitidos = "pdf, jpg, png, mp4, docx, xlsx"
        raise UploadInvalido(f"Tipo de arquivo não permitido. Aceitos: {permitidos}.")

    mime = detectar_mime(conteudo[:2048], magic_impl=magic_impl)
    if mime not in _EXT_MIMES[ext]:
        raise UploadInvalido(
            "Conteúdo do arquivo não confere com a extensão informada."
        )

    ext_canon = _EXT_CANON.get(ext, ext)
    nome_objeto = f"{uuid.uuid4().hex}.{ext_canon}"
    return AnexoValidado(
        nome_original=sanitizar_nome_exibicao(nome),
        nome_objeto=nome_objeto,
        ext=ext_canon,
        mime=mime,
        tamanho=tamanho,
        conteudo=conteudo,
    )
