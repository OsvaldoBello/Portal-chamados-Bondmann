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


# --------------------------------------------------------------------------
# `cor_rotulo` e `paragrafos_mensagem` (formatação do balão de chat)
# --------------------------------------------------------------------------
def test_cor_rotulo_e_estavel_e_neutra_para_vazio():
    from app.templating import _ROTULO_NEUTRO, cor_rotulo

    # Determinístico entre processos (MD5, não `hash()`): a mesma categoria
    # precisa sair sempre com a mesma cor, senão o badge "pisca" a cada deploy.
    assert cor_rotulo("Sistemas ERP") == cor_rotulo("  sistemas erp  ")
    for vazio in ("", "   ", None):
        assert cor_rotulo(vazio) == _ROTULO_NEUTRO


def test_paragrafos_de_texto_vazio_some():
    from app.templating import paragrafos_mensagem

    for vazio in ("", None):
        assert paragrafos_mensagem(vazio) == []
    # Só linhas em branco: nenhum bloco é aberto (o fechamento é no-op).
    assert paragrafos_mensagem("\n\n   \n") == []


def test_paragrafos_rejuntam_quebra_manual_mas_preservam_lista():
    from app.templating import paragrafos_mensagem

    texto = (
        "Bom dia, favor incluir o nome\r\n"
        "da assessora no relatório.\n"
        "\n"
        "Preciso de:\n"
        "- coluna de região\n"
        "- coluna de assessora\n"
    )
    blocos = paragrafos_mensagem(texto)
    # 1º parágrafo: quebrado na mão (colado de outro documento) → vira uma linha
    # só, para o navegador reformatar na largura real do balão.
    assert blocos[0] == "Bom dia, favor incluir o nome da assessora no relatório."
    # 2º: tem marcador de lista → cada item continua em sua linha.
    assert blocos[1] == "Preciso de:\n- coluna de região\n- coluna de assessora"
    assert len(blocos) == 2
