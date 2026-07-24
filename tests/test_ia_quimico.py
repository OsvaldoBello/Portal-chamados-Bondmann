"""Testes da F4 — agente Químico de dois passes (banco e modelo mockados).

Cobrem o DoD da F4 (plano_md_mestre_IA.md, Seção 7) e os invariantes da
Seção 8.2 aplicáveis em unit:

- Passe A com contexto LIMPO: o payload enviado ao modelo no Passe A não
  contém valores-sentinela originados da base ``base_quimico_*``.
- Passe B → só nota interna: a persistência da nota do Químico passa por
  ``_salvar_nota_interna`` (``is_interna=true`` fixado, sem parâmetro).
- Saída do Passe B fora dos logs em texto claro (``caplog``).
- ``ia_triagens.resultado`` do Passe B guarda METADADOS, nunca a pré-análise.
- PERGUNTAS ⇒ Passe B nem roda (base sigilosa nunca encosta no canal público).
- Falha do Passe B ⇒ degradação graciosa (nota do Passe A).
- Recuperação seletiva (``identificar_produtos``) e fatiamento de fichas
  (``fatiar_fichas``) — funções puras, com dados SINTÉTICOS (nenhum dado real
  da base entra no repositório).

Os cenários de red team comportamental são da F5 (marker ``redteam``).
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager, contextmanager
from unittest.mock import AsyncMock, patch

import pytest

from app.config import Settings
from app.ia import triagem
from app.ia.cliente import RespostaModelo
from app.ia.contexto_quimico import ContextoQuimico, formatar_contexto, identificar_produtos
from scripts.ingestao_base_quimico import fatiar_fichas

# ------------------------------------------------------------------
# Fixtures / fakes (mesmo padrão de tests/test_ia_triagem.py)
# ------------------------------------------------------------------

_CHAMADO_QUIMICO = {
    "id": "cid-q",
    "codigo": "BOND-2026-00600",
    "titulo": "Registro de Ocorrência — Cliente Exemplo",
    "descricao": "Produto apresentou excesso de espuma no tanque de lavagem.",
    "status": "NOVO",
    "cliente_id": "autor-uuid",
    "operador_id": None,
    "prioridade": "MEDIA",
    "dados_formulario": json.dumps({"produto": "PRODTESTE X", "lote": "1234567890123"}),
    "departamento_id": "dep-quimico",
    "categoria": "Registro de Ocorrência",
    "departamento": "Dpto Químico",
}

_SAIDA_A_SUFICIENTE = {
    "informacoes_suficientes": True,
    "confianca": "ALTA",
    "pre_analise": "Relato objetivo de espuma em uso do produto informado.",
    "categoria_sugerida": None,
    "prioridade_sugerida": None,
    "perguntas": [],
    "termos_busca": ["espuma", "tanque"],
}

_SAIDA_A_INSUFICIENTE = {
    "informacoes_suficientes": False,
    "confianca": "ALTA",
    "pre_analise": "Faltam dados de diluição e equipamento.",
    "categoria_sugerida": None,
    "prioridade_sugerida": None,
    "perguntas": ["Qual diluição foi utilizada?"],
    "termos_busca": ["espuma"],
}

_SAIDA_B_OK = {
    "pre_analise": (
        "HIPOTESE-SENTINELA-6M: provável dosagem acima da faixa; conferir "
        "diluição e contaminação por óleo antes de suspeitar do lote."
    ),
    "confianca": "MEDIA",
    "escalar_para_quimico": True,
    "produto_reconhecido": "PRODTESTE X",
    "dados_faltantes": ["Diluição utilizada", "Qualidade da água"],
}

# Valores-sentinela ÚNICOS simulando dados sigilosos da base (Seção 8.2, item 2:
# fixture semeia sentinelas e o teste garante que não aparecem no payload do A).
_CTX_SENTINELA = ContextoQuimico(
    produtos=[
        {
            "chave_produto": "SEG|S9999|PRODTESTE X|L1|001",
            "nome": "PRODTESTE X",
            "segmento": "SEGTESTE",
            "aplicacao": "APLICACAO-SENTINELA-77341",
            "familia_tecnica": "FAMILIA-SENTINELA-88452",
            "tipo_uso": "Limpeza",
            "componentes": ["COMPONENTE-SENTINELA-99563"],
            "orientacao": "ORIENTACAO-SENTINELA-11674",
        }
    ],
    fichas=["FICHA-SENTINELA-22785: usar puro, não misturar com ácidos."],
    diagnosticos=[{"sintoma": "Excesso de espuma", "Possíveis causas técnicas": "dosagem elevada"}],
    regras_sigilo=[
        {
            "tipo_informacao": "Composição, fórmula, percentuais ou quantidades",
            "Pode responder?": "Não para cliente externo",
        }
    ],
)


def _settings(**overrides) -> Settings:
    base = dict(
        session_secret="segredo-real-de-teste-nao-default",
        csrf_secret="outro-segredo-real-de-teste-nao-default",
        ia_triagem_ativa=True,
        ia_triagem_api_key="k-triagem",
        ia_triagem_departamentos="Dpto Químico",
        ia_triagem_model="gpt-5.4-mini",
        ia_triagem_base_url="https://api.openai.com/v1",
        ia_worker_database_url="postgresql://ia_worker:x@localhost/teste",
    )
    base.update(overrides)
    return Settings(**base)


class FakeConn:
    """Conexão administrativa scriptada (subset do FakeConn da suíte F1)."""

    def __init__(self, chamado: dict | None = None):
        self._chamado = dict(chamado or _CHAMADO_QUIMICO)
        self.triagem_inserts: list[tuple] = []
        self.executes: list[tuple[str, tuple]] = []

    async def fetchrow(self, sql: str, *args):
        if "FROM chamados" in sql:
            return dict(self._chamado)
        if "ultima_rodada" in sql:
            return {"ultima_rodada": 0, "ultima_acao": None, "ultima_em": None}
        raise AssertionError(f"fetchrow inesperado: {sql}")

    async def fetchval(self, sql: str, *args):
        if "FROM perfis" in sql:
            return "perfil-ia-uuid"
        if "INSERT INTO ia_triagens" in sql:
            self.triagem_inserts.append(args)
            return len(self.triagem_inserts)
        raise AssertionError(f"fetchval inesperado: {sql}")

    async def fetch(self, sql: str, *args):
        if "websearch_to_tsquery" in sql:
            return []
        if "FROM categorias" in sql:
            return [{"nome": "Registro de Ocorrência"}, {"nome": "Visita Técnica"}]
        raise AssertionError(f"fetch inesperado: {sql}")

    async def execute(self, sql: str, *args):
        self.executes.append((sql, args))


@pytest.fixture(autouse=True)
def _reset_cache_perfil():
    triagem._perfil_ia_id = None
    yield
    triagem._perfil_ia_id = None


@contextmanager
def _patched(
    conn: FakeConn,
    settings: Settings,
    completar: AsyncMock,
    contexto_b: ContextoQuimico | None = _CTX_SENTINELA,
    playbook: list[dict] | None = None,
):
    @asynccontextmanager
    async def _fake_admin():
        yield conn

    montar_ctx = AsyncMock(return_value=contexto_b)
    pb = AsyncMock(return_value=playbook if playbook is not None else [])
    with (
        patch.object(triagem, "get_settings", return_value=settings),
        patch.object(triagem, "admin_connection", _fake_admin),
        patch.object(triagem.cliente, "completar_chat", completar),
        patch.object(triagem, "notificar_nova_mensagem_email", AsyncMock()),
        patch.object(triagem.contexto_quimico, "playbook_perguntas", pb),
        patch.object(triagem.contexto_quimico, "montar_contexto_passe_b", montar_ctx),
    ):
        yield montar_ctx


def _resposta(payload: dict) -> RespostaModelo:
    return RespostaModelo(conteudo=json.dumps(payload), tokens_entrada=1500, tokens_saida=300)


def _mensagens_interna(conn: FakeConn) -> list[tuple[str, tuple]]:
    return [(sql, args) for sql, args in conn.executes if "INSERT INTO mensagens" in sql]


# ------------------------------------------------------------------
# Fluxo de dois passes
# ------------------------------------------------------------------


async def test_quimico_suficiente_roda_dois_passes_e_nota_vem_do_passe_b():
    conn = FakeConn()
    completar = AsyncMock(side_effect=[_resposta(_SAIDA_A_SUFICIENTE), _resposta(_SAIDA_B_OK)])
    with _patched(conn, _settings(), completar):
        await triagem.executar_triagem("cid-q")

    assert completar.await_count == 2, "Passe A + Passe B"
    # Duas linhas de auditoria: passes A e B da mesma rodada.
    passes = [args[2] for args in conn.triagem_inserts]
    assert passes == ["A", "B"]
    # Nota interna única, com o texto do Passe B, is_interna=true fixado no SQL.
    notas = _mensagens_interna(conn)
    assert len(notas) == 1
    sql, args = notas[0]
    assert "true" in sql and "is_interna" in sql
    assert "HIPOTESE-SENTINELA-6M" in args[2]
    assert "ESCALAR" in args[2]  # escalar_para_quimico=True sinalizado
    assert "Diluição utilizada" in args[2]  # dados_faltantes na nota


async def test_resultado_do_passe_b_guarda_metadados_nunca_a_pre_analise():
    conn = FakeConn()
    completar = AsyncMock(side_effect=[_resposta(_SAIDA_A_SUFICIENTE), _resposta(_SAIDA_B_OK)])
    with _patched(conn, _settings(), completar):
        await triagem.executar_triagem("cid-q")

    args_b = conn.triagem_inserts[1]
    resultado_b = json.loads(args_b[4])
    assert "pre_analise" not in resultado_b, "Seção 4.1: resultado do B só tem metadados"
    assert "HIPOTESE-SENTINELA-6M" not in json.dumps(resultado_b)
    assert resultado_b["escalar_para_quimico"] is True
    assert resultado_b["produto_reconhecido"] == "PRODTESTE X"
    # Ação do B é NOTA_INTERNA (nunca outra coisa).
    assert args_b[3] == "NOTA_INTERNA"


async def test_payload_do_passe_a_nao_contem_sentinelas_da_base():
    """Invariante 8.2 (item 2): o dict enviado ao modelo no Passe A não contém
    chaves/valores originados de base_quimico_produtos/fichas."""
    conn = FakeConn()
    completar = AsyncMock(side_effect=[_resposta(_SAIDA_A_SUFICIENTE), _resposta(_SAIDA_B_OK)])
    with _patched(conn, _settings(), completar):
        await triagem.executar_triagem("cid-q")

    payload_a = json.dumps(completar.await_args_list[0].kwargs["mensagens"], ensure_ascii=False)
    for sentinela in (
        "APLICACAO-SENTINELA-77341",
        "FAMILIA-SENTINELA-88452",
        "COMPONENTE-SENTINELA-99563",
        "ORIENTACAO-SENTINELA-11674",
        "FICHA-SENTINELA-22785",
    ):
        assert sentinela not in payload_a, f"Passe A vazou {sentinela}"
    # O mesmo sentinela ESTÁ no payload do Passe B (prova que o teste morde).
    payload_b = json.dumps(completar.await_args_list[1].kwargs["mensagens"], ensure_ascii=False)
    assert "FICHA-SENTINELA-22785" in payload_b
    assert "COMPONENTE-SENTINELA-99563" in payload_b


async def test_perguntas_nao_roda_passe_b_nem_toca_na_base():
    """PERGUNTAS (canal público) ⇒ o Passe B nem executa — a base sigilosa
    nunca participa da rodada que fala com o autor (Seção 3.1)."""
    conn = FakeConn()
    completar = AsyncMock(side_effect=[_resposta(_SAIDA_A_INSUFICIENTE)])
    settings = _settings(ia_triagem_modo_sombra=False)
    with _patched(conn, settings, completar) as montar_ctx:
        await triagem.executar_triagem("cid-q")

    assert completar.await_count == 1, "só o Passe A rodou"
    montar_ctx.assert_not_awaited()
    passes = [args[2] for args in conn.triagem_inserts]
    assert passes == ["A"]
    # Mensagem pública (pergunta), nenhuma nota interna.
    publicas = [
        (sql, args)
        for sql, args in conn.executes
        if "INSERT INTO mensagens" in sql and "false" in sql
    ]
    assert len(publicas) == 1
    assert not [s for s, _ in _mensagens_interna(conn) if "true" in s]


async def test_falha_do_passe_b_degrada_para_nota_do_passe_a():
    conn = FakeConn()
    completar = AsyncMock(
        side_effect=[_resposta(_SAIDA_A_SUFICIENTE), _resposta({"lixo": "sem schema"}),
                     _resposta({"lixo": "sem schema"})]  # 1 retry de JSON também falha
    )
    with _patched(conn, _settings(), completar):
        await triagem.executar_triagem("cid-q")

    # Auditoria: A=NOTA_INTERNA, B=ERRO.
    assert [args[2] for args in conn.triagem_inserts] == ["A", "B"]
    args_b = conn.triagem_inserts[1]
    assert args_b[3] == "ERRO"
    assert "erro" in json.loads(args_b[4])
    # A nota saiu mesmo assim, com o texto do Passe A (fallback).
    notas = _mensagens_interna(conn)
    assert len(notas) == 1
    assert _SAIDA_A_SUFICIENTE["pre_analise"] in notas[0][1][2]


async def test_saida_do_passe_b_nao_vai_a_log_em_texto_claro(caplog):
    """DoD F4: a pré-análise do Passe B não aparece nos logs (Seção 3.2)."""
    conn = FakeConn()
    completar = AsyncMock(side_effect=[_resposta(_SAIDA_A_SUFICIENTE), _resposta(_SAIDA_B_OK)])
    with _patched(conn, _settings(), completar), caplog.at_level("DEBUG"):
        await triagem.executar_triagem("cid-q")
    assert "HIPOTESE-SENTINELA-6M" not in caplog.text


async def test_departamento_nao_quimico_segue_passe_unico():
    chamado_ti = {**_CHAMADO_QUIMICO, "departamento": "TI", "dados_formulario": "{}"}
    conn = FakeConn(chamado=chamado_ti)
    completar = AsyncMock(side_effect=[_resposta(_SAIDA_A_SUFICIENTE)])
    settings = _settings(ia_triagem_departamentos="TI")
    with _patched(conn, settings, completar) as montar_ctx:
        await triagem.executar_triagem("cid-q")
    assert completar.await_count == 1
    montar_ctx.assert_not_awaited()
    assert [args[2] for args in conn.triagem_inserts] == ["UNICO"]


async def test_persistencia_da_nota_nao_aceita_is_interna_como_parametro():
    """Invariante 8.2 (item 1) — inspeção de assinatura (vale para o Passe B,
    que persiste exclusivamente por esta função)."""
    import inspect

    parametros = inspect.signature(triagem._salvar_nota_interna).parameters
    assert "is_interna" not in parametros


# ------------------------------------------------------------------
# Recuperação seletiva (função pura)
# ------------------------------------------------------------------

_NOMES = ["26", "ADITIVO 1090", "PRODTESTE X", "BRIL"]


def test_identificar_produtos_prioriza_o_select_do_formulario():
    assert identificar_produtos(_NOMES, "prodteste x", "qualquer texto") == ["PRODTESTE X"]


def test_identificar_produtos_no_texto_livre_palavra_inteira():
    achados = identificar_produtos(_NOMES, None, "Usamos o produto 26 no painel")
    assert achados == ["26"]
    # "26" NÃO casa com "26000 volts" (limite de palavra).
    assert identificar_produtos(_NOMES, None, "rigidez de 26000 volts") == []


def test_identificar_produtos_nome_mais_longo_vence():
    achados = identificar_produtos(_NOMES, None, "Problema com ADITIVO 1090 no fluido")
    assert achados[0] == "ADITIVO 1090"


def test_formatar_contexto_lista_componentes_sem_quantidades():
    texto = formatar_contexto(_CTX_SENTINELA)
    assert "COMPONENTE-SENTINELA-99563" in texto
    assert "SEM proporções" in texto
    assert "FICHA-SENTINELA-22785" in texto
    assert "Regras de conduta e sigilo" in texto


# ------------------------------------------------------------------
# Fatiamento das fichas (função pura — dados sintéticos)
# ------------------------------------------------------------------


def test_fatiar_fichas_agrupa_pagina_por_produto_e_continua_ficha():
    paginas = [
        "FICHA DO PRODTESTE X — aplicação geral",
        "continuação sem nome de produto (segunda página da ficha)",
        "BRIL: ficha do abrilhantador",
    ]
    fichas, sem_dona = fatiar_fichas(paginas, _NOMES)
    assert sem_dona == []
    assert "aplicação geral" in fichas["PRODTESTE X"]
    assert "segunda página" in fichas["PRODTESTE X"], "página sem dono segue a anterior"
    assert "abrilhantador" in fichas["BRIL"]


def test_fatiar_fichas_pagina_inicial_sem_produto_e_relatada():
    fichas, sem_dona = fatiar_fichas(["capa do documento sem produto"], _NOMES)
    assert fichas == {}
    assert sem_dona == [1]


def test_fatiar_fichas_nome_curto_nao_casa_em_numero_maior():
    fichas, sem_dona = fatiar_fichas(["rigidez dielétrica de 26000 volts"], ["26"])
    assert fichas == {}
    assert sem_dona == [1]
