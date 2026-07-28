"""Testes da validação server-side de anexos (Seção 3.9).

Puros/hermético: o detector de *magic bytes* é injetado, sem depender de libmagic.
"""

from __future__ import annotations

import pytest

from app.security.uploads import (
    UploadInvalido,
    sanitizar_nome_exibicao,
    sniff_signature,
    validar_anexo,
)

PDF = b"%PDF-1.4\n%fake pdf bytes"
PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 32
JPG = b"\xff\xd8\xff\xe0" + b"0" * 32
ZIP = b"PK\x03\x04" + b"0" * 32
MP4 = b"\x00\x00\x00\x18ftypmp42" + b"0" * 32


def _magic(mime: str):
    return lambda head: mime


def test_pdf_valido():
    a = validar_anexo("relatorio.pdf", PDF, magic_impl=_magic("application/pdf"))
    assert a.ext == "pdf"
    assert a.mime == "application/pdf"
    assert a.nome_objeto.endswith(".pdf")
    assert a.nome_original == "relatorio.pdf"


def test_jpeg_normaliza_extensao_para_jpg():
    a = validar_anexo("foto.jpeg", JPG, magic_impl=_magic("image/jpeg"))
    assert a.ext == "jpg"
    assert a.nome_objeto.endswith(".jpg")


def test_docx_aceita_zip_ooxml():
    # docx é contêiner ZIP: libmagic antigo devolve application/zip — tolerado só p/ docx/xlsx/pptx.
    a = validar_anexo("contrato.docx", ZIP, magic_impl=_magic("application/zip"))
    assert a.ext == "docx"


def test_pptx_valido():
    a = validar_anexo(
        "apresentacao.pptx",
        ZIP,
        magic_impl=_magic(
            "application/vnd.openxmlformats-officedocument.presentationml.presentation"
        ),
    )
    assert a.ext == "pptx"
    assert a.nome_objeto.endswith(".pptx")


def test_pptx_aceita_zip_ooxml():
    # pptx é contêiner ZIP: libmagic antigo devolve application/zip — mesma tolerância do docx/xlsx.
    a = validar_anexo("apresentacao.pptx", ZIP, magic_impl=_magic("application/zip"))
    assert a.ext == "pptx"


def test_extensao_fora_da_allowlist_rejeitada():
    with pytest.raises(UploadInvalido):
        validar_anexo("script.exe", PDF, magic_impl=_magic("application/pdf"))


def test_mime_incoerente_com_extensao_rejeitado():
    # extensão .pdf mas magic bytes de PNG -> rejeita (não confia na extensão).
    with pytest.raises(UploadInvalido):
        validar_anexo("falso.pdf", PNG, magic_impl=_magic("image/png"))


def test_arquivo_vazio_rejeitado():
    with pytest.raises(UploadInvalido):
        validar_anexo("x.pdf", b"", magic_impl=_magic("application/pdf"))


def test_tamanho_acima_do_limite_rejeitado():
    with pytest.raises(UploadInvalido):
        validar_anexo("g.pdf", PDF, max_bytes=4, magic_impl=_magic("application/pdf"))


@pytest.mark.parametrize(
    "entrada,esperado_contem",
    [
        ("../../etc/passwd", "passwd"),
        ("re la\\tório..pdf", "pdf"),
        ("", "arquivo"),
    ],
)
def test_sanitizacao_de_nome(entrada, esperado_contem):
    saida = sanitizar_nome_exibicao(entrada)
    assert "/" not in saida and "\\" not in saida
    assert ".." not in saida
    assert esperado_contem in saida


def test_sniff_signature_fallback():
    # Sniffer local por assinatura (usado quando libmagic não está disponível).
    assert sniff_signature(PDF) == "application/pdf"
    assert sniff_signature(PNG) == "image/png"
    assert sniff_signature(JPG) == "image/jpeg"
    assert sniff_signature(ZIP) == "application/zip"
    assert sniff_signature(MP4) == "video/mp4"
    assert sniff_signature(b"xyz") == "application/octet-stream"


def test_mp4_valido_via_sniffer():
    a = validar_anexo("video.mp4", MP4, magic_impl=sniff_signature)
    assert a.ext == "mp4"
    assert a.mime == "video/mp4"
