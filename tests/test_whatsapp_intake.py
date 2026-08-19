"""Intake de chamado via WhatsApp — fluxo do webhook até a criação do chamado.

Cobre os invariantes estruturais da feature: kill switch sem efeito colateral,
idempotência por ``wamid``, telefone não cadastrado nunca vira chamado,
o chamado é criado em nome do PERFIL RESOLVIDO (não de um perfil de sistema),
e destino alucinado pelo modelo nunca vira INSERT.
"""

import json
from contextlib import ExitStack, asynccontextmanager, contextmanager
from dataclasses import dataclass
from datetime import timedelta
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from app.config import Settings
from app.ia import whatsapp_intake
from app.ia.schemas import SaidaWhatsAppIntake
from app.main import app
from app.security.uploads import AnexoValidado, UploadInvalido
from app.services.portal import PortalService

_TELEFONE = "5551999998888"
_PERFIL = {
    "id": "perfil-uuid",
    "nome": "Fulano",
    "empresa_id": "empresa-uuid",
    "departamento_id": "dep-autor-uuid",
    "departamento_nome": "Compras",
}
# Setores ativos = domínio do campo `setor` (todos os departamentos ativos, não
# só os que recebem chamado — o setor aqui é o de quem PEDE).
_SETORES = ["Compras", "Produção", "TI"]
_CATALOGO = [
    {
        "id": "dep-ti-uuid",
        "nome": "TI",
        "categorias": [
            {
                "id": "cat-uuid",
                "nome": "Equipamentos",
                "subcategorias": [{"id": "sub-uuid", "nome": "Impressora"}],
            }
        ],
    }
]
_CATALOGO_MARKETING = [
    {
        "id": "dep-mkt-uuid",
        "nome": "Marketing",
        "categorias": [
            {
                "id": "cat-mkt-uuid",
                "nome": "Peça gráfica",
                "subcategorias": [{"id": "sub-mkt-uuid", "nome": "Banner"}],
            }
        ],
    }
]


def _settings(**overrides) -> Settings:
    base = dict(
        session_secret="segredo-real-de-teste-nao-default",
        csrf_secret="outro-segredo-real-de-teste-nao-default",
        whatsapp_intake_ativo=True,
        whatsapp_intake_departamentos="TI",
        ia_triagem_api_key="chave-de-teste",
        whatsapp_intake_max_rodadas=4,
        # Explícito para o teste não depender do `.env` do desenvolvedor:
        # `Settings()` também lê o arquivo, e com App Secret preenchido a rota
        # passa a exigir assinatura HMAC válida e devolve 403 nos POSTs abaixo.
        whatsapp_app_secret="",
    )
    base.update(overrides)
    return Settings(**base)


def _payload(wamid: str = "wamid.A", corpo: str = "a impressora parou") -> dict:
    return {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "1",
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "messages": [
                                {
                                    "id": wamid,
                                    "from": _TELEFONE,
                                    "type": "text",
                                    "text": {"body": corpo},
                                }
                            ]
                        },
                    }
                ],
            }
        ],
    }


def _payload_imagem(wamid: str = "wamid.IMG", midia_id: str = "midia-1", caption: str = "") -> dict:
    return {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "1",
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "messages": [
                                {
                                    "id": wamid,
                                    "from": _TELEFONE,
                                    "type": "image",
                                    "image": {"id": midia_id, "caption": caption},
                                }
                            ]
                        },
                    }
                ],
            }
        ],
    }


class FakeConn:
    """Conexão administrativa scriptada pelos SQLs que o intake usa."""

    def __init__(
        self,
        *,
        wamid_novo: bool = True,
        conversa_travavel: bool = True,
        auditoria_conflita: bool = False,
    ):
        self._wamid_novo = wamid_novo
        self._conversa_travavel = conversa_travavel
        self._auditoria_conflita = auditoria_conflita
        self.mensagens_acumuladas: list[dict] = []
        self.auditorias: list[tuple] = []
        self.executes: list[tuple[str, tuple]] = []
        self.rodada = 0
        # Chamado recente pra `_chamado_recente_para_anexo` devolver — `None`
        # (default) = sem chamado dentro da janela, mídia abre conversa nova.
        self.chamado_recente: dict | None = None

    async def fetchval(self, sql: str, *args):
        if "INSERT INTO whatsapp_mensagens_recebidas" in sql:
            return 10 if self._wamid_novo else None
        if "SELECT id::text FROM whatsapp_conversas" in sql:
            return None  # nenhuma conversa aberta ainda
        if "INSERT INTO whatsapp_conversas" in sql:
            return "conversa-uuid"
        if "INSERT INTO ia_whatsapp_intake" in sql:
            self.auditorias.append(args)
            return None if self._auditoria_conflita else 1
        raise AssertionError(f"fetchval inesperado: {sql}")

    async def fetchrow(self, sql: str, *args):
        if "UPDATE whatsapp_conversas" in sql and "PROCESSANDO" in sql:
            if not self._conversa_travavel:
                return None  # outra task já pegou (lock otimista)
            return {
                "id": "conversa-uuid",
                "perfil_id": _PERFIL["id"],
                "telefone": _TELEFONE,
                "rodada": self.rodada,
                "mensagens_acumuladas": json.dumps(self.mensagens_acumuladas),
            }
        if "FROM perfis p" in sql:
            return dict(_PERFIL)
        if "FROM whatsapp_conversas wc" in sql:
            return dict(self.chamado_recente) if self.chamado_recente else None
        raise AssertionError(f"fetchrow inesperado: {sql}")

    async def fetch(self, sql: str, *args):
        raise AssertionError(f"fetch inesperado: {sql}")

    async def execute(self, sql: str, *args):
        self.executes.append((sql, args))
        if "mensagens_acumuladas = mensagens_acumuladas ||" not in sql:
            return
        # O UPDATE de recepção tem 2 args (id, json); o de finalização tem 5,
        # com o json no fim. Em ambos o payload acumulado é o último arg.
        bruto = args[-1] if args else "[]"
        self.mensagens_acumuladas.extend(json.loads(bruto))


@dataclass
class Ambiente:
    """Handles dos mocks que os testes inspecionam."""

    criar: AsyncMock
    responder: AsyncMock
    agendadas: list


@contextmanager
def ambiente(
    conn: FakeConn,
    settings: Settings,
    *,
    perfil: dict | None = _PERFIL,
    saida: SaidaWhatsAppIntake | None = None,
    respostas_modelo: list[tuple] | None = None,
    catalogo: list | None = None,
    setores: list | None = None,
    capturar_agendamentos: bool = False,
):
    """Patches comuns: banco, settings, catálogo, modelo e envio de WhatsApp."""

    @asynccontextmanager
    async def _fake_admin():
        yield conn

    criar = AsyncMock(return_value={"id": "chamado-uuid", "codigo": "BOND-2026-00999"})
    responder = AsyncMock()
    agendadas: list = []

    repo = AsyncMock()
    repo.criar = criar
    repo.operadores = AsyncMock(return_value=[])
    repo.adicionar_mensagem = AsyncMock()

    patches = [
        patch.object(whatsapp_intake, "admin_connection", _fake_admin),
        patch.object(whatsapp_intake, "get_settings", return_value=settings),
        patch.object(
            whatsapp_intake, "resolver_perfil_por_telefone", AsyncMock(return_value=perfil)
        ),
        # `processar_conversa` relê o perfil pelo id (a conversa pode ser
        # reprocessada muito depois — ex.: reconciliação após restart).
        patch.object(whatsapp_intake, "_perfil_por_id", AsyncMock(return_value=perfil)),
        patch.object(
            whatsapp_intake,
            "_montar_catalogo",
            AsyncMock(return_value=catalogo if catalogo is not None else _CATALOGO),
        ),
        patch.object(
            whatsapp_intake,
            "_setores_validos",
            AsyncMock(return_value=setores if setores is not None else _SETORES),
        ),
        patch.object(
            whatsapp_intake,
            "chamar_modelo_estruturado",
            AsyncMock(side_effect=respostas_modelo)
            if respostas_modelo is not None
            else AsyncMock(return_value=(saida, None, 100, 50)),
        ),
        patch.object(whatsapp_intake, "_responder", responder),
        patch.object(whatsapp_intake, "_imagem_da_conversa", AsyncMock(return_value=None)),
        patch.object(whatsapp_intake, "_anexar_midia_da_conversa", AsyncMock()),
        patch.object(whatsapp_intake, "_pos_criacao", AsyncMock()),
        patch("app.repositories.chamados.ChamadosRepo", return_value=repo),
    ]
    if capturar_agendamentos:
        patches.append(patch.object(whatsapp_intake, "_agendar", agendadas.append))

    with ExitStack() as stack:
        for p in patches:
            stack.enter_context(p)
        try:
            yield Ambiente(criar=criar, responder=responder, agendadas=agendadas)
        finally:
            # Corotinas capturadas nunca são aguardadas — fecha para não vazar.
            for corotina in agendadas:
                if hasattr(corotina, "close"):
                    corotina.close()


def _saida_completa(**overrides) -> SaidaWhatsAppIntake:
    base = dict(
        informacoes_suficientes=True,
        confianca="ALTA",
        titulo="Impressora não imprime",
        descricao="A impressora do setor parou de imprimir.",
        # Setor DITO NA CONVERSA — de propósito diferente do `departamento_nome`
        # do perfil ("Compras"), para os testes provarem qual dos dois vence.
        setor="Produção",
        departamento="TI",
        categoria="Equipamentos",
        subcategoria="Impressora",
        prioridade="ALTA",
    )
    base.update(overrides)
    return SaidaWhatsAppIntake(**base)


# --- Webhook ---------------------------------------------------------------


def test_kill_switch_desligado_nao_toca_no_intake():
    """Default de produção: o webhook segue só logando, como antes da feature."""
    settings = _settings(whatsapp_intake_ativo=False)
    processar = AsyncMock()
    with (
        patch("app.routes.whatsapp.get_settings", return_value=settings),
        patch.object(whatsapp_intake, "processar_mensagens_whatsapp", processar),
        TestClient(app) as client,
    ):
        resp = client.post("/api/webhooks/whatsapp", json=_payload())
    assert resp.status_code == 200
    processar.assert_not_awaited()


def test_webhook_ligado_delega_para_o_intake():
    settings = _settings()
    processar = AsyncMock()
    with (
        patch("app.routes.whatsapp.get_settings", return_value=settings),
        patch("app.ia.whatsapp_intake.processar_mensagens_whatsapp", processar),
        TestClient(app) as client,
    ):
        resp = client.post("/api/webhooks/whatsapp", json=_payload())
    assert resp.status_code == 200
    processar.assert_awaited_once()


# --- Recepção de mensagem --------------------------------------------------


async def test_telefone_desconhecido_orienta_cadastro_e_nao_cria_conversa():
    conn = FakeConn()
    with ambiente(conn, _settings(), perfil=None, capturar_agendamentos=True) as amb:
        await whatsapp_intake.processar_mensagens_whatsapp(_payload())

        # Marcou a mensagem como SEM_PERFIL e não abriu conversa nenhuma.
        assert any("SEM_PERFIL" in sql for sql, _ in conn.executes)
        assert not any("INSERT INTO whatsapp_conversas" in sql for sql, _ in conn.executes)
        amb.criar.assert_not_awaited()
        # Uma corotina de resposta foi agendada (o texto de cadastro).
        assert len(amb.agendadas) == 1


async def test_wamid_repetido_nao_reprocessa():
    """A Meta reentrega webhooks (at-least-once); o UNIQUE absorve."""
    conn = FakeConn(wamid_novo=False)
    with ambiente(conn, _settings(), capturar_agendamentos=True) as amb:
        await whatsapp_intake.processar_mensagens_whatsapp(_payload())

        assert amb.agendadas == []
        assert conn.executes == []
        amb.criar.assert_not_awaited()


# --- Processamento da conversa ---------------------------------------------


async def test_fluxo_feliz_cria_chamado_em_nome_do_perfil_resolvido():
    conn = FakeConn()
    conn.mensagens_acumuladas = [{"papel": "usuario", "conteudo": "a impressora parou"}]
    with ambiente(conn, _settings(), saida=_saida_completa()) as amb:
        await whatsapp_intake.processar_conversa("conversa-uuid")

        amb.criar.assert_awaited_once()
        kwargs = amb.criar.await_args.kwargs
        # O dono do chamado é o usuário identificado pelo telefone — nunca um
        # perfil de sistema (o "Assistente IA" da triagem, por exemplo).
        assert kwargs["cliente_id"] == _PERFIL["id"]
        assert amb.criar.await_args.args[0] == {"sub": _PERFIL["id"]}  # claims sintéticas
        assert kwargs["departamento_id"] == "dep-ti-uuid"
        assert kwargs["categoria_id"] == "cat-uuid"
        assert kwargs["subcategoria_id"] == "sub-uuid"
        assert kwargs["origem_demanda"] == "WhatsApp"
        # `setor` = setor demandante, vindo da CONVERSA (decisão 2026-08-18),
        # não do `departamento_nome` do perfil, que aqui é "Compras".
        assert kwargs["setor"] == "Produção"
        # Prioridade é decidida pelo modelo, não mais fixa em MEDIA.
        assert kwargs["prioridade"] == "ALTA"
        assert kwargs["telefone_contato"] == _TELEFONE

        # Confirmação com o código do chamado e o link de acompanhamento.
        amb.responder.assert_awaited_once()
        resposta = amb.responder.await_args.args[1]
        assert "BOND-2026-00999" in resposta
        assert "/portal/chamados/chamado-uuid" in resposta
        # Auditoria gravada com a ação certa.
        assert conn.auditorias and conn.auditorias[0][2] == "CHAMADO_CRIADO"


async def test_departamento_alucinado_nunca_vira_insert():
    """Nome fora do catálogo real = alucinação; não pode virar FK inválida."""
    conn = FakeConn()
    saida = _saida_completa(departamento="Departamento Inexistente")
    with ambiente(conn, _settings(), saida=saida) as amb:
        await whatsapp_intake.processar_conversa("conversa-uuid")

        amb.criar.assert_not_awaited()
        assert conn.auditorias[0][2] == "ENCERRADO_SEM_CHAMADO"


async def test_categoria_alucinada_nunca_vira_insert():
    conn = FakeConn()
    saida = _saida_completa(categoria="Categoria Fantasma")
    with ambiente(conn, _settings(), saida=saida) as amb:
        await whatsapp_intake.processar_conversa("conversa-uuid")

        amb.criar.assert_not_awaited()
        assert conn.auditorias[0][2] == "ENCERRADO_SEM_CHAMADO"


async def test_assunto_fora_do_escopo_orienta_portal_em_vez_de_insistir():
    """Achado do teste real do setor Brigadistas (2026-08-19): quando o
    relato não se encaixa em nenhum destino do catálogo disponível, o bot
    orienta o Portal em vez de continuar perguntando sem chance de resolver."""
    conn = FakeConn()
    saida = SaidaWhatsAppIntake(
        informacoes_suficientes=False,
        confianca="ALTA",
        assunto_fora_do_escopo=True,
        setor="Brigadistas",
    )
    with ambiente(conn, _settings(), saida=saida) as amb:
        await whatsapp_intake.processar_conversa("conversa-uuid")

        amb.criar.assert_not_awaited()
        assert conn.auditorias[0][2] == "ENCERRADO_SEM_CHAMADO"
        resposta = amb.responder.await_args.args[1]
        assert "Portal" in resposta
        assert "/portal/chamados/novo" in resposta
        # Nome do(s) departamento(s) cobertos hoje é dinâmico (settings), não
        # fixo em "TI" no texto — evita a mensagem envelhecer com o rollout.
        assert "TI" in resposta
        assert "deu um problema" not in resposta.lower()


async def test_setor_do_perfil_e_a_rede_de_seguranca_quando_o_dito_nao_casa():
    """Setor fora da lista de ativos não vira valor livre: cai no cadastro."""
    conn = FakeConn()
    saida = _saida_completa(setor="Setor Que Não Existe")
    with ambiente(conn, _settings(), saida=saida) as amb:
        await whatsapp_intake.processar_conversa("conversa-uuid")

        amb.criar.assert_awaited_once()
        assert amb.criar.await_args.kwargs["setor"] == "Compras"  # do perfil


async def test_sem_setor_em_lugar_nenhum_nao_inventa_valor():
    """`setor` é obrigatório; sem casar na conversa NEM no perfil, orienta contato humano."""
    conn = FakeConn()
    perfil_sem_setor = dict(_PERFIL, departamento_id=None, departamento_nome=None)
    with ambiente(
        conn,
        _settings(),
        perfil=perfil_sem_setor,
        saida=_saida_completa(setor="Setor Que Não Existe"),
    ) as amb:
        await whatsapp_intake.processar_conversa("conversa-uuid")

        amb.criar.assert_not_awaited()
        assert "setor" in amb.responder.await_args.args[1].lower()


async def test_prioridade_ausente_degrada_para_media():
    """Modelo que omite prioridade não pode bloquear a abertura do chamado."""
    conn = FakeConn()
    with ambiente(conn, _settings(), saida=_saida_completa(prioridade=None)) as amb:
        await whatsapp_intake.processar_conversa("conversa-uuid")

        assert amb.criar.await_args.kwargs["prioridade"] == "MEDIA"


# --- Fluxo por demanda do Marketing -----------------------------------------


def _saida_marketing(**overrides) -> SaidaWhatsAppIntake:
    base = dict(
        informacoes_suficientes=True,
        confianca="ALTA",
        titulo="Banner para campanha",
        descricao="Precisa de um banner novo para a campanha de setembro.",
        setor="Produção",
        departamento="Marketing",
        categoria="Peça gráfica",
        subcategoria="Banner",
        prioridade="URGENTE",  # o Marketing ignora isso — só prova que é sobrescrito
    )
    base.update(overrides)
    return SaidaWhatsAppIntake(**base)


async def test_marketing_com_data_valida_cria_chamado_com_prazo_e_prioridade_media():
    conn = FakeConn()
    data_valida = (PortalService.data_entrega_min() + timedelta(days=7)).isoformat()
    saida = _saida_marketing(data_entrega=data_valida)
    with ambiente(
        conn, _settings(whatsapp_intake_departamentos="Marketing"),
        saida=saida, catalogo=_CATALOGO_MARKETING,
    ) as amb:
        await whatsapp_intake.processar_conversa("conversa-uuid")

        amb.criar.assert_awaited_once()
        kwargs = amb.criar.await_args.kwargs
        assert kwargs["departamento_id"] == "dep-mkt-uuid"
        assert kwargs["data_entrega"].isoformat() == data_valida
        assert kwargs["sem_prazo"] is False
        # Marketing não usa impacto × urgência do modelo — prioridade fixa em MEDIA.
        assert kwargs["prioridade"] == "MEDIA"
        assert conn.auditorias[0][2] == "CHAMADO_CRIADO"


async def test_marketing_sem_prazo_marca_prioridade_baixa():
    conn = FakeConn()
    saida = _saida_marketing(sem_prazo=True, data_entrega=None)
    with ambiente(
        conn, _settings(whatsapp_intake_departamentos="Marketing"),
        saida=saida, catalogo=_CATALOGO_MARKETING,
    ) as amb:
        await whatsapp_intake.processar_conversa("conversa-uuid")

        amb.criar.assert_awaited_once()
        kwargs = amb.criar.await_args.kwargs
        assert kwargs["data_entrega"] is None
        assert kwargs["sem_prazo"] is True
        assert kwargs["prioridade"] == "BAIXA"


async def test_marketing_sem_data_nem_sem_prazo_pergunta_de_novo_em_vez_de_criar():
    """Modelo disse `informacoes_suficientes: true` sem resolver o prazo do
    Marketing — o código não confia nisso e devolve a mesma pergunta que o
    formulário do Portal faria, sem criar o chamado."""
    conn = FakeConn()
    saida = _saida_marketing(data_entrega=None, sem_prazo=False)
    with ambiente(
        conn, _settings(whatsapp_intake_departamentos="Marketing"),
        saida=saida, catalogo=_CATALOGO_MARKETING,
    ) as amb:
        await whatsapp_intake.processar_conversa("conversa-uuid")

        amb.criar.assert_not_awaited()
        assert conn.auditorias[0][2] == "PERGUNTA"
        resposta = amb.responder.await_args.args[1]
        assert "data de entrega" in resposta.lower()


async def test_marketing_data_antes_do_minimo_pergunta_de_novo():
    conn = FakeConn()
    data_cedo_demais = PortalService.data_entrega_min().isoformat()
    # `data_entrega_min()` já é o mínimo aceito; um dia antes dele é cedo demais.
    from datetime import date as _date

    cedo = (_date.fromisoformat(data_cedo_demais) - timedelta(days=1)).isoformat()
    saida = _saida_marketing(data_entrega=cedo)
    with ambiente(
        conn, _settings(whatsapp_intake_departamentos="Marketing"),
        saida=saida, catalogo=_CATALOGO_MARKETING,
    ) as amb:
        await whatsapp_intake.processar_conversa("conversa-uuid")

        amb.criar.assert_not_awaited()
        assert conn.auditorias[0][2] == "PERGUNTA"
        assert "mínimo" in amb.responder.await_args.args[1].lower()


async def test_marketing_no_teto_de_rodadas_sem_prazo_resolvido_encerra():
    """Mesma trava de `decidir_acao_intake` para o resto da conversa: não fica
    perguntando a data para sempre além do teto de rodadas."""
    conn = FakeConn()
    conn.rodada = 3  # settings usa max_rodadas=4 → esta rodada já é a última
    saida = _saida_marketing(data_entrega=None, sem_prazo=False)
    with ambiente(
        conn, _settings(whatsapp_intake_departamentos="Marketing"),
        saida=saida, catalogo=_CATALOGO_MARKETING,
    ) as amb:
        await whatsapp_intake.processar_conversa("conversa-uuid")

        amb.criar.assert_not_awaited()
        assert conn.auditorias[0][2] == "ENCERRADO_SEM_CHAMADO"


async def test_informacao_insuficiente_pergunta_e_mantem_conversa_aberta():
    conn = FakeConn()
    saida = SaidaWhatsAppIntake(
        informacoes_suficientes=False,
        confianca="BAIXA",
        perguntas=["Qual equipamento apresentou o problema?"],
    )
    with ambiente(conn, _settings(), saida=saida) as amb:
        await whatsapp_intake.processar_conversa("conversa-uuid")

        amb.criar.assert_not_awaited()
        amb.responder.assert_awaited_once()
        assert "Qual equipamento" in amb.responder.await_args.args[1]


async def test_tres_perguntas_viram_uma_mensagem_numerada():
    """O roteiro de investigação da triagem chega ao usuário numa mensagem só."""
    conn = FakeConn()
    saida = SaidaWhatsAppIntake(
        informacoes_suficientes=False,
        confianca="BAIXA",
        perguntas=["Qual equipamento?", "Desde quando?", "O que já tentou?"],
    )
    with ambiente(conn, _settings(), saida=saida) as amb:
        await whatsapp_intake.processar_conversa("conversa-uuid")

        amb.criar.assert_not_awaited()
        amb.responder.assert_awaited_once()
        resposta = amb.responder.await_args.args[1]
        assert "1. Qual equipamento?" in resposta
        assert "3. O que já tentou?" in resposta
        assert conn.auditorias[0][2] == "PERGUNTA"
        # Conversa volta a COLETANDO para aceitar a resposta do usuário.
        assert any(
            "COLETANDO" in str(args)
            for sql, args in conn.executes
            if "UPDATE whatsapp_conversas" in sql
        )


async def test_resposta_repetida_tenta_de_novo_e_usa_a_nova():
    """Achado em produção (2026-08-19): o modelo às vezes repete uma mensagem
    já enviada nesta conversa mesmo com o estado injetado como fato. O código
    detecta e tenta de novo antes de mandar ao usuário."""
    conn = FakeConn()
    conn.mensagens_acumuladas = [
        {"papel": "usuario", "conteudo": "Opa"},
        {"papel": "assistente", "conteudo": "De qual setor você é?"},
        {"papel": "usuario", "conteudo": "TI"},
    ]
    conn.rodada = 2
    repetida = SaidaWhatsAppIntake(
        informacoes_suficientes=False, confianca="BAIXA",
        perguntas=["De qual setor você é?"],  # idêntica à já enviada
    )
    nova = SaidaWhatsAppIntake(
        informacoes_suficientes=False, confianca="ALTA", setor="TI",
        perguntas=["Show, e o que está acontecendo?"],
    )
    with ambiente(
        conn, _settings(), respostas_modelo=[(repetida, None, 100, 50), (nova, None, 80, 40)]
    ) as amb:
        await whatsapp_intake.processar_conversa("conversa-uuid")

        assert whatsapp_intake.chamar_modelo_estruturado.await_count == 2
        amb.responder.assert_awaited_once()
        resposta = amb.responder.await_args.args[1]
        assert resposta == "Show, e o que está acontecendo?"
        assert "De qual setor você é?" not in resposta
        # Tokens das duas tentativas somados no registro de auditoria.
        assert conn.auditorias[0][5] == 180  # tokens_entrada
        assert conn.auditorias[0][6] == 90  # tokens_saida
        resultado = json.loads(conn.auditorias[0][3])
        assert resultado["retry_anti_repeticao"] is True
        assert resultado["setor"] == "TI"


async def test_resposta_repetida_duas_vezes_usa_texto_generico():
    """Se a tentativa nova TAMBÉM repetir, nunca manda a mensagem duplicada —
    cai num texto fixo, mas preserva o `setor` já extraído pela 1ª tentativa."""
    conn = FakeConn()
    conn.mensagens_acumuladas = [
        {"papel": "usuario", "conteudo": "Opa"},
        {"papel": "assistente", "conteudo": "De qual setor você é?"},
        {"papel": "usuario", "conteudo": "TI"},
    ]
    conn.rodada = 2
    repetida = SaidaWhatsAppIntake(
        informacoes_suficientes=False, confianca="BAIXA", setor="TI",
        perguntas=["De qual setor você é?"],
    )
    with ambiente(
        conn, _settings(), respostas_modelo=[(repetida, None, 100, 50), (repetida, None, 90, 45)]
    ) as amb:
        await whatsapp_intake.processar_conversa("conversa-uuid")

        assert whatsapp_intake.chamar_modelo_estruturado.await_count == 2
        resposta = amb.responder.await_args.args[1]
        assert resposta == whatsapp_intake._TEXTO_CONTINUAR_GENERICO
        resultado = json.loads(conn.auditorias[0][3])
        assert resultado["setor"] == "TI"  # dado real preservado apesar do texto genérico


async def test_lock_otimista_impede_processamento_concorrente():
    conn = FakeConn(conversa_travavel=False)
    with ambiente(conn, _settings(), saida=_saida_completa()) as amb:
        await whatsapp_intake.processar_conversa("conversa-uuid")

        amb.criar.assert_not_awaited()
        amb.responder.assert_not_awaited()


async def test_rodada_ja_auditada_encerra_em_vez_de_repetir_para_sempre():
    """Execução anterior morreu entre o INSERT da auditoria e o UPDATE da
    conversa: sem encerrar, a reconciliação reprocessaria esta mesma rodada a
    cada varredura (o número da rodada nunca avançaria)."""
    conn = FakeConn(auditoria_conflita=True)
    with ambiente(conn, _settings(), saida=_saida_completa()) as amb:
        await whatsapp_intake.processar_conversa("conversa-uuid")

        # Não responde de novo (o usuário já foi respondido na execução que venceu).
        amb.responder.assert_not_awaited()
        # E a conversa vira terminal, liberando o índice de conversa aberta.
        assert any(
            "FALHOU" in sql for sql, _ in conn.executes if "UPDATE whatsapp_conversas" in sql
        )


async def test_kill_switch_desligado_nao_processa_conversa():
    conn = FakeConn()
    with ambiente(conn, _settings(whatsapp_intake_ativo=False), saida=_saida_completa()) as amb:
        await whatsapp_intake.processar_conversa("conversa-uuid")

        amb.criar.assert_not_awaited()
        amb.responder.assert_not_awaited()


async def test_sem_departamento_habilitado_kill_switch_efetivo():
    """CSV vazio = feature desligada, mesmo com a flag geral ligada."""
    settings = _settings(whatsapp_intake_departamentos="")
    assert whatsapp_intake.intake_ativo(settings) is False


async def test_sem_chave_de_ia_kill_switch_efetivo():
    settings = _settings(ia_triagem_api_key="")
    assert whatsapp_intake.intake_ativo(settings) is False


# --- Anexo pós-criação (foto/documento depois do chamado aberto) -----------


_CHAMADO_RECENTE = {"chamado_id": "chamado-uuid", "codigo": "BOND-2026-00999"}


async def test_midia_recente_apos_criacao_anexa_direto_sem_reabrir_intake():
    """Achado do pedido do gestor (2026-08-19): quem esqueceu de mandar a
    foto/documento no chamado consegue mandar depois, sem reabrir o roteiro
    do intake do zero — desde que dentro da janela pós-criação."""
    conn = FakeConn()
    conn.chamado_recente = _CHAMADO_RECENTE
    with (
        ambiente(conn, _settings(), capturar_agendamentos=True) as amb,
        patch.object(whatsapp_intake, "_anexar_midia_pos_criacao", AsyncMock()) as anexar_pos,
    ):
        await whatsapp_intake.processar_mensagens_whatsapp(
            _payload_imagem(midia_id="midia-1", caption="segue a foto")
        )

        anexar_pos.assert_called_once()
        alvo, perfil, msg, telefone, settings_arg = anexar_pos.call_args.args
        assert alvo == _CHAMADO_RECENTE
        assert perfil == _PERFIL
        assert msg["midia_id"] == "midia-1"
        assert telefone == _TELEFONE
        assert isinstance(settings_arg, Settings)

    # Nenhuma conversa nova foi criada nem o roteiro de perguntas rodou.
    assert not any("INSERT INTO whatsapp_conversas" in sql for sql, _ in conn.executes)
    amb.responder.assert_not_awaited()


async def test_midia_com_legenda_de_chamado_novo_nao_anexa_no_anterior():
    """Achado do gestor (2026-08-19): sem esse bypass, quem manda foto com
    legenda "novo chamado: ..." pra um problema DIFERENTE do que acabou de
    abrir teria a mensagem inteira engolida como comentário do chamado
    anterior. A legenda explícita pula o atalho e abre conversa nova."""
    conn = FakeConn()
    conn.chamado_recente = _CHAMADO_RECENTE
    with (
        ambiente(conn, _settings(), saida=_saida_completa(), capturar_agendamentos=True) as amb,
        patch.object(whatsapp_intake, "_anexar_midia_pos_criacao", AsyncMock()) as anexar_pos,
    ):
        await whatsapp_intake.processar_mensagens_whatsapp(
            _payload_imagem(caption="novo chamado: o ar-condicionado da sala 3 tá vazando")
        )

        anexar_pos.assert_not_called()
        assert len(amb.agendadas) == 1  # `processar_conversa` da conversa nova


async def test_midia_sem_chamado_recente_abre_conversa_normal():
    """Sem chamado dentro da janela (`chamado_recente=None`, default do
    fixture): a foto entra no fluxo de sempre, abrindo conversa nova (agenda
    `processar_conversa`, nunca o atalho de anexo pós-criação)."""
    conn = FakeConn()
    with (
        ambiente(conn, _settings(), saida=_saida_completa(), capturar_agendamentos=True) as amb,
        patch.object(whatsapp_intake, "_anexar_midia_pos_criacao", AsyncMock()) as anexar_pos,
    ):
        await whatsapp_intake.processar_mensagens_whatsapp(_payload_imagem())

        anexar_pos.assert_not_called()
        assert len(amb.agendadas) == 1  # `processar_conversa` da conversa nova


async def test_midia_janela_desligada_nunca_anexa_direto():
    """`WHATSAPP_INTAKE_ANEXO_JANELA_S=0` desliga o recurso — mesmo com um
    chamado recente no banco, a mídia sempre abre conversa nova."""
    conn = FakeConn()
    conn.chamado_recente = _CHAMADO_RECENTE
    with (
        ambiente(
            conn, _settings(whatsapp_intake_anexo_janela_s=0),
            saida=_saida_completa(), capturar_agendamentos=True,
        ) as amb,
        patch.object(whatsapp_intake, "_anexar_midia_pos_criacao", AsyncMock()) as anexar_pos,
    ):
        await whatsapp_intake.processar_mensagens_whatsapp(_payload_imagem())

        anexar_pos.assert_not_called()
        assert len(amb.agendadas) == 1


_ANEXO_VALIDO = AnexoValidado(
    nome_original="foto.jpg",
    nome_objeto="uuid123.jpg",
    ext="jpg",
    mime="image/jpeg",
    tamanho=100,
    conteudo=b"fake",
)


async def test_anexo_pos_criacao_sucesso_confirma_com_codigo():
    msg = {"midia_id": "midia-1", "midia_nome": None, "tipo": "image", "corpo": ""}
    responder = AsyncMock()
    with (
        patch.object(whatsapp_intake, "_midia_valida", AsyncMock(return_value=_ANEXO_VALIDO)),
        patch.object(whatsapp_intake, "_subir_anexo_chamado", AsyncMock(return_value=True)),
        patch.object(whatsapp_intake, "_responder", responder),
    ):
        await whatsapp_intake._anexar_midia_pos_criacao(
            _CHAMADO_RECENTE, _PERFIL, msg, _TELEFONE, _settings()
        )

    responder.assert_awaited_once()
    resposta = responder.await_args.args[1]
    assert "BOND-2026-00999" in resposta


async def test_anexo_pos_criacao_tipo_nao_permitido_explica_o_motivo():
    """`UploadInvalido` já traz mensagem pronta em PT-BR (mesma validação do
    upload do Portal) — o intake só repassa, sem inventar outro texto."""
    msg = {"midia_id": "midia-1", "midia_nome": "virus.exe", "tipo": "document", "corpo": ""}
    responder = AsyncMock()
    motivo = "Tipo de arquivo não permitido. Aceitos: pdf, jpg, png, mp4, docx, xlsx, pptx."
    with (
        patch.object(whatsapp_intake, "_midia_valida", AsyncMock(side_effect=UploadInvalido(motivo))),
        patch.object(whatsapp_intake, "_responder", responder),
    ):
        await whatsapp_intake._anexar_midia_pos_criacao(
            _CHAMADO_RECENTE, _PERFIL, msg, _TELEFONE, _settings()
        )

    resposta = responder.await_args.args[1]
    assert motivo == resposta


async def test_anexo_pos_criacao_download_falhou_pede_pra_tentar_de_novo():
    msg = {"midia_id": "midia-1", "midia_nome": None, "tipo": "image", "corpo": ""}
    responder = AsyncMock()
    with (
        patch.object(whatsapp_intake, "_midia_valida", AsyncMock(return_value=None)),
        patch.object(whatsapp_intake, "_responder", responder),
    ):
        await whatsapp_intake._anexar_midia_pos_criacao(
            _CHAMADO_RECENTE, _PERFIL, msg, _TELEFONE, _settings()
        )

    resposta = responder.await_args.args[1]
    assert "tentar" in resposta.lower()


async def test_anexo_pos_criacao_storage_indisponivel_nao_lanca():
    msg = {"midia_id": "midia-1", "midia_nome": None, "tipo": "image", "corpo": ""}
    responder = AsyncMock()
    with (
        patch.object(whatsapp_intake, "_midia_valida", AsyncMock(return_value=_ANEXO_VALIDO)),
        patch.object(whatsapp_intake, "_subir_anexo_chamado", AsyncMock(return_value=False)),
        patch.object(whatsapp_intake, "_responder", responder),
    ):
        await whatsapp_intake._anexar_midia_pos_criacao(
            _CHAMADO_RECENTE, _PERFIL, msg, _TELEFONE, _settings()
        )

    resposta = responder.await_args.args[1]
    assert "BOND-2026-00999" not in resposta
