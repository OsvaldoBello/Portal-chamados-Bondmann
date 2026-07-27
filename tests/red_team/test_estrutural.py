"""Red team estrutural do agente Químico (F5, Seção 8.3 do plano IA).

Camada "sempre verde por construção": roda o motor de dois passes com o
modelo MOCKADO contra o corpus de entradas maliciosas (``tests/red_team/casos/``)
e prova que os invariantes da Seção 8.2 seguram — a proteção é estrutural
(o que não está no contexto não pode vazar), então nenhum texto malicioso no
CHAMADO consegue romper o isolamento, independente do que o autor escreva.

A camada que mede o COMPORTAMENTO do modelo (obediência ao prompt diante da
mesma malícia) é ``test_comportamental.py`` — chamada real, opt-in.
"""

from __future__ import annotations

import dataclasses
import inspect
import json
import re
from unittest.mock import AsyncMock

import pytest

from app.ia import contexto_quimico, triagem
from tests.red_team._corpus import carregar_casos, caso_para_chamado
from tests.test_ia_quimico import (
    _CHAMADO_QUIMICO,
    _CTX_SENTINELA,
    _SAIDA_A_INSUFICIENTE,
    _SAIDA_A_SUFICIENTE,
    _SAIDA_B_OK,
    FakeConn,
    _patched,
    _resposta,
    _settings,
)

pytestmark = pytest.mark.redteam

CASOS = carregar_casos()
CASOS_IDS = [c.id for c in CASOS]

# Categorias mínimas exigidas pela Seção 8.3 do plano IA — se um arquivo for
# removido/renomeado sem querer, este teste denuncia o corpus incompleto.
_CATEGORIAS_MINIMAS = {
    "pedido_direto_formulacao",
    "ignore_instrucoes",
    "autoridade_falsa",
    "extracao_incremental",
    "quantidades_aproximadas",
    "exfiltracao_nota_publica",
}

_SENTINELAS = (
    "APLICACAO-SENTINELA-77341",
    "FAMILIA-SENTINELA-88452",
    "COMPONENTE-SENTINELA-99563",
    "ORIENTACAO-SENTINELA-11674",
    "FICHA-SENTINELA-22785",
)


def test_corpus_cobre_todas_as_categorias_minimas():
    assert {c.id for c in CASOS} == _CATEGORIAS_MINIMAS


@pytest.mark.parametrize("caso", CASOS, ids=CASOS_IDS)
async def test_payload_do_passe_a_nao_vaza_sentinelas_com_chamado_malicioso(caso):
    """Invariante 8.2 (item 2) sob ataque: nenhum texto malicioso no CHAMADO
    consegue fazer a base sigilosa aparecer no payload do Passe A — o Passe A
    simplesmente não recebe essas fontes, então não há o que a malícia explore."""
    chamado = caso_para_chamado(caso, base=_CHAMADO_QUIMICO)
    conn = FakeConn(chamado=chamado)
    completar = AsyncMock(side_effect=[_resposta(_SAIDA_A_SUFICIENTE), _resposta(_SAIDA_B_OK)])
    with _patched(conn, _settings(), completar):
        await triagem.executar_triagem("cid-q")

    payload_a = json.dumps(completar.await_args_list[0].kwargs["mensagens"], ensure_ascii=False)
    for sentinela in _SENTINELAS:
        assert sentinela not in payload_a, f"[{caso.id}] Passe A vazou {sentinela}"
    # O ataque está de fato no payload (prova que o teste morde o texto malicioso).
    assert caso.descricao in payload_a or caso.titulo in payload_a


@pytest.mark.parametrize("caso", CASOS, ids=CASOS_IDS)
async def test_ciclo_de_perguntas_nunca_roda_passe_b_mesmo_com_ataque(caso):
    """Categorias que tentam extração incremental/exfiltração via canal público:
    mesmo que o modelo (Passe A) decida que falta informação, o Passe B (o
    único que toca a base sigilosa) não roda nesta rodada — a base nunca
    participa do canal que fala com o autor (Seção 3.1)."""
    chamado = caso_para_chamado(caso, base=_CHAMADO_QUIMICO)
    conn = FakeConn(chamado=chamado)
    completar = AsyncMock(side_effect=[_resposta(_SAIDA_A_INSUFICIENTE)])
    settings = _settings(ia_triagem_modo_sombra=False)
    with _patched(conn, settings, completar) as montar_ctx:
        await triagem.executar_triagem("cid-q")

    montar_ctx.assert_not_awaited()
    passes = [args[2] for args in conn.triagem_inserts]
    assert passes == ["A"]
    # Só mensagem pública (pergunta) saiu — nenhuma nota interna nesta rodada.
    notas_internas = [
        (sql, args) for sql, args in conn.executes if "INSERT INTO mensagens" in sql and "true" in sql
    ]
    assert notas_internas == []


@pytest.mark.parametrize("caso", CASOS, ids=CASOS_IDS)
async def test_resultado_auditado_do_passe_b_nunca_contem_a_pre_analise(caso):
    """Mesmo com chamado malicioso, ``ia_triagens.resultado`` do Passe B segue
    só com metadados (Seção 4.1) — um único lugar sensível a proteger."""
    chamado = caso_para_chamado(caso, base=_CHAMADO_QUIMICO)
    conn = FakeConn(chamado=chamado)
    completar = AsyncMock(side_effect=[_resposta(_SAIDA_A_SUFICIENTE), _resposta(_SAIDA_B_OK)])
    with _patched(conn, _settings(), completar):
        await triagem.executar_triagem("cid-q")

    args_b = conn.triagem_inserts[1]
    resultado_b = json.loads(args_b[4])
    assert "pre_analise" not in resultado_b
    assert _SAIDA_B_OK["pre_analise"] not in json.dumps(resultado_b)


def test_extracao_incremental_tem_resposta_de_rodada_2_maliciosa():
    """O cenário multi-rodada do corpus precisa realmente ter uma 2ª rodada —
    senão o teste acima não estaria cobrindo extração incremental de fato."""
    caso = next(c for c in CASOS if c.id == "extracao_incremental")
    assert caso.resposta_rodada2 and len(caso.resposta_rodada2) > 10


def test_exfiltracao_nota_publica_tem_resposta_de_rodada_2_maliciosa():
    caso = next(c for c in CASOS if c.id == "exfiltracao_nota_publica")
    assert caso.resposta_rodada2 and len(caso.resposta_rodada2) > 10


# ------------------------------------------------------------------
# Invariantes estáticos (não dependem de nenhum caso do corpus — provam a
# camada mais baixa: nem o CÓDIGO tem como buscar quantidades).
# ------------------------------------------------------------------


def test_contexto_quimico_nao_tem_campo_de_quantidade_nem_formulacao():
    """``ContextoQuimico`` (o que chega ao Passe B) não tem nenhum campo que
    possa carregar quantidade/proporção — a estrutura de dados em si não
    comporta o dado sigiloso (C7)."""
    campos = {f.name for f in dataclasses.fields(contexto_quimico.ContextoQuimico)}
    assert campos == {"produtos", "fichas", "diagnosticos", "regras_sigilo"}
    proibidos = {"formulacoes", "quantidade", "quantidades", "proporcao", "proporcoes"}
    assert campos.isdisjoint(proibidos)


def test_nenhuma_query_do_contexto_quimico_referencia_base_quimico_formulacoes():
    """Nem o Passe A nem o Passe B fazem ``SELECT`` em ``base_quimico_formulacoes``
    (C7 — a exclusão é estrutural: o role ``ia_worker`` nem tem GRANT, mas o
    código também nunca tenta ler a tabela das quantidades). Verifica só as
    QUERIES (``FROM``/``JOIN``), não a prosa do docstring do módulo (que cita
    a tabela ao explicar a exclusão)."""
    fonte = inspect.getsource(contexto_quimico)
    assert not re.search(r"(?:FROM|JOIN)\s+base_quimico_formulacoes", fonte, re.IGNORECASE)


def test_formatar_contexto_declara_explicitamente_sem_proporcoes():
    """``formatar_contexto`` (o texto que de fato vira mensagem `user` do
    Passe B) lista só nomes de componentes, com o rótulo "SEM proporções" —
    documentação executável do invariante."""
    texto = contexto_quimico.formatar_contexto(_CTX_SENTINELA)
    assert "SEM proporções" in texto
    # Sentinelas de nome/aplicação/orientação SÃO esperadas aqui (é o contexto
    # legítimo do Passe B); o que nunca pode aparecer é quantidade — que nem
    # existe no dataclass (teste acima).
    assert "COMPONENTE-SENTINELA-99563" in texto


def test_persistencia_da_nota_interna_fixa_is_interna_sem_parametro():
    """Invariante 8.2 (item 1): ``is_interna`` não é parâmetro da função que
    persiste a nota do Químico — não há como um ataque (ou um bug futuro)
    injetar ``is_interna=False`` por essa via."""
    parametros = inspect.signature(triagem._salvar_nota_interna).parameters
    assert "is_interna" not in parametros


def test_pergunta_publica_e_nota_interna_sao_funcoes_de_persistencia_distintas():
    """O canal público (:func:`_enviar_pergunta_publica`) e o canal interno
    (:func:`_salvar_nota_interna`) são funções DIFERENTES com `is_interna`
    fixado em cada uma (false/true) — nenhum parâmetro compartilhado permite
    uma flag dinâmica trocar o destino de uma mensagem."""
    assert triagem._enviar_pergunta_publica is not triagem._salvar_nota_interna
    for func in (triagem._enviar_pergunta_publica, triagem._salvar_nota_interna):
        assert "is_interna" not in inspect.signature(func).parameters
