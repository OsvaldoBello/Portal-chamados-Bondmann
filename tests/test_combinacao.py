"""Unit da digest de combinação de chamados (`app/domain/combinacao.py`, 0065).

Lógica pura — sem banco, sem RLS. Dois contratos: (1) com a renderização do chat
(`app.templating.paragrafos_mensagem`) — o texto precisa sair em parágrafos
separados por linha em branco, senão a digest chega ao usuário como um bloco
único e ilegível; (2) com a decisão de 2026-07-30 — a digest é o DESCRITIVO do
duplicado, nunca a conversa dele.
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.domain.combinacao import anexos_combinacao, texto_combinacao
from app.templating import paragrafos_mensagem

CRIADO = datetime(2026, 7, 30, 13, 5, tzinfo=UTC)  # 10:05 em Brasília


def _duplicado(**extra):
    base = {
        "codigo": "BOND-2026-00042",
        "titulo": "Não consigo acessar a rede",
        "descricao": "Desde as 9h a pasta compartilhada não abre.",
        "created_at": CRIADO,
        "cliente_nome": "Bruno Souza",
        "cliente_departamento": "Comercial",
        "telefone_contato": "(51) 99999-0000",
    }
    base.update(extra)
    return base


def _mensagem(**extra):
    # Só `anexos` — é o único campo que o repositório busca das mensagens do
    # duplicado desde que a digest deixou de copiar a conversa.
    base = {"anexos": []}
    base.update(extra)
    return base


def test_texto_traz_codigo_autor_assunto_e_descricao():
    texto = texto_combinacao(_duplicado())
    assert "BOND-2026-00042" in texto
    assert "Bruno Souza (Comercial)" in texto
    assert "(51) 99999-0000" in texto
    assert "30/07/2026 10:05" in texto  # convertido para o fuso de Brasília
    assert "Não consigo acessar a rede" in texto
    assert "Desde as 9h" in texto


def test_cada_bloco_vira_um_paragrafo_na_renderizacao():
    """`paragrafos_mensagem` rejunta linhas soltas de um mesmo parágrafo — se a
    digest usasse quebra simples, tudo viraria uma linha só no balão do chat."""
    paragrafos = paragrafos_mensagem(texto_combinacao(_duplicado()))
    assert len(paragrafos) == 4  # cabeçalho + abertura + assunto + descrição
    assert paragrafos[0].startswith("🔗 Chamado BOND-2026-00042 combinado")


def test_digest_nao_traz_a_conversa_do_duplicado():
    """Decisão do gestor 2026-07-30: só o descritivo. Copiar as falas poluía o
    chat do principal — e o que mais aparecia ali eram as perguntas da triagem
    por IA (mensagens públicas como quaisquer outras) do chamado repetido."""
    texto = texto_combinacao(_duplicado())
    assert "continua fora" not in texto
    assert "Mensagens do chamado combinado" not in texto
    # A função não recebe mais as mensagens: não há como uma fala vazar por ela.
    assert texto_combinacao(_duplicado()) == texto


def test_duplicado_sem_autor_ou_contato_nao_quebra():
    texto = texto_combinacao(
        _duplicado(cliente_nome=None, cliente_departamento=None,
                   telefone_contato=None, descricao=""),
    )
    assert "autor não identificado" in texto
    assert "contato" not in texto


def test_anexos_sao_consolidados_sem_repetir_path():
    mensagens = [
        _mensagem(anexos=[{"path": "e1/c1/foto.png", "nome": "foto.png"}]),
        _mensagem(anexos=[{"path": "e1/c1/foto.png", "nome": "foto.png"},
                          {"path": "e1/c1/log.pdf", "nome": "log.pdf"}]),
        _mensagem(anexos=None),
    ]
    anexos = anexos_combinacao(mensagens)
    assert [a["path"] for a in anexos] == ["e1/c1/foto.png", "e1/c1/log.pdf"]
