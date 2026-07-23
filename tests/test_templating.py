"""Testes de `app/templating.py::static_url` (cache-busting, 2026-07-23).

Sem isso, o navegador de quem já visitou o site servia do cache HTTP a
versão ANTIGA de um `.js`/`.css` próprio mesmo depois do deploy trocar o
arquivo no servidor (bug real: lightbox de foto em `/admin/usuarios` não
abria porque o `shell.js` em cache não tinha a função ainda)."""

from __future__ import annotations

from pathlib import Path

from app.templating import static_url


def test_static_url_arquivo_existente_ganha_query_v():
    url = static_url("/static/js/shell.js")
    assert url.startswith("/static/js/shell.js?v=")
    versao = url.split("?v=")[1]
    assert versao.isdigit()


def test_static_url_muda_quando_o_arquivo_muda(tmp_path, monkeypatch):
    import app.templating as templating_mod

    static_dir = tmp_path / "static"
    static_dir.mkdir()
    arquivo = static_dir / "js" / "fake.js"
    arquivo.parent.mkdir()
    arquivo.write_text("v1")
    monkeypatch.setattr(templating_mod, "_STATIC_DIR", static_dir)

    url1 = static_url("/static/js/fake.js")
    import os
    import time

    time.sleep(1.05)  # mtime tem granularidade de segundo em alguns FS
    arquivo.write_text("v2")
    os.utime(arquivo, None)
    url2 = static_url("/static/js/fake.js")

    assert url1 != url2


def test_static_url_arquivo_ausente_devolve_path_sem_query():
    assert static_url("/static/js/nao_existe_de_verdade.js") == "/static/js/nao_existe_de_verdade.js"


def test_static_url_registrado_como_global_jinja():
    from app.templating import templates

    assert templates.env.globals.get("static_url") is static_url


def test_shell_js_real_existe_e_gera_url_valida():
    # Sanity: o arquivo real (não um fixture) resolve — garante que o path de
    # produção (`_STATIC_DIR` real do projeto) está correto.
    from app.templating import _STATIC_DIR

    assert (_STATIC_DIR / "js" / "shell.js").is_file()
    assert Path(_STATIC_DIR).name == "static"
