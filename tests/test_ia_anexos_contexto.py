"""Testes de `app/ia/anexos_contexto.py` (F7 — leitura de anexos pela IA).

Funções puras (`redimensionar_imagem`/`extrair_texto_pdf`) são testadas com
bytes reais via Pillow/pypdf — não só mocks. Essa escolha é deliberada: o
incidente de 2026-07-29 (`_MARGEM_ORFAO` como string em vez de `timedelta`)
só existiu porque o teste correspondente usava uma conexão 100% mockada e
nunca passou pelo encoder real do `asyncpg`. Aqui, o equivalente seria um
teste que nunca chama o Pillow/pypdf de verdade.
"""

from __future__ import annotations

import base64
import io

from PIL import Image

from app.config import Settings
from app.ia.anexos_contexto import (
    ResultadoAnexos,
    extrair_texto_pdf,
    montar_contexto_anexos,
    redimensionar_imagem,
)


def _imagem_png(largura: int, altura: int) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (largura, altura), color=(120, 40, 200)).save(buf, format="PNG")
    return buf.getvalue()


def _pdf_com_texto(texto: str) -> bytes:
    """PDF mínimo válido com uma página de texto real (sem dependências
    extras) — permite testar `extrair_texto_pdf` com bytes reais, iguais aos
    que o `pypdf` vê em produção."""
    conteudo = f"BT /F1 18 Tf 72 720 Td ({texto}) Tj ET".encode("latin-1")
    objetos = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /Resources << /Font << /F1 4 0 R >> >> "
        b"/MediaBox [0 0 612 792] /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(conteudo)).encode() + b" >>\nstream\n" + conteudo + b"\nendstream",
    ]
    partes = [b"%PDF-1.4\n"]
    offsets = [0]
    for i, obj in enumerate(objetos, start=1):
        offsets.append(sum(len(p) for p in partes))
        partes.append(f"{i} 0 obj\n".encode() + obj + b"\nendobj\n")
    xref_offset = sum(len(p) for p in partes)
    xref = [b"xref\n0 " + str(len(objetos) + 1).encode() + b"\n0000000000 65535 f \n"]
    for off in offsets[1:]:
        xref.append(f"{off:010} 00000 n \n".encode())
    trailer = (
        b"trailer\n<< /Size " + str(len(objetos) + 1).encode() + b" /Root 1 0 R >>\n"
        b"startxref\n" + str(xref_offset).encode() + b"\n%%EOF"
    )
    return b"".join(partes) + b"".join(xref) + trailer


def _pdf_sem_texto() -> bytes:
    """PDF válido, mas com página vazia (sem stream de conteúdo) — simula um
    PDF escaneado/só-imagem, que `extrair_texto_pdf` deve ignorar sem OCR."""
    objetos = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] >>",
    ]
    partes = [b"%PDF-1.4\n"]
    offsets = [0]
    for i, obj in enumerate(objetos, start=1):
        offsets.append(sum(len(p) for p in partes))
        partes.append(f"{i} 0 obj\n".encode() + obj + b"\nendobj\n")
    xref_offset = sum(len(p) for p in partes)
    xref = [b"xref\n0 " + str(len(objetos) + 1).encode() + b"\n0000000000 65535 f \n"]
    for off in offsets[1:]:
        xref.append(f"{off:010} 00000 n \n".encode())
    trailer = (
        b"trailer\n<< /Size " + str(len(objetos) + 1).encode() + b" /Root 1 0 R >>\n"
        b"startxref\n" + str(xref_offset).encode() + b"\n%%EOF"
    )
    return b"".join(partes) + b"".join(xref) + trailer


def _settings(**overrides) -> Settings:
    base = dict(
        session_secret="segredo-real-de-teste-nao-default",
        csrf_secret="outro-segredo-real-de-teste-nao-default",
        ia_triagem_anexos_ativo=True,
    )
    base.update(overrides)
    return Settings(**base)


# --- redimensionar_imagem -----------------------------------------------


def test_redimensionar_imagem_produz_data_uri_jpeg_valida():
    uri = redimensionar_imagem(_imagem_png(2000, 1500), max_dimensao=1024, qualidade=78)
    assert uri.startswith("data:image/jpeg;base64,")
    decodificada = Image.open(io.BytesIO(base64.b64decode(uri.split(",", 1)[1])))
    assert decodificada.format == "JPEG"
    assert max(decodificada.size) <= 1024


def test_redimensionar_imagem_nao_amplia_imagem_pequena():
    uri = redimensionar_imagem(_imagem_png(200, 100), max_dimensao=1024, qualidade=78)
    decodificada = Image.open(io.BytesIO(base64.b64decode(uri.split(",", 1)[1])))
    assert decodificada.size == (200, 100)


def test_redimensionar_imagem_bytes_corrompidos_devolve_none():
    assert redimensionar_imagem(b"isto nao e uma imagem", max_dimensao=1024, qualidade=78) is None


# --- extrair_texto_pdf -----------------------------------------------


def test_extrair_texto_pdf_com_texto_real():
    texto = extrair_texto_pdf(
        _pdf_com_texto("BONDMANN TESTE EXTRACAO"), max_paginas=5, max_chars=6000
    )
    assert "BONDMANN TESTE EXTRACAO" in texto


def test_extrair_texto_pdf_trunca_no_max_chars():
    texto_longo = "A" * 100
    texto = extrair_texto_pdf(_pdf_com_texto(texto_longo), max_paginas=5, max_chars=20)
    assert len(texto) <= len("…truncado] ") + 20 + 5
    assert texto.endswith("[…truncado]")


def test_extrair_texto_pdf_sem_texto_extraivel_devolve_string_vazia():
    assert extrair_texto_pdf(_pdf_sem_texto(), max_paginas=5, max_chars=6000) == ""


def test_extrair_texto_pdf_bytes_corrompidos_devolve_string_vazia_sem_lancar():
    assert extrair_texto_pdf(b"isto nao e um pdf", max_paginas=5, max_chars=6000) == ""


# --- montar_contexto_anexos -----------------------------------------------


class _FakeStorage:
    def __init__(self, respostas: dict[str, bytes | None]):
        self._respostas = respostas
        self.chamadas: list[str] = []

    async def download(self, token, path):  # noqa: ARG002 — token não importa aqui
        self.chamadas.append(path)
        return self._respostas.get(path)


def _anexo(path: str, mime: str, tamanho: int = 1000, nome: str | None = None) -> dict:
    return {"path": path, "nome": nome or path, "mime": mime, "tamanho": tamanho}


async def test_kill_switch_off_nao_baixa_nada():
    storage = _FakeStorage({"a.jpg": _imagem_png(100, 100)})
    resultado = await montar_contexto_anexos(
        storage, "token", [_anexo("a.jpg", "image/jpeg")], _settings(ia_triagem_anexos_ativo=False),
        eh_quimico=False,
    )
    assert resultado == ResultadoAnexos()
    assert storage.chamadas == []


async def test_lista_de_anexos_vazia_nao_baixa_nada():
    storage = _FakeStorage({})
    resultado = await montar_contexto_anexos(storage, "token", [], _settings(), eh_quimico=False)
    assert resultado == ResultadoAnexos()
    assert storage.chamadas == []


async def test_mime_fora_do_allow_list_e_ignorado_sem_rede():
    storage = _FakeStorage({})
    resultado = await montar_contexto_anexos(
        storage, "token",
        [_anexo("v.mp4", "video/mp4"), _anexo("d.docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document")],
        _settings(), eh_quimico=False,
    )
    assert storage.chamadas == []
    assert resultado.processados == 0
    assert resultado.ignorados == 2


async def test_tamanho_acima_do_teto_e_ignorado_sem_baixar():
    settings = _settings(ia_triagem_anexos_max_bytes=1000)
    storage = _FakeStorage({"a.jpg": _imagem_png(100, 100)})
    resultado = await montar_contexto_anexos(
        storage, "token", [_anexo("a.jpg", "image/jpeg", tamanho=2000)], settings, eh_quimico=False,
    )
    assert storage.chamadas == []
    assert resultado.ignorados == 1


async def test_teto_maior_do_quimico_permite_arquivo_que_o_geral_rejeitaria():
    settings = _settings(ia_triagem_anexos_max_bytes=1000, ia_triagem_anexos_max_bytes_quimico=5000)
    conteudo = _imagem_png(100, 100)
    storage = _FakeStorage({"a.jpg": conteudo})
    resultado = await montar_contexto_anexos(
        storage, "token", [_anexo("a.jpg", "image/jpeg", tamanho=3000)], settings, eh_quimico=True,
    )
    assert storage.chamadas == ["a.jpg"]
    assert resultado.processados == 1


async def test_teto_por_tipo_limita_quantidade_de_downloads():
    settings = _settings(ia_triagem_anexos_max_arquivos_imagem=2)
    conteudo = _imagem_png(100, 100)
    anexos = [_anexo(f"img{i}.jpg", "image/jpeg") for i in range(5)]
    storage = _FakeStorage({a["path"]: conteudo for a in anexos})
    resultado = await montar_contexto_anexos(storage, "token", anexos, settings, eh_quimico=False)
    assert len(storage.chamadas) == 2
    assert resultado.processados == 2
    assert resultado.ignorados == 3


async def test_download_none_e_ignorado_sem_lancar():
    storage = _FakeStorage({"a.jpg": None})
    resultado = await montar_contexto_anexos(
        storage, "token", [_anexo("a.jpg", "image/jpeg")], _settings(), eh_quimico=False,
    )
    assert resultado.processados == 0
    assert resultado.ignorados == 1


async def test_imagem_corrompida_e_ignorada_sem_lancar():
    storage = _FakeStorage({"a.jpg": b"lixo binario"})
    resultado = await montar_contexto_anexos(
        storage, "token", [_anexo("a.jpg", "image/jpeg")], _settings(), eh_quimico=False,
    )
    assert resultado.processados == 0
    assert resultado.ignorados == 1


async def test_pdf_sem_texto_e_ignorado_sem_lancar():
    storage = _FakeStorage({"a.pdf": _pdf_sem_texto()})
    resultado = await montar_contexto_anexos(
        storage, "token", [_anexo("a.pdf", "application/pdf")], _settings(), eh_quimico=False,
    )
    assert resultado.processados == 0
    assert resultado.ignorados == 1


async def test_caso_feliz_imagem_e_pdf_juntos():
    storage = _FakeStorage(
        {"foto.jpg": _imagem_png(1200, 900), "laudo.pdf": _pdf_com_texto("RESULTADO DO LAUDO")}
    )
    resultado = await montar_contexto_anexos(
        storage, "token",
        [_anexo("foto.jpg", "image/jpeg", nome="foto.jpg"), _anexo("laudo.pdf", "application/pdf", nome="laudo.pdf")],
        _settings(), eh_quimico=False,
    )
    assert resultado.processados == 2
    assert resultado.ignorados == 0
    assert len(resultado.blocos_imagem) == 1
    assert resultado.blocos_imagem[0]["type"] == "image_url"
    assert resultado.blocos_imagem[0]["image_url"]["detail"] == "low"
    assert "RESULTADO DO LAUDO" in resultado.texto_pdfs
    assert "laudo.pdf" in resultado.texto_pdfs


async def test_detail_configuravel_e_valor_invalido_degrada_para_low():
    storage = _FakeStorage({"a.jpg": _imagem_png(100, 100)})
    resultado = await montar_contexto_anexos(
        storage, "token", [_anexo("a.jpg", "image/jpeg")],
        _settings(ia_triagem_anexos_detail="high"), eh_quimico=False,
    )
    assert resultado.blocos_imagem[0]["image_url"]["detail"] == "high"

    storage2 = _FakeStorage({"a.jpg": _imagem_png(100, 100)})
    resultado2 = await montar_contexto_anexos(
        storage2, "token", [_anexo("a.jpg", "image/jpeg")],
        _settings(ia_triagem_anexos_detail="algo-invalido"), eh_quimico=False,
    )
    assert resultado2.blocos_imagem[0]["image_url"]["detail"] == "low"
