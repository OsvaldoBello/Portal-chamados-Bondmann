"""Testes do motor de triagem por IA (F1 — ``app/ia/triagem.py``), banco e modelo mockados.

Cobrem o DoD da F1 (plano_md_mestre_IA.md, Seção 7) e os testes de invariante
aplicáveis (Seção 8.2): kill switch sem efeito colateral, uma chamada
estruturada + 1 retry em JSON inválido → ERRO silencioso, falha de provedor
nunca levanta, idempotência (verificação prévia + conflito não duplica nota),
guarda de atendimento (NOVO/sem operador) e nota SEMPRE interna
(``is_interna=true`` fixado em código, não parâmetro).
"""

from __future__ import annotations

import asyncio
import inspect
import json
from contextlib import asynccontextmanager, contextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.config import Settings
from app.ia import triagem
from app.ia.cliente import RespostaModelo
from app.ia.schemas import SaidaTriagem

# ------------------------------------------------------------------
# Fixtures / fakes
# ------------------------------------------------------------------

_CHAMADO = {
    "id": "cid",
    "codigo": "BOND-2026-00500",
    "titulo": "Computador não liga",
    "descricao": "Desde ontem a máquina da recepção não dá vídeo.",
    "status": "NOVO",
    "cliente_id": "autor-uuid",
    "operador_id": None,
    "prioridade": "MEDIA",
    "dados_formulario": "{}",  # jsonb chega como str no asyncpg
    "departamento_id": "dep-ti",
    "categoria": "Hardware",
    "departamento": "TI",
}

_SAIDA_OK = {
    "informacoes_suficientes": False,
    "confianca": "MEDIA",
    "pre_analise": "Provável falha de fonte ou cabo de vídeo.",
    "categoria_sugerida": None,
    "prioridade_sugerida": "ALTA",
    "perguntas": ["O monitor acende alguma luz?", "Há barulho de ventoinha ao ligar?"],
    "termos_busca": ["não liga", "sem vídeo"],
}


def _settings(**overrides) -> Settings:
    base = dict(
        session_secret="segredo-real-de-teste-nao-default",
        csrf_secret="outro-segredo-real-de-teste-nao-default",
        ia_triagem_ativa=True,
        ia_triagem_api_key="k-triagem",
        ia_triagem_departamentos="TI",
        # Explícito para não herdar GROQ_MODEL/GROQ_BASE_URL do `.env` local do
        # dev (alias de transição da F0 tem precedência sobre o default).
        ia_triagem_model="gpt-5.4-mini",
        ia_triagem_base_url="https://api.openai.com/v1",
    )
    base.update(overrides)
    return Settings(**base)


class FakeConn:
    """Conexão administrativa scriptada: responde pelos SQLs que o motor usa."""

    def __init__(
        self,
        chamado: dict | None = _CHAMADO,
        ultima_rodada: int = 0,
        ultima_acao: str | None = None,
        autor_respondeu: bool = False,
        conversa: tuple[dict, ...] = (),
        categorias: tuple[str, ...] = ("Hardware", "Redes", "Sistemas"),
        perfil_id: str | None = "perfil-ia-uuid",
        triagem_insert_id: int | None = 1,
        semelhantes: tuple[dict, ...] = (),
        busca_quebra: bool = False,
    ):
        self._chamado = chamado
        self._ultima_rodada = ultima_rodada
        self._ultima_acao = ultima_acao
        self._autor_respondeu = autor_respondeu
        self._conversa = conversa
        self._categorias = categorias
        self._perfil_id = perfil_id
        self._triagem_insert_id = triagem_insert_id
        self._semelhantes = semelhantes
        self._busca_quebra = busca_quebra
        self.busca_args: tuple | None = None  # captura os args da busca FTS (F3)
        self.triagem_inserts: list[tuple] = []
        self.executes: list[tuple[str, tuple]] = []

    async def fetchrow(self, sql: str, *args):
        if "FROM chamados" in sql:
            return dict(self._chamado) if self._chamado is not None else None
        if "ultima_rodada" in sql:
            return {
                "ultima_rodada": self._ultima_rodada,
                "ultima_acao": self._ultima_acao,
                "ultima_em": "2026-07-22T10:00:00Z",
            }
        raise AssertionError(f"fetchrow inesperado: {sql}")

    async def fetchval(self, sql: str, *args):
        if "FROM perfis" in sql:
            return self._perfil_id
        if "INSERT INTO ia_triagens" in sql:
            self.triagem_inserts.append(args)
            return self._triagem_insert_id
        if "FROM mensagens" in sql:
            return 1 if self._autor_respondeu else None
        raise AssertionError(f"fetchval inesperado: {sql}")

    async def fetch(self, sql: str, *args):
        if "websearch_to_tsquery" in sql:  # busca de semelhantes (F3)
            if self._busca_quebra:
                raise RuntimeError("índice FTS indisponível")
            self.busca_args = args
            return [dict(s) for s in self._semelhantes]
        if "FROM categorias" in sql:
            return [{"nome": n} for n in self._categorias]
        if "FROM mensagens" in sql:
            return [dict(m) for m in self._conversa]
        raise AssertionError(f"fetch inesperado: {sql}")

    async def execute(self, sql: str, *args):
        self.executes.append((sql, args))


@pytest.fixture(autouse=True)
def _reset_cache_perfil():
    triagem._perfil_ia_id = None
    yield
    triagem._perfil_ia_id = None


@contextmanager
def _patched(conn: FakeConn, settings: Settings, completar: AsyncMock):
    @asynccontextmanager
    async def _fake_admin():
        yield conn

    notificar = AsyncMock()
    with (
        patch.object(triagem, "get_settings", return_value=settings),
        patch.object(triagem, "admin_connection", _fake_admin),
        patch.object(triagem.cliente, "completar_chat", completar),
        patch.object(triagem, "notificar_nova_mensagem_email", notificar),
    ):
        yield notificar


def _resposta_ok() -> RespostaModelo:
    return RespostaModelo(
        conteudo=json.dumps(_SAIDA_OK), tokens_entrada=1200, tokens_saida=300
    )


# ------------------------------------------------------------------
# Kill switch e guardas
# ------------------------------------------------------------------


async def test_kill_switch_sem_nenhum_efeito_colateral():
    admin = MagicMock(side_effect=AssertionError("não pode tocar no banco"))
    completar = AsyncMock(side_effect=AssertionError("não pode chamar o modelo"))
    with (
        patch.object(triagem, "get_settings", return_value=_settings(ia_triagem_ativa=False)),
        patch.object(triagem, "admin_connection", admin),
        patch.object(triagem.cliente, "completar_chat", completar),
    ):
        await triagem.executar_triagem("cid")
    admin.assert_not_called()
    completar.assert_not_awaited()


async def test_sem_api_key_tambem_desliga():
    admin = MagicMock(side_effect=AssertionError("não pode tocar no banco"))
    with (
        patch.object(triagem, "get_settings", return_value=_settings(ia_triagem_api_key="")),
        patch.object(triagem, "admin_connection", admin),
    ):
        await triagem.executar_triagem("cid")
    admin.assert_not_called()


async def test_departamento_fora_da_lista_nao_tria():
    conn = FakeConn(chamado={**_CHAMADO, "departamento": "RH"})
    completar = AsyncMock()
    with _patched(conn, _settings(), completar):
        await triagem.executar_triagem("cid")
    completar.assert_not_awaited()
    assert conn.triagem_inserts == [] and conn.executes == []


async def test_atendente_ja_atuando_rodada1_ainda_gera_nota_interna():
    """Ajuste 2026-07-23: assumir o chamado não suprime a pré-análise — a nota
    interna é justamente para quem atende."""
    conn = FakeConn(chamado={**_CHAMADO, "status": "EM_ATENDIMENTO", "operador_id": "op-1"})
    completar = AsyncMock(return_value=_resposta_ok())
    with _patched(conn, _settings(), completar):
        await triagem.executar_triagem("cid")
    assert conn.triagem_inserts[0][3] == "NOTA_INTERNA"
    msg_sql, _ = next((s, a) for s, a in conn.executes if "INSERT INTO mensagens" in s)
    assert "true" in msg_sql  # nota interna, nunca canal público


async def test_atendente_atuando_forca_nota_mesmo_fora_da_sombra():
    """Fora da sombra + saída perguntável + atendente atuando ⇒ nunca interpela
    o autor: a ação degrada para NOTA_INTERNA e nenhum e-mail sai."""
    conn = FakeConn(chamado={**_CHAMADO, "status": "EM_ATENDIMENTO", "operador_id": "op-1"})
    completar = AsyncMock(
        return_value=RespostaModelo(
            conteudo=json.dumps({**_SAIDA_OK, "confianca": "ALTA"})
        )
    )
    with _patched(conn, _settings(ia_triagem_modo_sombra=False), completar) as notificar:
        await triagem.executar_triagem("cid")
    assert conn.triagem_inserts[0][3] == "NOTA_INTERNA"
    notificar.assert_not_awaited()


async def test_chamado_resolvido_nao_tria():
    conn = FakeConn(chamado={**_CHAMADO, "status": "RESOLVIDO", "operador_id": "op-1"})
    completar = AsyncMock()
    with _patched(conn, _settings(), completar):
        await triagem.executar_triagem("cid")
    completar.assert_not_awaited()
    assert conn.triagem_inserts == []


# ------------------------------------------------------------------
# Fluxo feliz (modo sombra)
# ------------------------------------------------------------------


async def test_fluxo_sombra_grava_auditoria_nota_interna_e_historico():
    conn = FakeConn()
    completar = AsyncMock(return_value=_resposta_ok())
    with _patched(conn, _settings(), completar):
        await triagem.executar_triagem("cid")

    completar.assert_awaited_once()  # UMA chamada estruturada por triagem
    assert completar.await_args.kwargs["json_mode"] is True

    # Auditoria em ia_triagens: ação, modelo, tokens e custo gravados (Regra #9).
    assert len(conn.triagem_inserts) == 1
    args = conn.triagem_inserts[0]
    chamado_id, rodada, passe, acao, resultado_json, modelo = args[:6]
    tokens_in, tokens_out, custo, duracao = args[6:]
    assert (chamado_id, rodada, passe, acao) == ("cid", 1, "UNICO", "NOTA_INTERNA")
    assert json.loads(resultado_json)["pre_analise"] == _SAIDA_OK["pre_analise"]
    assert modelo == "gpt-5.4-mini"
    assert (tokens_in, tokens_out) == (1200, 300)
    assert custo == pytest.approx((1200 * 0.75 + 300 * 4.50) / 1_000_000)
    assert isinstance(duracao, int)

    # Nota interna assinada pelo perfil da IA + evento IA_TRIAGEM no histórico.
    sqls = [sql for sql, _ in conn.executes]
    assert any("INSERT INTO mensagens" in s for s in sqls)
    assert any("INSERT INTO historico_chamados" in s for s in sqls)
    msg_sql, msg_args = next((s, a) for s, a in conn.executes if "INSERT INTO mensagens" in s)
    assert "true" in msg_sql  # is_interna fixado no SQL, não vem de parâmetro
    assert msg_args[1] == "perfil-ia-uuid"
    assert "Provável falha de fonte" in msg_args[2]
    hist_sql, hist_args = next(
        (s, a) for s, a in conn.executes if "INSERT INTO historico_chamados" in s
    )
    assert "'IA_TRIAGEM'" in hist_sql
    assert hist_args[1] == "perfil-ia-uuid"


# ------------------------------------------------------------------
# Falhas: JSON inválido (retry único) e provedor fora
# ------------------------------------------------------------------


async def test_json_invalido_faz_um_retry_e_registra_erro_silencioso():
    conn = FakeConn()
    invalida = RespostaModelo(conteudo="não é json", tokens_entrada=10, tokens_saida=5)
    completar = AsyncMock(return_value=invalida)
    with _patched(conn, _settings(), completar):
        await triagem.executar_triagem("cid")

    assert completar.await_count == 2  # 1 chamada + 1 retry, nunca loop
    assert len(conn.triagem_inserts) == 1
    args = conn.triagem_inserts[0]
    assert args[3] == "ERRO"
    assert "json_invalido" in json.loads(args[4])["erro"]
    assert args[6] == 20 and args[7] == 10  # tokens somados das 2 tentativas
    assert conn.executes == []  # sem nota, sem histórico


async def test_falha_de_provedor_nao_levanta_e_nao_bloqueia_abertura():
    conn = FakeConn()
    completar = AsyncMock(side_effect=httpx.HTTPError("timeout"))
    with _patched(conn, _settings(), completar):
        await triagem.executar_triagem("cid")  # não levanta — abertura intacta

    completar.assert_awaited_once()  # falha de provedor não tem retry
    assert len(conn.triagem_inserts) == 1
    assert conn.triagem_inserts[0][3] == "ERRO"
    assert conn.executes == []


# ------------------------------------------------------------------
# Idempotência (UNIQUE + verificação prévia)
# ------------------------------------------------------------------


async def test_task_duplicada_apos_nota_nao_re_tria():
    """Replay da task de abertura depois da triagem concluída: última ação foi
    NOTA_INTERNA (fim de ciclo) → nada acontece, sem gastar modelo."""
    conn = FakeConn(ultima_rodada=1, ultima_acao="NOTA_INTERNA")
    completar = AsyncMock()
    with _patched(conn, _settings(), completar):
        await triagem.executar_triagem("cid")
    completar.assert_not_awaited()
    assert conn.triagem_inserts == []


async def test_conflito_no_insert_nao_duplica_nota():
    conn = FakeConn(triagem_insert_id=None)  # ON CONFLICT DO NOTHING → sem id
    completar = AsyncMock(return_value=_resposta_ok())
    with _patched(conn, _settings(), completar):
        await triagem.executar_triagem("cid")
    assert len(conn.triagem_inserts) == 1
    assert conn.executes == []  # corrida perdida: nem nota nem histórico


async def test_perfil_assistente_ausente_vira_erro_sem_nota():
    conn = FakeConn(perfil_id=None)
    completar = AsyncMock(return_value=_resposta_ok())
    with _patched(conn, _settings(), completar):
        await triagem.executar_triagem("cid")
    assert conn.triagem_inserts[0][3] == "ERRO"
    assert json.loads(conn.triagem_inserts[0][4])["erro"] == "perfil_assistente_ia_ausente"
    assert conn.executes == []


# ------------------------------------------------------------------
# Invariantes e funções puras
# ------------------------------------------------------------------


def test_salvar_nota_interna_nao_aceita_is_interna_como_parametro():
    """Invariante da Seção 8.2: o canal interno é fixado em código."""
    params = inspect.signature(triagem._salvar_nota_interna).parameters
    assert "is_interna" not in params


def test_deve_triar_exige_flag_chave_e_departamento():
    s = _settings()
    assert triagem.deve_triar("TI", s) is True
    assert triagem.deve_triar("RH", s) is False
    assert triagem.deve_triar(None, s) is False
    assert triagem.deve_triar("TI", _settings(ia_triagem_ativa=False)) is False
    assert triagem.deve_triar("TI", _settings(ia_triagem_api_key="")) is False


def test_montar_mensagens_inclui_chamado_e_catalogo():
    msgs = triagem.montar_mensagens(dict(_CHAMADO, dados_formulario={}), ["Hardware", "Redes"])
    assert msgs[0]["role"] == "system" and "triagem" in msgs[0]["content"].lower()
    user = msgs[1]["content"]
    assert "Computador não liga" in user
    assert "Categoria escolhida: Hardware" in user
    assert "- Redes" in user  # catálogo listado


def test_montar_nota_lista_perguntas_quando_insuficiente():
    saida = SaidaTriagem.model_validate(_SAIDA_OK)
    nota = triagem.montar_nota(saida, _CHAMADO)
    assert "Provável falha de fonte" in nota
    assert "1. O monitor acende alguma luz?" in nota
    assert "Prioridade sugerida: ALTA (declarada: MEDIA)" in nota
    assert "Gerado por IA" in nota


def test_montar_nota_omite_sugestoes_iguais_e_perguntas_quando_suficiente():
    saida = SaidaTriagem.model_validate(
        {
            **_SAIDA_OK,
            "informacoes_suficientes": True,
            "perguntas": [],
            "prioridade_sugerida": "MEDIA",  # igual à declarada → omitida
            "categoria_sugerida": "Hardware",  # igual à escolhida → omitida
        }
    )
    nota = triagem.montar_nota(saida, _CHAMADO)
    assert "sugerida" not in nota.lower()
    assert "perguntas" not in nota.lower()


# ------------------------------------------------------------------
# Testes adicionais de funcionalidade (F1)
# ------------------------------------------------------------------


def test_prompt_ti_carrega_sem_o_cabecalho_de_documentacao():
    """O cabeçalho do arquivo (antes do 1º ---) é doc, não prompt."""
    prompt = triagem._prompt("ti")
    assert "Prompt mestre" not in prompt  # cabeçalho não vai ao modelo
    assert "assistente de triagem" in prompt
    assert "informacoes_suficientes" in prompt  # contrato JSON presente
    # A frase pode quebrar linha no markdown — compara com espaços normalizados.
    assert "NUNCA conclui atendimento" in " ".join(prompt.split())


async def test_historico_registra_rodada_passe_e_modo_sombra():
    conn = FakeConn()
    completar = AsyncMock(return_value=_resposta_ok())
    with _patched(conn, _settings(), completar):
        await triagem.executar_triagem("cid")
    _, hist_args = next(
        (s, a) for s, a in conn.executes if "INSERT INTO historico_chamados" in s
    )
    detalhes = json.loads(hist_args[2])
    assert detalhes["rodada"] == 1 and detalhes["passe"] == "UNICO"
    assert detalhes["acao"] == "NOTA_INTERNA" and detalhes["modo_sombra"] is True


async def test_dados_formulario_como_dict_tambem_funciona():
    """O jsonb pode vir como str (asyncpg) ou dict (fakes/futuros codecs)."""
    conn = FakeConn(chamado={**_CHAMADO, "dados_formulario": {}})
    completar = AsyncMock(return_value=_resposta_ok())
    with _patched(conn, _settings(), completar):
        await triagem.executar_triagem("cid")
    assert conn.triagem_inserts[0][3] == "NOTA_INTERNA"


def test_montar_mensagens_rotula_campos_do_formulario_quimico():
    """Prepara o Passe A da F4: os campos dinâmicos entram rotulados."""
    chamado = dict(
        _CHAMADO,
        categoria="Registro de Ocorrência",
        departamento="Dpto Químico",
        dados_formulario={"cidade": "Canoas", "tipo_ocorrencia": "PRODUTO"},
    )
    user = triagem.montar_mensagens(chamado, [])[1]["content"]
    assert "Cidade: Canoas" in user
    assert "Qual o tipo de Ocorrência?: PRODUTO" in user  # rótulo real do schema


async def test_chamado_inexistente_nao_faz_nada():
    conn = FakeConn(chamado=None)
    completar = AsyncMock()
    with _patched(conn, _settings(), completar):
        await triagem.executar_triagem("cid")
    completar.assert_not_awaited()
    assert conn.triagem_inserts == [] and conn.executes == []


def test_saida_triagem_ignora_campos_extras_do_modelo():
    saida = SaidaTriagem.model_validate({**_SAIDA_OK, "divagacao": "blá"})
    assert not hasattr(saida, "divagacao")


def test_saida_triagem_recusa_mais_de_tres_perguntas():
    import pydantic

    with pytest.raises(pydantic.ValidationError):
        SaidaTriagem.model_validate({**_SAIDA_OK, "perguntas": ["a", "b", "c", "d"]})


def test_saida_triagem_exige_pre_analise():
    import pydantic

    with pytest.raises(pydantic.ValidationError):
        SaidaTriagem.model_validate({**_SAIDA_OK, "pre_analise": ""})


# ------------------------------------------------------------------
# F2 — Decisão de ação, perguntas públicas e re-triagem
# ------------------------------------------------------------------

_SAIDA_PERGUNTAVEL = {**_SAIDA_OK, "confianca": "ALTA"}


def _sombra_off(**over):
    return _settings(ia_triagem_modo_sombra=False, **over)


def test_decidir_acao_matrix():
    s_alta = SaidaTriagem.model_validate(_SAIDA_PERGUNTAVEL)
    # Modo sombra: sempre nota interna, mesmo com pergunta viável.
    assert triagem.decidir_acao(s_alta, 1, _settings()) == "NOTA_INTERNA"
    # Atendimento iniciado: força nota interna mesmo fora da sombra (2026-07-23).
    assert (
        triagem.decidir_acao(s_alta, 1, _sombra_off(), atendimento_iniciado=True)
        == "NOTA_INTERNA"
    )
    # Fora da sombra + insuficiente + confiança ALTA + rodada disponível.
    assert triagem.decidir_acao(s_alta, 1, _sombra_off()) == "PERGUNTAS"
    # Última rodada (== teto): nota com lacunas, nunca pergunta.
    assert triagem.decidir_acao(s_alta, 2, _sombra_off()) == "NOTA_INTERNA"
    # Confiança abaixo de ALTA não pergunta (mitigação 10.1).
    s_media = SaidaTriagem.model_validate(_SAIDA_OK)
    assert triagem.decidir_acao(s_media, 1, _sombra_off()) == "NOTA_INTERNA"
    # Informação suficiente: nota direto.
    s_ok = SaidaTriagem.model_validate(
        {**_SAIDA_PERGUNTAVEL, "informacoes_suficientes": True, "perguntas": []}
    )
    assert triagem.decidir_acao(s_ok, 1, _sombra_off()) == "NOTA_INTERNA"


async def test_perguntas_geram_mensagem_publica_sem_nota_e_email():
    """Decisão do usuário 2026-07-23: com informação insuficiente, o ciclo de
    perguntas vem PRIMEIRO — a nota interna fica para o fim do ciclo (rodada
    seguinte ou teto), nunca junto da pergunta."""
    conn = FakeConn()
    completar = AsyncMock(
        return_value=RespostaModelo(
            conteudo=json.dumps(_SAIDA_PERGUNTAVEL), tokens_entrada=100, tokens_saida=50
        )
    )
    with _patched(conn, _sombra_off(), completar) as notificar:
        await triagem.executar_triagem("cid")

    assert conn.triagem_inserts[0][3] == "PERGUNTAS"
    inserts = [(s, a) for s, a in conn.executes if "INSERT INTO mensagens" in s]
    assert len(inserts) == 1  # SÓ a pergunta pública — nenhuma nota nesta rodada
    msg_sql, msg_args = inserts[0]
    assert "false" in msg_sql and "true" not in msg_sql  # canal público, fixado no SQL
    assert "O monitor acende alguma luz?" in msg_args[2]
    assert "BOND-2026-00500" in msg_args[2]  # código citado na mensagem
    # E-mail disparado para o autor (remetente = perfil IA ⇒ destinatário = autor).
    notificar.assert_awaited_once()
    chamado_arg, remetente_arg, conteudo_arg = notificar.await_args.args
    assert chamado_arg["cliente_id"] == "autor-uuid"
    assert remetente_arg == "perfil-ia-uuid"
    assert "poderia responder" in conteudo_arg


async def test_sombra_nunca_envia_mensagem_publica_nem_email():
    conn = FakeConn()
    completar = AsyncMock(
        return_value=RespostaModelo(conteudo=json.dumps(_SAIDA_PERGUNTAVEL))
    )
    with _patched(conn, _settings(), completar) as notificar:  # sombra ON (default)
        await triagem.executar_triagem("cid")
    assert conn.triagem_inserts[0][3] == "NOTA_INTERNA"
    msg_sql, _ = next((s, a) for s, a in conn.executes if "INSERT INTO mensagens" in s)
    assert "true" in msg_sql  # só nota interna
    notificar.assert_not_awaited()


async def test_re_triagem_roda_2_com_conversa_no_contexto():
    conversa = (
        {"remetente_id": "perfil-ia-uuid", "conteudo": "1. O monitor acende alguma luz?"},
        {"remetente_id": "autor-uuid", "conteudo": "Sim, acende mas fica preta."},
    )
    conn = FakeConn(
        ultima_rodada=1, ultima_acao="PERGUNTAS", autor_respondeu=True, conversa=conversa
    )
    completar = AsyncMock(
        return_value=RespostaModelo(
            conteudo=json.dumps({**_SAIDA_OK, "informacoes_suficientes": True, "perguntas": []})
        )
    )
    with _patched(conn, _sombra_off(), completar):
        await triagem.executar_triagem("cid")

    assert conn.triagem_inserts[0][1] == 2  # rodada 2
    assert conn.triagem_inserts[0][3] == "NOTA_INTERNA"
    user_content = completar.await_args.kwargs["mensagens"][1]["content"]
    assert "[Autor] Sim, acende mas fica preta." in user_content
    assert "[Equipe] 1. O monitor acende alguma luz?" in user_content


async def test_re_triagem_sem_resposta_do_autor_nao_roda():
    """Task duplicada logo após a pergunta: o autor ainda não respondeu."""
    conn = FakeConn(ultima_rodada=1, ultima_acao="PERGUNTAS", autor_respondeu=False)
    completar = AsyncMock()
    with _patched(conn, _sombra_off(), completar):
        await triagem.executar_triagem("cid")
    completar.assert_not_awaited()
    assert conn.triagem_inserts == []


async def test_teto_de_rodadas_terceira_rodada_impossivel():
    """DoD F2: com MAX_RODADAS=2, a rodada 3 não acontece nem com resposta."""
    conn = FakeConn(ultima_rodada=2, ultima_acao="PERGUNTAS", autor_respondeu=True)
    completar = AsyncMock()
    with _patched(conn, _sombra_off(), completar):
        await triagem.executar_triagem("cid")
    completar.assert_not_awaited()
    assert conn.triagem_inserts == []


async def test_ultima_rodada_insuficiente_gera_nota_com_lacunas():
    """Rodada 2 (teto) ainda insuficiente → nota interna sinalizando as lacunas."""
    conn = FakeConn(ultima_rodada=1, ultima_acao="PERGUNTAS", autor_respondeu=True)
    completar = AsyncMock(
        return_value=RespostaModelo(conteudo=json.dumps(_SAIDA_PERGUNTAVEL))
    )
    with _patched(conn, _sombra_off(), completar) as notificar:
        await triagem.executar_triagem("cid")
    assert conn.triagem_inserts[0][1] == 2
    assert conn.triagem_inserts[0][3] == "NOTA_INTERNA"  # nunca pergunta no teto
    _, msg_args = next((s, a) for s, a in conn.executes if "INSERT INTO mensagens" in s)
    assert "Informações faltantes" in msg_args[2]
    notificar.assert_not_awaited()


async def test_re_triagem_respeita_guarda_de_atendimento():
    """DoD F2: re-triagem só com status='NOVO' e operador_id IS NULL."""
    conn = FakeConn(
        chamado={**_CHAMADO, "status": "EM_ATENDIMENTO", "operador_id": "op-1"},
        ultima_rodada=1,
        ultima_acao="PERGUNTAS",
        autor_respondeu=True,
    )
    completar = AsyncMock()
    with _patched(conn, _sombra_off(), completar):
        await triagem.executar_triagem("cid")
    completar.assert_not_awaited()
    assert conn.triagem_inserts == []


async def test_falha_de_email_nao_derruba_a_triagem():
    conn = FakeConn()
    completar = AsyncMock(
        return_value=RespostaModelo(conteudo=json.dumps(_SAIDA_PERGUNTAVEL))
    )
    with _patched(conn, _sombra_off(), completar) as notificar:
        notificar.side_effect = RuntimeError("smtp fora")
        await triagem.executar_triagem("cid")  # não levanta
    assert conn.triagem_inserts[0][3] == "PERGUNTAS"  # triagem persistida mesmo assim


def test_montar_pergunta_publica_tem_tom_e_instrucao_de_resposta():
    saida = SaidaTriagem.model_validate(_SAIDA_PERGUNTAVEL)
    texto = triagem.montar_pergunta_publica(saida, _CHAMADO)
    assert "assistente virtual" in texto
    assert "1. O monitor acende alguma luz?" in texto
    assert "responder aqui no chamado ou a este e-mail" in texto


# ------------------------------------------------------------------
# F3 — Busca de chamados semelhantes (FTS) citados na nota interna
# ------------------------------------------------------------------

_SEMELHANTE = {
    "id": "sim-1",
    "codigo": "BOND-2026-00412",
    "titulo": "Erro ao emitir NF-e",
    "resolucao": "Reinstalação do certificado A1 e reconfiguração do emissor.",
}


async def test_nota_cita_semelhantes_com_codigo_e_resolucao():
    conn = FakeConn(semelhantes=(_SEMELHANTE,))
    completar = AsyncMock(return_value=_resposta_ok())
    with _patched(conn, _settings(), completar):
        await triagem.executar_triagem("cid")

    _, msg_args = next((s, a) for s, a in conn.executes if "INSERT INTO mensagens" in s)
    assert "Casos semelhantes já resolvidos:" in msg_args[2]
    assert "BOND-2026-00412" in msg_args[2]
    assert "Reinstalação do certificado A1" in msg_args[2]
    # Auditoria: os códigos citados ficam em ia_triagens.resultado.
    assert json.loads(conn.triagem_inserts[0][4])["semelhantes_codigos"] == ["BOND-2026-00412"]
    # Defesa em profundidade (Seção 5): o SQL filtra pelo departamento do chamado
    # e a consulta une os termos do modelo com OR.
    dep, cid, consulta, limite = conn.busca_args
    assert dep == "dep-ti" and cid == "cid"
    assert consulta == "não liga OR sem vídeo"
    assert limite == 3


async def test_nota_sem_semelhantes_omite_secao():
    """DoD F3: degradação graciosa — seção omitida, nunca inventada."""
    conn = FakeConn()  # busca responde vazio
    completar = AsyncMock(return_value=_resposta_ok())
    with _patched(conn, _settings(), completar):
        await triagem.executar_triagem("cid")
    _, msg_args = next((s, a) for s, a in conn.executes if "INSERT INTO mensagens" in s)
    assert "Casos semelhantes" not in msg_args[2]
    assert "semelhantes_codigos" not in json.loads(conn.triagem_inserts[0][4])


async def test_sem_termos_busca_nao_toca_no_banco():
    conn = FakeConn()
    completar = AsyncMock(
        return_value=RespostaModelo(conteudo=json.dumps({**_SAIDA_OK, "termos_busca": []}))
    )
    with _patched(conn, _settings(), completar):
        await triagem.executar_triagem("cid")
    assert conn.busca_args is None  # busca nem executou
    assert conn.triagem_inserts[0][3] == "NOTA_INTERNA"  # triagem seguiu normal


async def test_falha_na_busca_nao_derruba_a_triagem():
    """Regra #5: erro na busca é silencioso — a nota sai sem a seção."""
    conn = FakeConn(busca_quebra=True)
    completar = AsyncMock(return_value=_resposta_ok())
    with _patched(conn, _settings(), completar):
        await triagem.executar_triagem("cid")
    assert conn.triagem_inserts[0][3] == "NOTA_INTERNA"
    _, msg_args = next((s, a) for s, a in conn.executes if "INSERT INTO mensagens" in s)
    assert "Casos semelhantes" not in msg_args[2]
    assert "Provável falha de fonte" in msg_args[2]  # nota íntegra mesmo assim


async def test_acao_perguntas_nao_busca_semelhantes():
    """A pergunta pública ao autor não cita casos internos — a busca fica para
    a rodada que fecha o ciclo com a nota interna."""
    conn = FakeConn(semelhantes=(_SEMELHANTE,))
    completar = AsyncMock(
        return_value=RespostaModelo(conteudo=json.dumps(_SAIDA_PERGUNTAVEL))
    )
    with _patched(conn, _sombra_off(), completar):
        await triagem.executar_triagem("cid")
    assert conn.triagem_inserts[0][3] == "PERGUNTAS"
    assert conn.busca_args is None
    _, msg_args = next((s, a) for s, a in conn.executes if "INSERT INTO mensagens" in s)
    assert "BOND-2026-00412" not in msg_args[2]


def test_montar_nota_com_semelhantes_e_truncamento():
    saida = SaidaTriagem.model_validate({**_SAIDA_OK, "informacoes_suficientes": True, "perguntas": []})
    longa = dict(_SEMELHANTE, resolucao="solução  muito\n detalhada " * 40)
    nota = triagem.montar_nota(saida, _CHAMADO, [longa])
    linha = next(li for li in nota.splitlines() if "BOND-2026-00412" in li)
    assert "…" in linha  # resolução truncada
    assert "\n detalhada" not in linha  # espaços normalizados numa linha só
    # Sem resolução registrada: cita só código e título, sem seção vazia.
    nota2 = triagem.montar_nota(saida, _CHAMADO, [dict(_SEMELHANTE, resolucao=None)])
    assert "resolução registrada" not in nota2
    assert "BOND-2026-00412" in nota2


def test_montar_consulta_une_termos_com_or_e_ignora_vazios():
    from app.repositories.ia_busca import montar_consulta

    assert montar_consulta(["tela azul", " reinicia ", ""]) == "tela azul OR reinicia"
    assert montar_consulta([]) == ""
    assert montar_consulta(["  ", ""]) == ""


async def test_buscar_semelhantes_sem_consulta_util_devolve_vazio_sem_sql():
    from app.repositories import ia_busca

    class _ConnProibida:
        async def fetch(self, *a):  # pragma: no cover — não deve ser chamada
            raise AssertionError("não pode tocar no banco sem termos")

    assert await ia_busca.buscar_semelhantes(
        _ConnProibida(), departamento_id="d1", chamado_id="c1", termos=["", "  "]
    ) == []


# ------------------------------------------------------------------
# Reconciliação (rede de segurança do agendamento em memória)
#
# Caso real que motivou este módulo: BOND-2026-00593 (2026-07-23) — chamado
# de TI elegível, ZERO linhas em `ia_triagens`/`historico_chamados`, enquanto
# o chamado anterior e o seguinte foram triados normalmente em segundos. A
# causa mais provável é um restart/redeploy do processo entre o
# `asyncio.create_task` do agendamento e o primeiro INSERT de auditoria —
# a tarefa em memória se perde sem deixar rastro.
# ------------------------------------------------------------------


class _FakeConnOrfaos:
    """Só implementa `.fetch` — é tudo que `_chamados_orfaos` usa."""

    def __init__(self, ids: list[str]):
        self._ids = ids
        self.args: tuple | None = None

    async def fetch(self, sql: str, *args):
        assert "ia_triagens" in sql and "chamados" in sql
        self.args = args
        return [{"id": i} for i in self._ids]


def _fake_admin_orfaos(conn: _FakeConnOrfaos):
    @asynccontextmanager
    async def _fake_admin():
        yield conn

    return _fake_admin


async def test_reconciliacao_kill_switch_nao_toca_no_banco():
    admin = MagicMock(side_effect=AssertionError("não pode tocar no banco"))
    with (
        patch.object(triagem, "get_settings", return_value=_settings(ia_triagem_ativa=False)),
        patch.object(triagem, "admin_connection", admin),
    ):
        assert await triagem.reconciliar_triagens_perdidas() == 0
    admin.assert_not_called()


async def test_reconciliacao_sem_api_key_tambem_desliga():
    admin = MagicMock(side_effect=AssertionError("não pode tocar no banco"))
    with (
        patch.object(triagem, "get_settings", return_value=_settings(ia_triagem_api_key="")),
        patch.object(triagem, "admin_connection", admin),
    ):
        assert await triagem.reconciliar_triagens_perdidas() == 0
    admin.assert_not_called()


async def test_reconciliacao_sem_departamentos_nao_roda_query():
    conn = _FakeConnOrfaos([])
    with (
        patch.object(
            triagem, "get_settings", return_value=_settings(ia_triagem_departamentos="")
        ),
        patch.object(triagem, "admin_connection", _fake_admin_orfaos(conn)),
    ):
        assert await triagem.reconciliar_triagens_perdidas() == 0
    assert conn.args is None  # retorno cedo: nem chega a consultar


async def test_reconciliacao_reexecuta_cada_orfao():
    conn = _FakeConnOrfaos(["cid-1", "cid-2"])
    executar = AsyncMock()
    with (
        patch.object(triagem, "get_settings", return_value=_settings()),
        patch.object(triagem, "admin_connection", _fake_admin_orfaos(conn)),
        patch.object(triagem, "executar_triagem", executar),
    ):
        total = await triagem.reconciliar_triagens_perdidas()
    assert total == 2
    assert [c.args[0] for c in executar.await_args_list] == ["cid-1", "cid-2"]
    assert conn.args == (["TI"], 2)  # departamentos_lista + teto de rodadas


async def test_reconciliacao_sem_orfaos_nao_chama_executar_triagem():
    conn = _FakeConnOrfaos([])
    executar = AsyncMock()
    with (
        patch.object(triagem, "get_settings", return_value=_settings()),
        patch.object(triagem, "admin_connection", _fake_admin_orfaos(conn)),
        patch.object(triagem, "executar_triagem", executar),
    ):
        assert await triagem.reconciliar_triagens_perdidas() == 0
    executar.assert_not_awaited()


async def test_loop_reconciliacao_ignora_erro_e_continua():
    """O loop nunca morre por erro de uma volta (Regra #5 aplicada à varredura)."""
    chamada = AsyncMock(side_effect=[RuntimeError("banco fora do ar"), None])
    sleep = AsyncMock(side_effect=[None, asyncio.CancelledError()])
    with (
        patch.object(triagem, "reconciliar_triagens_perdidas", chamada),
        patch.object(triagem.asyncio, "sleep", sleep),
    ):
        with pytest.raises(asyncio.CancelledError):
            await triagem._loop_reconciliacao(1.0)
    assert chamada.await_count == 1


async def test_iniciar_reconciliacao_none_com_triagem_desligada():
    assert triagem.iniciar_reconciliacao(_settings(ia_triagem_ativa=False)) is None


async def test_iniciar_reconciliacao_none_com_intervalo_zero():
    settings = _settings(ia_triagem_reconciliacao_intervalo_s=0)
    assert triagem.iniciar_reconciliacao(settings) is None


async def test_iniciar_reconciliacao_cria_task_quando_ligada():
    settings = _settings(ia_triagem_reconciliacao_intervalo_s=999)
    task = triagem.iniciar_reconciliacao(settings)
    assert isinstance(task, asyncio.Task)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
