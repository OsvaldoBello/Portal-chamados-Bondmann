"""Testes da validação server-side de anexos (Seção 3.9).

Puros/hermético: o detector de *magic bytes* é injetado, sem depender de libmagic.
"""

from __future__ import annotations

import os
import sys
import time

import pytest

import app.security.uploads as uploads_mod
from app.security.uploads import (
    UploadInvalido,
    _magic_disponivel_probe,
    extensao_provavel,
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


def test_extensao_provavel_mimes_inequivocos():
    """Usado quando não há nome de arquivo (documento do WhatsApp sem
    ``filename``) — resolve os MIMEs que só têm UMA extensão possível."""
    assert extensao_provavel("application/pdf") == "pdf"
    assert extensao_provavel("image/png") == "png"
    assert extensao_provavel("image/jpeg") == "jpg"  # nunca "jpeg" (sinônimo)
    assert extensao_provavel("video/mp4") == "mp4"


def test_extensao_provavel_mime_ambiguo_devolve_none():
    """``application/zip`` serve pra docx/xlsx/pptx — sem nome de arquivo não
    dá pra escolher entre eles às cegas; melhor recusar do que arriscar."""
    assert extensao_provavel("application/zip") is None


# ---------------------------------------------------------------------------
# Probe de disponibilidade do libmagic (auditoria 2026-08-27)
#
# A probe original rodava `import magic` numa threading.Thread com timeout —
# proteção real contra a thread TRAVAR, mas nenhuma contra ela CRASHAR o
# processo (um access violation nativo numa thread mata o processo inteiro;
# só isolamento por processo protege disso). Visto ao vivo: a suíte inteira
# derrubada por "Windows fatal exception: access violation" dentro do
# `import magic`. Estes testes fixam o contrato da versão corrigida
# (multiprocessing.Process) sem depender do libmagic real instalado — que é
# exatamente a peça não confiável que motivou a correção.
#
# As duas funções abaixo (não fechamentos locais) são o alvo do processo
# filho via monkeypatch: `multiprocessing` com o método `spawn` (default no
# Windows) precisa localizar o alvo por referência de módulo importável pra
# recriá-lo no processo filho — uma função definida dentro do corpo do teste
# não seria picklable.
def _alvo_probe_sucesso() -> None:
    sys.exit(0)


def _alvo_probe_trava() -> None:
    time.sleep(60)


def _alvo_probe_falha() -> None:
    sys.exit(1)


def _alvo_probe_morre_abruptamente() -> None:
    # Sem exceção capturável nem sys.exit "normal" — o jeito determinístico
    # de reproduzir "o processo filho saiu sem terminar normalmente" (mesmo
    # sintoma, do ponto de vista do pai, de um crash nativo por access
    # violation: exitcode diferente de 0, sem exceção Python nenhuma).
    os._exit(1)


@pytest.fixture(autouse=True)
def _reseta_cache_da_probe(monkeypatch: pytest.MonkeyPatch):
    """Toda função de teste abaixo começa com o cache global limpo — sem
    isso, o resultado de um teste vazaria pro próximo (a probe só roda de
    verdade na 1ª chamada por processo)."""
    monkeypatch.setattr(uploads_mod, "_magic_disponivel", None)
    yield


def test_magic_probe_sucesso_no_processo_filho(monkeypatch):
    monkeypatch.setattr(uploads_mod, "_tentar_importar_magic", _alvo_probe_sucesso)
    assert _magic_disponivel_probe() is True


def test_magic_probe_processo_travado_vira_indisponivel_no_timeout(monkeypatch):
    """O caso original que a probe existe pra cobrir: o processo filho nunca
    volta. Timeout encurtado só pra o teste não esperar os 2s de produção."""
    monkeypatch.setattr(uploads_mod, "_tentar_importar_magic", _alvo_probe_trava)
    monkeypatch.setattr(uploads_mod, "_MAGIC_PROBE_TIMEOUT", 0.3)
    inicio = time.monotonic()
    assert _magic_disponivel_probe() is False
    # Prova que voltou pelo timeout curto, não pelos 60s do alvo.
    assert time.monotonic() - inicio < 10.0


def test_magic_probe_processo_morre_com_exitcode_diferente_de_zero(monkeypatch):
    """Cobre o caso que a versão em thread NUNCA detectava: o processo filho
    morre sozinho, sem exceção Python nenhuma — o que um crash nativo por
    access violation também produz do ponto de vista do processo pai
    (``exitcode`` diferente de 0)."""
    monkeypatch.setattr(uploads_mod, "_tentar_importar_magic", _alvo_probe_morre_abruptamente)
    assert _magic_disponivel_probe() is False


def test_magic_probe_cacheia_o_resultado_por_processo(monkeypatch):
    """1ª chamada decide; chamadas seguintes não voltam a spawnar processo —
    provado trocando o alvo por um que devolveria outra resposta: se a probe
    não estivesse cacheando, o resultado mudaria na 2ª chamada."""
    monkeypatch.setattr(uploads_mod, "_tentar_importar_magic", _alvo_probe_sucesso)
    assert _magic_disponivel_probe() is True

    monkeypatch.setattr(uploads_mod, "_tentar_importar_magic", _alvo_probe_falha)
    assert _magic_disponivel_probe() is True  # continua True: veio do cache


def test_magic_probe_real_termina_dentro_do_timeout():
    """Smoke test end-to-end contra o `import magic` de verdade instalado no
    ambiente — não fixa se o resultado é True ou False (isso depende do SO e
    do libmagic disponível), só que a chamada SEMPRE retorna um bool dentro
    do orçamento de tempo, sem travar nem derrubar o processo do pytest. Se
    esta chamada crashar o runner, a proteção por processo deixou de
    funcionar."""
    inicio = time.monotonic()
    resultado = _magic_disponivel_probe()
    assert isinstance(resultado, bool)
    assert time.monotonic() - inicio < uploads_mod._MAGIC_PROBE_TIMEOUT + 5.0
    assert extensao_provavel("text/plain") is None
