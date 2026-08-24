"""Intake de chamado via WhatsApp — fluxo do webhook até a criação do chamado.

Cobre os invariantes estruturais da feature: kill switch sem efeito colateral,
idempotência por ``wamid``, telefone não cadastrado nunca vira chamado,
o chamado é criado em nome do PERFIL RESOLVIDO (não de um perfil de sistema),
e destino alucinado pelo modelo nunca vira INSERT.
"""

import json
from contextlib import ExitStack, asynccontextmanager, contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.domain.formularios_quimico import (
    CAT_ANALISE,
    CAT_DESENVOLVIMENTO,
    CAT_OCORRENCIA,
    CAT_VISITA,
    campos_da_categoria,
)
from app.domain.periodo import TZ_BR
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
        # Campos confirmados em rodada anterior pra `_campos_confirmados`
        # devolver — `None` (default) = nada extraído ainda.
        self.resultado_confirmado: dict | None = None
        self.consultas_campos_confirmados = 0

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
        if "SELECT resultado FROM ia_whatsapp_intake" in sql:
            self.consultas_campos_confirmados += 1
            if self.resultado_confirmado is None:
                return None
            return {"resultado": json.dumps(self.resultado_confirmado, ensure_ascii=False)}
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
    responder_documento: AsyncMock
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
    responder_documento = AsyncMock()
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
        patch.object(whatsapp_intake, "_responder_documento", responder_documento),
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
            yield Ambiente(
                criar=criar, responder=responder,
                responder_documento=responder_documento, agendadas=agendadas,
            )
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


# --- Formulário obrigatório do RH -------------------------------------------
#
# Mesma regra endurecida no Portal em 2026-08-10 (`app/routes/portal.py`):
# certas subcategorias do RH exigem o FB anexado já na ABERTURA — "avisar e
# deixar concluir depois" reabriu um bug real uma vez. O WhatsApp reaproveita
# `app/domain/formularios_rh.py::formulario_da_subcategoria` para não virar
# uma porta lateral que reabre o mesmo gap.


_CATALOGO_RH = [
    {
        "id": "dep-rh-uuid",
        "nome": "RH",
        "categorias": [
            {
                "id": "cat-rh-uuid",
                "nome": "Movimentação de pessoal",
                "subcategorias": [
                    {"id": "sub-rh-formulario-uuid", "nome": "Aumento de Quadro"},
                    {"id": "sub-rh-livre-uuid", "nome": "Dúvida geral"},
                ],
            }
        ],
    }
]


def _saida_rh(**overrides) -> SaidaWhatsAppIntake:
    base = dict(
        informacoes_suficientes=True,
        confianca="ALTA",
        titulo="Contratação para o time comercial",
        descricao="Precisa abrir vaga nova para reforçar o time comercial.",
        setor="Produção",
        departamento="RH",
        categoria="Movimentação de pessoal",
        subcategoria="Aumento de Quadro",
        prioridade="MEDIA",
    )
    base.update(overrides)
    return SaidaWhatsAppIntake(**base)


async def test_rh_subcategoria_com_formulario_sem_anexo_pergunta_em_vez_de_criar():
    """Sem anexo, o bot manda o modelo em branco como DOCUMENTO de verdade no
    WhatsApp (não só um link em texto) e explica o que fazer em seguida."""
    conn = FakeConn()
    with ambiente(
        conn, _settings(whatsapp_intake_departamentos="RH"),
        saida=_saida_rh(), catalogo=_CATALOGO_RH,
    ) as amb:
        await whatsapp_intake.processar_conversa("conversa-uuid")

        amb.criar.assert_not_awaited()
        assert conn.auditorias[0][2] == "PERGUNTA"
        # O modelo em branco foi mandado como documento (link do estático).
        amb.responder_documento.assert_awaited_once()
        link, kwargs = amb.responder_documento.await_args.args[1], amb.responder_documento.await_args.kwargs
        assert link.endswith("/static/formularios/rh/fb031-solicitacao-contratacao.docx")
        assert "fb031" in kwargs["nome_arquivo"].lower()
        # E o texto explicativo saiu depois, sem repetir o link (o documento
        # já chegou pronto pra abrir).
        resposta = amb.responder.await_args.args[1]
        assert "formulário" in resposta.lower()
        assert "http" not in resposta.lower()


async def test_rh_subcategoria_com_formulario_e_anexo_valido_cria_chamado():
    conn = FakeConn()
    conn.mensagens_acumuladas = [
        {"papel": "usuario", "conteudo": "", "midia_id": "midia-1", "midia_nome": None}
    ]
    with (
        ambiente(
            conn, _settings(whatsapp_intake_departamentos="RH"),
            saida=_saida_rh(), catalogo=_CATALOGO_RH,
        ) as amb,
        patch.object(whatsapp_intake, "_midia_valida", AsyncMock(return_value=_ANEXO_VALIDO)),
    ):
        await whatsapp_intake.processar_conversa("conversa-uuid")

        amb.criar.assert_awaited_once()
        assert conn.auditorias[0][2] == "CHAMADO_CRIADO"


async def test_rh_anexo_antigo_sem_relacao_nao_satisfaz_a_exigencia():
    """Achado real em produção (2026-08-20, chamado BOND-2026-00780): um anexo
    mandado no COMEÇO de uma conversa longa (antes de a pessoa dizer o que
    precisava, sem nenhuma relação com o formulário) satisfazia a exigência
    porque a checagem antiga procurava mídia em qualquer ponto do histórico.
    Só a ÚLTIMA mensagem pode contar — havendo mensagens de texto depois do
    anexo antigo, a exigência continua pendente."""
    conn = FakeConn()
    conn.mensagens_acumuladas = [
        {"papel": "usuario", "conteudo": "", "midia_id": "midia-antiga", "midia_nome": "outra-coisa.docx"},
        {"papel": "assistente", "conteudo": "Bom dia! ..."},
        {"papel": "usuario", "conteudo": "Preciso de alteração de função", "midia_id": None},
    ]
    with (
        ambiente(
            conn, _settings(whatsapp_intake_departamentos="RH"),
            saida=_saida_rh(), catalogo=_CATALOGO_RH,
        ) as amb,
        patch.object(whatsapp_intake, "_midia_valida", AsyncMock(return_value=_ANEXO_VALIDO)),
    ):
        await whatsapp_intake.processar_conversa("conversa-uuid")

        amb.criar.assert_not_awaited()
        assert conn.auditorias[0][2] == "PERGUNTA"
        amb.responder_documento.assert_awaited_once()


async def test_rh_subcategoria_com_formulario_e_anexo_invalido_pergunta_de_novo():
    """Arquivo enviado mas recusado pela validação (tipo/tamanho) conta como
    "sem anexo" — não deixa passar algo que o Portal também recusaria."""
    conn = FakeConn()
    conn.mensagens_acumuladas = [
        {"papel": "usuario", "conteudo": "", "midia_id": "midia-1", "midia_nome": "virus.exe"}
    ]
    with (
        ambiente(
            conn, _settings(whatsapp_intake_departamentos="RH"),
            saida=_saida_rh(), catalogo=_CATALOGO_RH,
        ) as amb,
        patch.object(
            whatsapp_intake, "_midia_valida",
            AsyncMock(side_effect=UploadInvalido("Tipo não permitido.")),
        ),
    ):
        await whatsapp_intake.processar_conversa("conversa-uuid")

        amb.criar.assert_not_awaited()
        assert conn.auditorias[0][2] == "PERGUNTA"


async def test_rh_subcategoria_sem_formulario_nao_exige_anexo():
    conn = FakeConn()
    with ambiente(
        conn, _settings(whatsapp_intake_departamentos="RH"),
        saida=_saida_rh(subcategoria="Dúvida geral"), catalogo=_CATALOGO_RH,
    ) as amb:
        await whatsapp_intake.processar_conversa("conversa-uuid")

        amb.criar.assert_awaited_once()
        assert conn.auditorias[0][2] == "CHAMADO_CRIADO"


async def test_rh_no_teto_de_rodadas_sem_formulario_encerra():
    conn = FakeConn()
    conn.rodada = 3  # settings usa max_rodadas=4 → esta rodada já é a última
    with ambiente(
        conn, _settings(whatsapp_intake_departamentos="RH"),
        saida=_saida_rh(), catalogo=_CATALOGO_RH,
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


# --- Campos confirmados não se perdem entre rodadas -------------------------
#
# Achado em produção (2026-08-20, chamado real de Marketing): `setor` saiu
# certo em `saida.setor` desde a rodada 2 (auditoria confirma), mas o modelo
# mesmo assim reformulou "de qual setor você é?" nas rodadas 2, 3 E 4 — cada
# reformulação com texto diferente escapava da rede de segurança de
# repetição exata, queimando rodadas do teto à toa até a conversa morrer em
# ENCERRADO_SEM_CHAMADO. Estes testes cobrem a correção: o setor (e os
# outros campos "sticky") já confirmado numa rodada anterior é lido de volta
# da auditoria e nunca se perde, mesmo que o modelo omita no JSON desta vez.


async def test_primeira_rodada_nunca_consulta_campos_confirmados():
    """Round 1 não tem nada extraído ainda — nem vale a pena consultar."""
    conn = FakeConn()  # rodada=0 → computa rodada=1
    with ambiente(conn, _settings(), saida=_saida_completa()) as amb:
        await whatsapp_intake.processar_conversa("conversa-uuid")

    assert conn.consultas_campos_confirmados == 0
    amb.criar.assert_awaited_once()


async def test_setor_confirmado_em_rodada_anterior_nao_se_perde_mesmo_se_modelo_esquecer():
    """O modelo (mockado) volta SEM `setor` nesta rodada — simula exatamente
    o achado de produção. Com o setor já confirmado na rodada anterior, o
    chamado ainda assim é criado com o setor certo, sem pedir de novo."""
    conn = FakeConn()
    conn.rodada = 1  # próxima rodada processada será a 2
    conn.resultado_confirmado = {"setor": "TI"}
    saida_sem_setor = _saida_completa(setor=None)
    with ambiente(conn, _settings(), saida=saida_sem_setor) as amb:
        await whatsapp_intake.processar_conversa("conversa-uuid")

        assert conn.consultas_campos_confirmados == 1
        amb.criar.assert_awaited_once()
        assert amb.criar.await_args.kwargs["setor"] == "TI"


async def test_campo_confirmado_nao_sobrescreve_valor_novo_do_modelo():
    """Se a pessoa CORRIGIU o setor nesta rodada, o valor novo do modelo
    vence — o campo confirmado só reaparece quando o modelo omite."""
    conn = FakeConn()
    conn.rodada = 1
    conn.resultado_confirmado = {"setor": "Compras"}
    saida_corrigida = _saida_completa(setor="Produção")
    with ambiente(conn, _settings(), saida=saida_corrigida) as amb:
        await whatsapp_intake.processar_conversa("conversa-uuid")

        amb.criar.assert_awaited_once()
        assert amb.criar.await_args.kwargs["setor"] == "Produção"


async def test_pedido_novo_reseta_destino_mas_mantem_setor():
    """Achado real em produção (2026-08-21): depois do destino (departamento/
    categoria/campos_formulario do Químico) confirmado, a pessoa dizer que
    quer um chamado NOVO não pode deixar esses campos grudados pra sempre —
    o bot ficava preso perguntando um campo (região) de um formulário que a
    pessoa já tinha abandonado. Só `setor` (quem ela é) continua valendo."""
    conn = FakeConn()
    conn.rodada = 1
    conn.resultado_confirmado = {
        "setor": "TI",
        "departamento": "Dpto Químico",
        "categoria": CAT_OCORRENCIA,
        "campos_formulario": {"regiao": "007-GRAVATAI"},
    }
    conn.mensagens_acumuladas = [
        {"papel": "usuario", "conteudo": "sou do TI, quero um relatório de ocorrência pro Químico"},
        {"papel": "assistente", "conteudo": "Qual é a região?"},
        {"papel": "usuario", "conteudo": "Quero um novo chamado, esquece esse"},
    ]
    saida = SaidaWhatsAppIntake(informacoes_suficientes=False, perguntas=["O que você precisa agora?"])
    with ambiente(
        conn, _settings(whatsapp_intake_departamentos="Dpto Químico"),
        saida=saida, catalogo=_CATALOGO_QUIMICO,
    ) as amb:
        await whatsapp_intake.processar_conversa("conversa-uuid")

        amb.criar.assert_not_awaited()
        resultado = json.loads(conn.auditorias[0][3])
        assert resultado.get("setor") == "TI"  # preservado
        assert not resultado.get("departamento")  # não reposto pela rede de segurança
        assert not resultado.get("categoria")
        assert not resultado.get("campos_formulario")


@pytest.mark.parametrize(
    "texto",
    [
        "cancela esse chamado",
        "Cancela, por favor",
        "escolhi errado a opção",
        "acho que escolhi a opção errada",
        "foi por engano, desculpa",
        "não era isso que eu queria",
        "nao era isso que eu queria",
        "não é isso, me confundi",
        "quero voltar do começo",
        "quero voltar do comeco",
        "vamos recomeçar",
        "quero recomecar do zero",
    ],
)
def test_pede_novo_chamado_reconhece_cancelamento_e_arrependimento(texto):
    """Pedido do usuário (2026-08-21): a detecção de reinício só cobria
    variações de "novo chamado" — alguém dizendo só "cancela" ou "não era
    isso que eu queria" não disparava o reset, deixando categoria/formulário
    do pedido abandonado grudados na conversa."""
    assert whatsapp_intake._pede_novo_chamado(texto) is True


def test_pede_novo_chamado_nao_reconhece_texto_sem_sinal_de_reinicio():
    assert whatsapp_intake._pede_novo_chamado("o computador não liga mais") is False


async def test_pedido_novo_reseta_destino_com_frase_de_cancelamento():
    """Mesmo cenário de :func:`test_pedido_novo_reseta_destino_mas_mantem_setor`,
    mas com uma frase de cancelamento em vez de "novo chamado" — prova que o
    reset não depende só daquela expressão específica."""
    conn = FakeConn()
    conn.rodada = 1
    conn.resultado_confirmado = {
        "setor": "TI",
        "departamento": "Dpto Químico",
        "categoria": CAT_OCORRENCIA,
        "campos_formulario": {"regiao": "007-GRAVATAI"},
    }
    conn.mensagens_acumuladas = [
        {"papel": "usuario", "conteudo": "sou do TI, quero um relatório de ocorrência pro Químico"},
        {"papel": "assistente", "conteudo": "Qual é a região?"},
        {"papel": "usuario", "conteudo": "Cancela, não era isso que eu queria"},
    ]
    saida = SaidaWhatsAppIntake(informacoes_suficientes=False, perguntas=["O que você precisa agora?"])
    with ambiente(
        conn, _settings(whatsapp_intake_departamentos="Dpto Químico"),
        saida=saida, catalogo=_CATALOGO_QUIMICO,
    ) as amb:
        await whatsapp_intake.processar_conversa("conversa-uuid")

        amb.criar.assert_not_awaited()
        resultado = json.loads(conn.auditorias[0][3])
        assert resultado.get("setor") == "TI"  # preservado
        assert not resultado.get("departamento")  # não reposto pela rede de segurança
        assert not resultado.get("categoria")
        assert not resultado.get("campos_formulario")


async def test_pedido_novo_esquece_a_narrativa_antiga_no_prompt():
    """Pedido do usuário (2026-08-21): zerar os campos "confirmados" não
    bastava — a transcrição INTEIRA da conversa abandonada (produto, região,
    gerente...) continuava sendo enviada ao modelo, que se perdia relendo a
    narrativa antiga e voltava a inferir a categoria/formulário de lá, às
    vezes reabrindo o pedido que a pessoa acabou de cancelar. Depois do
    reinício, o prompt enviado ao modelo não pode mais conter nada da
    conversa anterior à mensagem de reinício — só ela em diante — e
    `informacoes_suficientes`/apresentação não podem regredir por causa
    disso."""
    conn = FakeConn()
    conn.rodada = 1
    conn.resultado_confirmado = {
        "setor": "TI",
        "departamento": "Dpto Químico",
        "categoria": CAT_OCORRENCIA,
        "campos_formulario": {"regiao": "007-GRAVATAI"},
    }
    conn.mensagens_acumuladas = [
        {"papel": "usuario", "conteudo": "sou do TI, quero um relatório de ocorrência pro Químico"},
        {"papel": "assistente", "conteudo": "Qual é a região?"},
        {"papel": "usuario", "conteudo": "Panambi, o gerente é o Wallysson"},
        {"papel": "assistente", "conteudo": "Qual é o supervisor?"},
        {"papel": "usuario", "conteudo": "Cancela, não era isso que eu queria"},
    ]
    saida = SaidaWhatsAppIntake(informacoes_suficientes=False, perguntas=["O que você precisa agora?"])
    with ambiente(
        conn, _settings(whatsapp_intake_departamentos="Dpto Químico"),
        saida=saida, catalogo=_CATALOGO_QUIMICO,
    ) as amb:
        await whatsapp_intake.processar_conversa("conversa-uuid")

        amb.criar.assert_not_awaited()
        prompt_enviado = whatsapp_intake.chamar_modelo_estruturado.await_args_list[0].args[0]
        texto_prompt = json.dumps(prompt_enviado, ensure_ascii=False)
        assert "Panambi" not in texto_prompt
        assert "Wallysson" not in texto_prompt
        assert "Qual é o supervisor?" not in texto_prompt
        assert "Cancela, não era isso que eu queria" in texto_prompt
        # não regride pra "primeira mensagem" só por o histórico ter sido cortado:
        assert "primeira mensagem desta conversa" not in texto_prompt

        resultado = json.loads(conn.auditorias[0][3])
        assert resultado["historico_desde"] == 4  # índice da mensagem de reinício


async def test_pedido_novo_reseta_rodada_efetiva_nao_esbarra_no_teto():
    """Pedido do usuário (2026-08-21): reiniciar o pedido não pode fazer a
    conversa esbarrar no teto de rodadas por causa das rodadas do pedido
    ABANDONADO — o teto reconta a partir de 1 quando a pessoa reinicia."""
    conn = FakeConn()
    conn.rodada = 3  # settings usa max_rodadas=4 → sem o fix, a próxima rodada (4) já bateria no teto
    conn.resultado_confirmado = {
        "setor": "TI",
        "departamento": "Dpto Químico",
        "categoria": CAT_OCORRENCIA,
        "campos_formulario": {"regiao": "007-GRAVATAI"},
        "rodada_efetiva": 3,
    }
    conn.mensagens_acumuladas = [
        {"papel": "usuario", "conteudo": "sou do TI, quero um relatório de ocorrência pro Químico"},
        {"papel": "assistente", "conteudo": "Qual é a região?"},
        {"papel": "usuario", "conteudo": "Quero um novo chamado, esquece esse"},
    ]
    saida = SaidaWhatsAppIntake(informacoes_suficientes=False, perguntas=["O que você precisa agora?"])
    with ambiente(
        conn, _settings(whatsapp_intake_departamentos="Dpto Químico"),
        saida=saida, catalogo=_CATALOGO_QUIMICO,
    ) as amb:
        await whatsapp_intake.processar_conversa("conversa-uuid")

        amb.criar.assert_not_awaited()
        # Sem o fix, isso seria ENCERRADO_SEM_CHAMADO (rodada crua 4 >= teto 4).
        assert conn.auditorias[0][2] == "PERGUNTA"
        resultado = json.loads(conn.auditorias[0][3])
        assert resultado["rodada_efetiva"] == 1


# --- Formulário dinâmico do Departamento Químico ----------------------------
#
# Mesma regra própria do Portal (`app/domain/formularios_quimico.py`): cada
# categoria do Químico tem um layout FIXO de campos, coletado no WhatsApp
# campo a campo ao longo da conversa. Quem garante que nada obrigatório ficou
# de fora, e que valores de `select`/`checkbox_multi` batem com as opções
# reais, é `_validar_formulario_quimico` — reaproveitando
# `formularios_quimico.validar_payload`, a MESMA função do Portal.

_CATALOGO_QUIMICO = [
    {
        "id": "dep-quimico-uuid",
        "nome": "Dpto Químico",
        "categorias": [
            {"id": "cat-analise-uuid", "nome": CAT_ANALISE, "subcategorias": []},
            {"id": "cat-ocorrencia-uuid", "nome": CAT_OCORRENCIA, "subcategorias": []},
            {"id": "cat-visita-uuid", "nome": CAT_VISITA, "subcategorias": []},
            {"id": "cat-desenvolvimento-uuid", "nome": CAT_DESENVOLVIMENTO, "subcategorias": []},
            # Categoria do Químico SEM layout dinâmico conhecido — prova que o
            # departamento sozinho não força o fluxo de formulário fixo.
            {"id": "cat-duvida-uuid", "nome": "Dúvida Geral", "subcategorias": []},
        ],
    }
]


def _valores_validos(nome_categoria: str) -> dict[str, str | list[str]]:
    """Um valor válido para CADA campo (obrigatório ou não) da categoria,
    derivado do schema real (`campos_da_categoria`) em vez de hardcodado —
    não quebra se o formulário do Químico ganhar/perder campos."""
    valores: dict[str, str | list[str]] = {}
    for campo in campos_da_categoria(nome_categoria):
        if campo.tipo == "select":
            valores[campo.name] = campo.opcoes[0]
        elif campo.tipo == "checkbox_multi":
            valores[campo.name] = [campo.opcoes[0]]
        elif campo.tipo == "email":
            valores[campo.name] = "contato@teste.com"
        elif campo.tipo == "date":
            valores[campo.name] = datetime.now(TZ_BR).date().isoformat()
        elif campo.tipo == "number":
            valores[campo.name] = "1"
        else:
            # Texto DISTINTO por campo, com o nome do campo LOGO NO INÍCIO
            # (não só um template genérico repetido) — dois campos de texto
            # com o mesmo valor disparariam de propósito a rede de segurança
            # _remover_copias_entre_campos; o nome no início garante que a
            # distinção sobrevive ao truncamento em `max(min_chars, len(base))`
            # pros campos sem min_chars (a diferença só aparecendo mais pra
            # frente não seria suficiente). `.strip()` no final evita
            # divergir do valor limpo por `validar_payload` (que sempre
            # `.strip()`a a resposta), sem o quê o truncamento podia deixar
            # um espaço sobrando só de um lado.
            base = f"{campo.name} teste"
            texto = (base + " ") * ((campo.min_chars // (len(base) + 1)) + 1)
            valores[campo.name] = texto[: max(campo.min_chars, len(base))].strip()
    return valores


def _saida_quimico(
    categoria: str = CAT_ANALISE,
    campos_formulario: dict[str, Any] | None = None,
    **overrides,
) -> SaidaWhatsAppIntake:
    base = dict(
        informacoes_suficientes=True,
        confianca="ALTA",
        titulo="Assunto genérico dito pelo modelo",
        descricao="Descrição genérica dita pelo modelo.",
        setor="Produção",
        departamento="Dpto Químico",
        categoria=categoria,
        subcategoria=None,
        prioridade="MEDIA",
        campos_formulario=(
            campos_formulario if campos_formulario is not None else _valores_validos(categoria)
        ),
    )
    base.update(overrides)
    return SaidaWhatsAppIntake(**base)


@pytest.mark.parametrize(
    "categoria", [CAT_OCORRENCIA, CAT_VISITA, CAT_ANALISE, CAT_DESENVOLVIMENTO]
)
async def test_quimico_formulario_completo_cria_chamado_com_titulo_e_descricao_derivados(categoria):
    """Um teste por CATEGORIA do Químico (as 4 com layout dinâmico
    conhecido) — prova, com o schema real de cada uma (campos, tipos,
    opções), que o fluxo campo a campo funciona ponta a ponta e não só para
    a categoria mais simples. `titulo`/`descricao` ditos pelo modelo são
    IGNORADOS nesta categoria — o sistema deriva os dois do formulário,
    mesma regra da tela de abertura do Portal (que esconde os dois campos
    para o Químico)."""
    conn = FakeConn()
    with ambiente(
        conn, _settings(whatsapp_intake_departamentos="Dpto Químico"),
        saida=_saida_quimico(categoria), catalogo=_CATALOGO_QUIMICO,
    ) as amb:
        await whatsapp_intake.processar_conversa("conversa-uuid")

        amb.criar.assert_awaited_once()
        kwargs = amb.criar.await_args.kwargs
        assert kwargs["departamento_id"] == "dep-quimico-uuid"
        assert kwargs["dados_formulario"] == _valores_validos(categoria)
        assert kwargs["titulo"] != "Assunto genérico dito pelo modelo"
        assert categoria in kwargs["titulo"]
        assert kwargs["descricao"] != "Descrição genérica dita pelo modelo."
        assert conn.auditorias[0][2] == "CHAMADO_CRIADO"


def test_rotulo_chat_deixa_claro_que_e_do_contato_do_cliente():
    """Pedido do usuário (2026-08-21): "Cargo"/"Setor"/"Fone"/"E-mail" são
    rótulos ambíguos numa conversa linear de WhatsApp (parecem ser sobre
    quem está no chat, não sobre o contato do lado do cliente) — só o texto
    mostrado no chat muda; o schema (`CampoDef.label`, fonte única também
    do Portal, que já agrupa isso visualmente numa seção própria) continua
    intacto."""
    campos = {c.name: c for c in campos_da_categoria(CAT_OCORRENCIA)}
    assert whatsapp_intake._rotulo_chat(campos["cargo"]) == "Cargo do contato do cliente"
    assert whatsapp_intake._rotulo_chat(campos["setor_contato"]) == "Setor do contato do cliente"
    assert whatsapp_intake._rotulo_chat(campos["fone"]) == "Telefone do contato do cliente"
    assert whatsapp_intake._rotulo_chat(campos["email"]) == "E-mail do contato do cliente"
    assert (
        whatsapp_intake._rotulo_chat(campos["nome_contato_cliente"])
        == "Nome do contato do cliente"
    )
    # Schema original nunca muda — o Portal continua mostrando "Cargo" cru
    # (a seção "Contato do Cliente" do form web já desambigua visualmente).
    assert campos["cargo"].label == "Cargo"
    # Campo sem override (ex.: região) não é afetado.
    assert whatsapp_intake._rotulo_chat(campos["regiao"]) == campos["regiao"].label


def test_rotulo_mencionado_em_tolera_parafrase_natural_do_modelo():
    """Achado real em produção (2026-08-21): a checagem antiga exigia a
    substring EXATA do rótulo ("Nome da Empresa (Cliente)") dentro da
    pergunta — uma pergunta parafraseada com naturalidade ("qual é o nome
    da empresa DO cliente?", com "do" a mais e sem os parênteses) não batia,
    deixando passar uma repetição que a rede de segurança deveria ter
    pego."""
    campo = next(
        c for c in campos_da_categoria(CAT_OCORRENCIA) if c.name == "nome_empresa_cliente"
    )
    assert whatsapp_intake._rotulo_mencionado_em(
        campo, "qual é o nome da empresa do cliente?"
    )
    assert whatsapp_intake._rotulo_mencionado_em(campo, "qual é o nome da empresa?")
    assert not whatsapp_intake._rotulo_mencionado_em(campo, "qual é o supervisor?")


def test_remove_copias_entre_campos_descarta_valor_copiado_do_vizinho():
    """Achado real em produção (2026-08-21): mesmo com a "regra dura" no
    prompt contra copiar valor de campo vizinho, o modelo copiou o nome da
    empresa pro campo do contato e o código da região pra cidade — a rede
    de segurança estrutural descarta esses casos (nunca os dois campos
    genuinamente iguais), mas nunca mexe num par que veio de fato diferente
    (mesmo se coincidentemente igual em letra maiúscula/minúscula, o que
    aqui é tratado como igual de propósito — nomes reais não colidem por
    acaso)."""
    entrada = {
        "nome_empresa_cliente": "Alumínios Ltda",
        "nome_contato_cliente": "Alumínios Ltda",  # copiado por engano
        "regiao": "054-CANOAS",
        "cidade": "054-CANOAS",  # copiado por engano
        "cargo": "Comprador",  # campo sem par, nunca mexe
    }
    limpo = whatsapp_intake._remover_copias_entre_campos(entrada)
    assert "nome_contato_cliente" not in limpo
    assert "cidade" not in limpo
    assert limpo["nome_empresa_cliente"] == "Alumínios Ltda"
    assert limpo["regiao"] == "054-CANOAS"
    assert limpo["cargo"] == "Comprador"


def test_remove_copias_entre_campos_preserva_valores_genuinamente_diferentes():
    entrada = {
        "nome_empresa_cliente": "Alumínios Ltda",
        "nome_contato_cliente": "Rodrigo Silva",
        "regiao": "054-CANOAS",
        "cidade": "Canoas",
    }
    assert whatsapp_intake._remover_copias_entre_campos(entrada) == entrada


def test_remove_valores_invalidos_de_select_descarta_opcao_de_outro_campo():
    """Achado real em produção (2026-08-21): o modelo preencheu "Supervisor"
    com um nome que só existe na lista de "Gerente" (e ainda com erro de
    grafia) — sem checagem por campo, isso ficava gravado sem erro nenhum
    por várias rodadas, só sendo pego bem depois na validação final."""
    entrada = {
        "regiao": "007-GRAVATAI",  # opção real, mantido
        "supervisor": "WALLYSSON ALEXANDRO DE ANDRADE MEDEIROS",  # só existe (parecido) em Gerente
        "produto": "DEGRAX 25",  # opção real, mantido
    }
    limpo = whatsapp_intake._remover_valores_invalidos_de_select(CAT_OCORRENCIA, entrada)
    assert "supervisor" not in limpo
    assert limpo["regiao"] == "007-GRAVATAI"
    assert limpo["produto"] == "DEGRAX 25"


def test_remove_valores_invalidos_de_select_preserva_checkbox_multi_parcialmente_valido():
    campo_analises = next(
        c for c in campos_da_categoria(CAT_ANALISE) if c.tipo == "checkbox_multi"
    )
    entrada = {campo_analises.name: [campo_analises.opcoes[0], "Opção Inventada Pelo Modelo"]}
    limpo = whatsapp_intake._remover_valores_invalidos_de_select(CAT_ANALISE, entrada)
    assert limpo[campo_analises.name] == [campo_analises.opcoes[0]]


async def test_quimico_pergunta_reformulada_sobre_campo_ja_preenchido_e_pega():
    """Reproduz o achado real em produção (2026-08-21, captura de tela do
    usuário): rodada anterior perguntou "Qual é o nome da empresa do
    cliente? Preciso disso pra seguir com o registro."; a pessoa respondeu;
    o modelo preencheu `nome_empresa_cliente` corretamente MAS repetiu a
    mesma pergunta, só que reformulada mais curta ("Qual é o nome da
    empresa do cliente?") — a checagem antiga (substring exata do rótulo)
    não pegava isso porque a frase do modelo tem "do" a mais e não tem os
    parênteses do rótulo oficial. Com :func:`_rotulo_mencionado_em`, a rede
    de segurança reconhece que ainda é sobre o mesmo campo e tenta de novo."""
    conn = FakeConn()
    conn.rodada = 1
    conn.resultado_confirmado = {
        "setor": "TI",
        "departamento": "Dpto Químico",
        "categoria": CAT_OCORRENCIA,
        "campos_formulario": {"regiao": "054-CANOAS"},
    }
    conn.mensagens_acumuladas = [
        {"papel": "assistente", "conteudo": "Qual é o nome da empresa do cliente? Preciso disso pra seguir com o registro."},
        {"papel": "usuario", "conteudo": "Aluminio LTDA empresas"},
    ]
    primeira_tentativa = _saida_quimico(
        CAT_OCORRENCIA,
        campos_formulario={"regiao": "054-CANOAS", "nome_empresa_cliente": "Aluminio LTDA empresas"},
        informacoes_suficientes=False,
        perguntas=["Qual é o nome da empresa do cliente?"],
    )
    segunda_tentativa = _saida_quimico(
        CAT_OCORRENCIA,
        campos_formulario={"regiao": "054-CANOAS", "nome_empresa_cliente": "Aluminio LTDA empresas"},
        informacoes_suficientes=False,
        perguntas=["Qual é o código do cliente?"],
    )
    with ambiente(
        conn, _settings(whatsapp_intake_departamentos="Dpto Químico"),
        respostas_modelo=[
            (primeira_tentativa, None, 100, 50),
            (segunda_tentativa, None, 100, 50),
        ],
        catalogo=_CATALOGO_QUIMICO,
    ) as amb:
        await whatsapp_intake.processar_conversa("conversa-uuid")

        assert whatsapp_intake.chamar_modelo_estruturado.await_count == 2
        resposta = amb.responder.await_args.args[1]
        assert "empresa" not in resposta.lower()


async def test_quimico_campo_obrigatorio_faltando_pergunta_em_vez_de_criar():
    """`informacoes_suficientes: true` do modelo não é suficiente sozinho —
    o código revalida contra o schema real e não deixa passar sem os campos
    obrigatórios, mesma postura do `regra.erro` do Marketing."""
    conn = FakeConn()
    saida = _saida_quimico(CAT_ANALISE, campos_formulario={})
    with ambiente(
        conn, _settings(whatsapp_intake_departamentos="Dpto Químico"),
        saida=saida, catalogo=_CATALOGO_QUIMICO,
    ) as amb:
        await whatsapp_intake.processar_conversa("conversa-uuid")

        amb.criar.assert_not_awaited()
        assert conn.auditorias[0][2] == "PERGUNTA"
        resposta = amb.responder.await_args.args[1]
        # Achado real em produção (2026-08-21): o texto técnico de
        # validar_payload ("Preencha o campo...") ia direto pro usuário —
        # a mensagem agora é reescrita numa pergunta natural, mas ainda
        # precisa citar o campo certo e (quando aplicável) as opções.
        assert "ainda preciso saber" in resposta.lower()
        assert "unidade de entrega da amostra" in resposta.lower()
        assert "preencha o campo" not in resposta.lower()


async def test_quimico_progresso_real_nao_cai_na_mensagem_totalmente_generica():
    """Reproduz ao vivo em produção (2026-08-24, depois do fix anterior no
    mesmo dia): "roger" foi corretamente casado com um Supervisor VÁLIDO
    ("ROGERIO DA COSTA CARDOSO") em `campos_formulario` — nenhum valor
    inválido aqui. Mas o modelo repetiu o texto EXATO da pergunta anterior
    ("Qual é o Supervisor?", provável tentativa de confirmar a pessoa
    certa), disparando `_repete_mensagem_anterior`; o retry produziu uma
    pergunta que AINDA menciona "Supervisor" (pega por
    `_quimico_travado_no_campo`), então a conversa caía na mensagem
    TOTALMENTE genérica mesmo com o dado certo já capturado — o usuário
    achava que precisava recomeçar do zero. Como houve progresso real
    (`supervisor` é novo comparado ao confirmado antes), a resposta final
    deve perguntar pelo PRÓXIMO campo real, não a mensagem genérica."""
    conn = FakeConn()
    conn.rodada = 1
    conn.resultado_confirmado = {
        "setor": "TI",
        "departamento": "Dpto Químico",
        "categoria": CAT_OCORRENCIA,
        "campos_formulario": {
            "regiao": "038-SANTA MARIA",
            "produto": "DEGRAX 25",
            "codigo_cliente": "CLI20244",
            "descricao_situacao": "O produto Degrax 25 corroeu a estrutura metálica do cliente.",
        },
    }
    conn.mensagens_acumuladas = [
        {"papel": "assistente", "conteudo": "Qual é o Supervisor?"},
        {"papel": "usuario", "conteudo": "roger"},
    ]
    campos_com_supervisor = {
        "regiao": "038-SANTA MARIA",
        "produto": "DEGRAX 25",
        "codigo_cliente": "CLI20244",
        "descricao_situacao": "O produto Degrax 25 corroeu a estrutura metálica do cliente.",
        "supervisor": "ROGERIO DA COSTA CARDOSO",
    }
    primeira_tentativa = _saida_quimico(
        CAT_OCORRENCIA,
        campos_formulario=campos_com_supervisor,
        informacoes_suficientes=False,
        perguntas=["Qual é o Supervisor?"],  # repete EXATO a pergunta anterior
    )
    segunda_tentativa = _saida_quimico(
        CAT_OCORRENCIA,
        campos_formulario=campos_com_supervisor,
        informacoes_suficientes=False,
        perguntas=["Confirma que o supervisor é Rogério da Costa Cardoso?"],  # ainda cita o rótulo
    )
    with ambiente(
        conn, _settings(whatsapp_intake_departamentos="Dpto Químico"),
        respostas_modelo=[
            (primeira_tentativa, None, 100, 50),
            (segunda_tentativa, None, 100, 50),
        ],
        catalogo=_CATALOGO_QUIMICO,
    ) as amb:
        await whatsapp_intake.processar_conversa("conversa-uuid")

        assert whatsapp_intake.chamar_modelo_estruturado.await_count == 2
        amb.criar.assert_not_awaited()
        resposta = amb.responder.await_args.args[1]
        assert resposta != whatsapp_intake._TEXTO_CONTINUAR_GENERICO
        # Não repete a pergunta que já foi respondida.
        assert "qual é o supervisor" not in resposta.lower()
        # Dado capturado nesta rodada não pode se perder.
        resultado = json.loads(conn.auditorias[0][3])
        assert resultado["campos_formulario"]["supervisor"] == "ROGERIO DA COSTA CARDOSO"


async def test_quimico_valor_de_select_fora_da_lista_pergunta_de_novo():
    """Valor de `select` que não bate EXATO com a lista real (alucinação ou
    interpretação errada do modelo) nunca vira dado gravado — descartado já
    por :func:`whatsapp_intake._remover_valores_invalidos_de_select`
    (achado real em produção 2026-08-21), antes mesmo de chegar na
    validação final: o campo volta a ficar pendente, não "com valor
    errado"."""
    conn = FakeConn()
    campos = _valores_validos(CAT_ANALISE) | {"unidade_entrega": "Unidade Que Não Existe"}
    saida = _saida_quimico(CAT_ANALISE, campos_formulario=campos)
    with ambiente(
        conn, _settings(whatsapp_intake_departamentos="Dpto Químico"),
        saida=saida, catalogo=_CATALOGO_QUIMICO,
    ) as amb:
        await whatsapp_intake.processar_conversa("conversa-uuid")

        amb.criar.assert_not_awaited()
        assert conn.auditorias[0][2] == "PERGUNTA"
        resposta = amb.responder.await_args.args[1].lower()
        resultado = json.loads(conn.auditorias[0][3])
        assert "unidade_entrega" not in (resultado.get("campos_formulario") or {})
        assert "unidade de entrega da amostra" in resposta
        assert "matriz canoas/rs" in resposta
        assert "opção inválida" not in resposta


async def test_quimico_valor_invalido_nao_para_de_pedir_o_campo_certo():
    """Reproduz ao vivo em produção (2026-08-24): a pessoa respondeu
    "Roberto" pro Supervisor — nome que não existe na lista real. O modelo
    achou que tinha capturado a resposta e JÁ pulou pra perguntar o PRÓXIMO
    campo ("Qual é o gerente?"), sem saber que "Roberto" seria descartado
    por :func:`whatsapp_intake._remover_valores_invalidos_de_select`. Sem
    correção, `_quimico_travado_no_campo` detectava o travamento (nenhum
    campo novo confirmado) e tentava de novo com o modelo — que repetia o
    mesmo pulo — até cair na mensagem genérica `_TEXTO_CONTINUAR_GENERICO`
    duas rodadas seguidas, sem NUNCA dizer que o problema era o Supervisor
    nem quais eram as opções válidas. A correção detecta o valor inválido
    na saída BRUTA do modelo e responde direto (sem gastar uma segunda
    chamada) citando o campo certo."""
    conn = FakeConn()
    conn.rodada = 1
    conn.resultado_confirmado = {
        "setor": "TI",
        "departamento": "Dpto Químico",
        "categoria": CAT_OCORRENCIA,
        "campos_formulario": {"regiao": "009-BENTO GONCALVES", "cidade": "Canoas"},
    }
    conn.mensagens_acumuladas = [
        {"papel": "assistente", "conteudo": "Qual é o supervisor?"},
        {"papel": "usuario", "conteudo": "Roberto"},
    ]
    saida = _saida_quimico(
        CAT_OCORRENCIA,
        campos_formulario={
            "regiao": "009-BENTO GONCALVES",
            "cidade": "Canoas",
            "supervisor": "Roberto",
        },
        informacoes_suficientes=False,
        perguntas=["Qual é o gerente?"],
    )
    with ambiente(
        conn, _settings(whatsapp_intake_departamentos="Dpto Químico"),
        saida=saida, catalogo=_CATALOGO_QUIMICO,
    ) as amb:
        await whatsapp_intake.processar_conversa("conversa-uuid")

        # Não vale a pena chamar o modelo de novo — já se sabe o problema.
        assert whatsapp_intake.chamar_modelo_estruturado.await_count == 1
        amb.criar.assert_not_awaited()
        resposta = amb.responder.await_args.args[1]
        assert "roberto" in resposta.lower()
        assert "supervisor" in resposta.lower()
        assert resposta != whatsapp_intake._TEXTO_CONTINUAR_GENERICO
        # A auditoria tem que bater com o que foi mandado de verdade.
        resultado = json.loads(conn.auditorias[0][3])
        assert resultado["perguntas"] == [resposta]


async def test_quimico_checkbox_multi_aceita_mais_de_uma_analise_solicitada():
    """"Análises solicitadas" é `checkbox_multi` — o modelo devolve uma LISTA
    com os itens exatos que a pessoa pediu (o bot apresentou as opções
    numeradas e mapeou a resposta livre de volta para elas)."""
    conn = FakeConn()
    campos_analise = campos_da_categoria(CAT_ANALISE)
    opcoes_analise = next(c for c in campos_analise if c.name == "analises_solicitadas").opcoes
    escolhidas = [opcoes_analise[0], opcoes_analise[2]]
    campos = _valores_validos(CAT_ANALISE) | {"analises_solicitadas": escolhidas}
    saida = _saida_quimico(CAT_ANALISE, campos_formulario=campos)
    with ambiente(
        conn, _settings(whatsapp_intake_departamentos="Dpto Químico"),
        saida=saida, catalogo=_CATALOGO_QUIMICO,
    ) as amb:
        await whatsapp_intake.processar_conversa("conversa-uuid")

        amb.criar.assert_awaited_once()
        assert amb.criar.await_args.kwargs["dados_formulario"]["analises_solicitadas"] == escolhidas


async def test_quimico_checkbox_multi_pergunta_formatada_pelo_codigo():
    """Achado real em produção (2026-08-21): o modelo escreveu as 8 opções
    de "Análises solicitadas" numa única linha corrida, ilegível no
    WhatsApp. Assim que o PRÓXIMO campo pendente é `checkbox_multi`, o
    CÓDIGO formata a pergunta (opções numeradas, uma por linha de verdade)
    e ignora o texto que o modelo escreveu — mesmo princípio de
    `texto_das_perguntas`."""
    conn = FakeConn()
    saida = _saida_quimico(
        CAT_ANALISE,
        campos_formulario={
            "unidade_entrega": "Matriz Canoas/RS",
            "identificacao_cliente": "CLI00002",
            "descricao_amostra": "Reagente que corroeu alumínio",
        },
        perguntas=["Qual a análise? 1. pH 2. Densidade 3. Brix (tudo numa linha só)"],
        informacoes_suficientes=False,
    )
    with ambiente(
        conn, _settings(whatsapp_intake_departamentos="Dpto Químico"),
        saida=saida, catalogo=_CATALOGO_QUIMICO,
    ) as amb:
        await whatsapp_intake.processar_conversa("conversa-uuid")

        resposta = amb.responder.await_args.args[1]
        # Texto mal formatado do modelo foi IGNORADO por completo.
        assert "tudo numa linha só" not in resposta
        assert "Análises solicitadas" in resposta
        # Cada opção numerada tem sua própria linha (quebra real, não espaço).
        assert "\n1. Determinação de pH\n" in resposta
        assert "\n8. Outra" in resposta


async def test_quimico_checkbox_multi_resposta_numerica_preenchida_por_codigo_quando_modelo_falha():
    """Achado real em produção (2026-08-21): a pessoa respondeu "1 e 4" a uma
    pergunta de múltipla escolha e o modelo nunca preencheu
    `campos_formulario["analises_solicitadas"]`, mesmo reconhecendo a
    resposta na própria `descricao` ("Análises já mencionadas: 1 e 4") —
    travando a conversa pedindo a mesma coisa pra sempre. Rede de segurança
    em código: quando o próximo campo pendente é `checkbox_multi` e ainda
    está vazio, mapeia a ÚLTIMA mensagem do usuário por número contra as
    opções reais e preenche direto, sem depender do modelo pra essa parte."""
    conn = FakeConn()
    conn.mensagens_acumuladas = [
        {"papel": "assistente", "conteudo": "Análises solicitadas ...:\n1. Determinação de pH\n..."},
        {"papel": "usuario", "conteudo": "1 e 4"},
    ]
    opcoes_analise = next(
        c for c in campos_da_categoria(CAT_ANALISE) if c.name == "analises_solicitadas"
    ).opcoes
    campos = {
        "unidade_entrega": "Matriz Canoas/RS",
        "identificacao_cliente": "CLI00002",
        "descricao_amostra": "Reagente que corroeu alumínio",
        "objetivo_analises": "Identificar o reagente",
        # "analises_solicitadas" ausente de propósito — o modelo "esqueceu".
    }
    saida = _saida_quimico(CAT_ANALISE, campos_formulario=campos)
    with ambiente(
        conn, _settings(whatsapp_intake_departamentos="Dpto Químico"),
        saida=saida, catalogo=_CATALOGO_QUIMICO,
    ) as amb:
        await whatsapp_intake.processar_conversa("conversa-uuid")

        amb.criar.assert_awaited_once()
        dados = amb.criar.await_args.kwargs["dados_formulario"]
        assert dados["analises_solicitadas"] == [opcoes_analise[0], opcoes_analise[3]]


async def test_quimico_formulario_completo_cria_chamado_mesmo_com_informacoes_suficientes_false():
    """Achado real em produção (2026-08-21), na sequência do teste acima:
    depois da rede de segurança preencher o último campo faltante
    (`analises_solicitadas`), o modelo continuou dizendo
    `informacoes_suficientes: false` e repetindo a mesma pergunta já
    resolvida — mesmo com o formulário 100% completo e válido. O código
    força `informacoes_suficientes: true` quando `validar_payload` (a MESMA
    validação usada na criação) já aprovaria o formulário resultante."""
    conn = FakeConn()
    conn.mensagens_acumuladas = [
        {"papel": "assistente", "conteudo": "Análises solicitadas ...:\n1. Determinação de pH\n..."},
        {"papel": "usuario", "conteudo": "1 e 4"},
    ]
    campos = {
        "unidade_entrega": "Matriz Canoas/RS",
        "identificacao_cliente": "CLI00002",
        "descricao_amostra": "Reagente que corroeu alumínio",
        "objetivo_analises": "Identificar o reagente",
        # "analises_solicitadas" ausente — a rede de segurança numérica preenche.
    }
    saida = _saida_quimico(CAT_ANALISE, campos_formulario=campos, informacoes_suficientes=False)
    with ambiente(
        conn, _settings(whatsapp_intake_departamentos="Dpto Químico"),
        saida=saida, catalogo=_CATALOGO_QUIMICO,
    ) as amb:
        await whatsapp_intake.processar_conversa("conversa-uuid")

        amb.criar.assert_awaited_once()
        assert conn.auditorias[0][2] == "CHAMADO_CRIADO"


async def test_quimico_categoria_sem_layout_dinamico_segue_fluxo_generico():
    """Departamento Químico sozinho não força o formulário fixo — só as
    categorias com layout conhecido (`CAMPOS_POR_CATEGORIA`) exigem isso;
    fora delas, `titulo`/`descricao` do modelo valem normalmente."""
    conn = FakeConn()
    saida = _saida_quimico("Dúvida Geral", campos_formulario={})
    with ambiente(
        conn, _settings(whatsapp_intake_departamentos="Dpto Químico"),
        saida=saida, catalogo=_CATALOGO_QUIMICO,
    ) as amb:
        await whatsapp_intake.processar_conversa("conversa-uuid")

        amb.criar.assert_awaited_once()
        kwargs = amb.criar.await_args.kwargs
        assert kwargs["titulo"] == "Assunto genérico dito pelo modelo"
        assert kwargs["dados_formulario"] == {}


async def test_quimico_no_teto_de_rodadas_com_campo_faltando_encerra():
    conn = FakeConn()
    conn.rodada = 3  # settings usa max_rodadas=4 → esta rodada já é a última
    saida = _saida_quimico(CAT_ANALISE, campos_formulario={})
    with ambiente(
        conn, _settings(whatsapp_intake_departamentos="Dpto Químico"),
        saida=saida, catalogo=_CATALOGO_QUIMICO,
    ) as amb:
        await whatsapp_intake.processar_conversa("conversa-uuid")

        amb.criar.assert_not_awaited()
        assert conn.auditorias[0][2] == "ENCERRADO_SEM_CHAMADO"


async def test_quimico_desenvolvimento_anexa_aviso_estatico_na_confirmacao():
    """Aviso fixo de "Solicitação de Desenvolvimento" (rodapé do formulário
    no Portal) sai também na confirmação do WhatsApp, mesma informação."""
    conn = FakeConn()
    saida = _saida_quimico(CAT_DESENVOLVIMENTO)
    with ambiente(
        conn, _settings(whatsapp_intake_departamentos="Dpto Químico"),
        saida=saida, catalogo=_CATALOGO_QUIMICO,
    ) as amb:
        await whatsapp_intake.processar_conversa("conversa-uuid")

        amb.criar.assert_awaited_once()
        resposta = amb.responder.await_args.args[1]
        assert "gestão de P&D" in resposta


async def test_quimico_campos_formulario_confirmados_persistem_entre_rodadas():
    """Mesma rede de segurança de `_mesclar_campos_confirmados` (setor etc.),
    agora para o dict aninhado `campos_formulario`: um campo já confirmado
    numa rodada anterior não se perde se o modelo omitir de novo."""
    conn = FakeConn()
    conn.rodada = 1  # próxima rodada processada será a 2
    todos = _valores_validos(CAT_ANALISE)
    conn.resultado_confirmado = {
        "departamento": "Dpto Químico",
        "categoria": CAT_ANALISE,
        "campos_formulario": {
            "unidade_entrega": todos["unidade_entrega"],
            "identificacao_cliente": todos["identificacao_cliente"],
        },
    }
    faltando = {k: v for k, v in todos.items() if k not in ("unidade_entrega", "identificacao_cliente")}
    saida = _saida_quimico(CAT_ANALISE, campos_formulario=faltando)
    with ambiente(
        conn, _settings(whatsapp_intake_departamentos="Dpto Químico"),
        saida=saida, catalogo=_CATALOGO_QUIMICO,
    ) as amb:
        await whatsapp_intake.processar_conversa("conversa-uuid")

        assert conn.consultas_campos_confirmados == 1
        amb.criar.assert_awaited_once()
        assert amb.criar.await_args.kwargs["dados_formulario"] == todos


async def test_quimico_campo_travado_tenta_de_novo_e_usa_a_nova():
    """Achado real em produção (2026-08-21): a pessoa responde um campo do
    formulário do Químico claramente (ex.: a justificativa do
    desenvolvimento), mas o modelo não preenche `campos_formulario` nem
    avança — só troca a frase de abertura da MESMA pergunta, o que escapa
    da detecção de repetição EXATA (`_repete_mensagem_anterior`). O código
    detecta pela FALTA de campo novo e tenta de novo antes de mandar ao
    usuário — mesmo padrão de `test_resposta_repetida_tenta_de_novo_e_usa_a_nova`."""
    conn = FakeConn()
    conn.rodada = 2
    conn.resultado_confirmado = {
        "setor": "TI",
        "departamento": "Dpto Químico",
        "categoria": CAT_DESENVOLVIMENTO,
        "campos_formulario": {"objetivo_desenvolvimento": "produto pra limpar pneu"},
    }
    travada = _saida_quimico(
        CAT_DESENVOLVIMENTO,
        campos_formulario={"objetivo_desenvolvimento": "produto pra limpar pneu"},  # sem avanço
        perguntas=["Qual seria a justificativa desse desenvolvimento?"],
        informacoes_suficientes=False,
    )
    avancou = _saida_quimico(
        CAT_DESENVOLVIMENTO,
        campos_formulario={
            "objetivo_desenvolvimento": "produto pra limpar pneu",
            "justificativa": "pneus sujos danificam com o tempo",
        },
        perguntas=["Qual seria o mercado-alvo?"],
        informacoes_suficientes=False,
    )
    with ambiente(
        conn, _settings(whatsapp_intake_departamentos="Dpto Químico"),
        respostas_modelo=[(travada, None, 100, 50), (avancou, None, 80, 40)],
        catalogo=_CATALOGO_QUIMICO,
    ) as amb:
        await whatsapp_intake.processar_conversa("conversa-uuid")

        assert whatsapp_intake.chamar_modelo_estruturado.await_count == 2
        resposta = amb.responder.await_args.args[1]
        assert resposta == "Qual seria o mercado-alvo?"
        resultado = json.loads(conn.auditorias[0][3])
        assert resultado["retry_anti_repeticao"] is True
        assert resultado["campos_formulario"]["justificativa"] == "pneus sujos danificam com o tempo"


async def test_quimico_pergunta_ainda_cita_campo_que_acabou_de_preencher_tenta_de_novo():
    """Achado real em produção (2026-08-21), variação do achado acima: desta
    vez o modelo PREENCHEU o campo novo em `campos_formulario` (ex.:
    "estado": "RS"), mas o TEXTO da pergunta ainda perguntava por ele
    ("Qual é o estado?") em vez de avançar pro próximo campo pendente — o
    dado ficou certo, só o texto que confundia quem estava respondendo."""
    conn = FakeConn()
    conn.rodada = 2
    conn.resultado_confirmado = {
        "setor": "TI",
        "departamento": "Dpto Químico",
        "categoria": CAT_VISITA,
        "campos_formulario": {"cidade": "CANOAS"},
    }
    travada = _saida_quimico(
        CAT_VISITA,
        campos_formulario={"cidade": "CANOAS", "estado": "RS"},  # avançou no DADO...
        perguntas=["Qual é o estado?"],  # ...mas a pergunta continua sobre o mesmo campo
        informacoes_suficientes=False,
    )
    avancou = _saida_quimico(
        CAT_VISITA,
        # `regiao_cliente` NÃO preenchido ainda — é exatamente o campo
        # perguntado agora, ainda sem resposta, não "avançado" no dado.
        campos_formulario={"cidade": "CANOAS", "estado": "RS"},
        perguntas=["Qual é a região do cliente?"],
        informacoes_suficientes=False,
    )
    with ambiente(
        conn, _settings(whatsapp_intake_departamentos="Dpto Químico"),
        respostas_modelo=[(travada, None, 100, 50), (avancou, None, 80, 40)],
        catalogo=_CATALOGO_QUIMICO,
    ) as amb:
        await whatsapp_intake.processar_conversa("conversa-uuid")

        assert whatsapp_intake.chamar_modelo_estruturado.await_count == 2
        resposta = amb.responder.await_args.args[1]
        assert resposta == "Qual é a região do cliente?"
        resultado = json.loads(conn.auditorias[0][3])
        assert resultado["campos_formulario"]["estado"] == "RS"


async def test_quimico_campo_travado_duas_vezes_usa_texto_generico():
    """Se a tentativa nova TAMBÉM não avançar nenhum campo, nunca manda a
    mesma pergunta de novo — cai no texto fixo, mas preserva os campos já
    confirmados (mesmo padrão de `test_resposta_repetida_duas_vezes_usa_texto_generico`)."""
    conn = FakeConn()
    conn.rodada = 2
    conn.resultado_confirmado = {
        "setor": "TI",
        "departamento": "Dpto Químico",
        "categoria": CAT_DESENVOLVIMENTO,
        "campos_formulario": {"objetivo_desenvolvimento": "produto pra limpar pneu"},
    }
    travada = _saida_quimico(
        CAT_DESENVOLVIMENTO,
        campos_formulario={"objetivo_desenvolvimento": "produto pra limpar pneu"},
        perguntas=["Qual seria a justificativa desse desenvolvimento?"],
        informacoes_suficientes=False,
    )
    with ambiente(
        conn, _settings(whatsapp_intake_departamentos="Dpto Químico"),
        respostas_modelo=[(travada, None, 100, 50), (travada, None, 90, 45)],
        catalogo=_CATALOGO_QUIMICO,
    ) as amb:
        await whatsapp_intake.processar_conversa("conversa-uuid")

        assert whatsapp_intake.chamar_modelo_estruturado.await_count == 2
        resposta = amb.responder.await_args.args[1]
        assert resposta == whatsapp_intake._TEXTO_CONTINUAR_GENERICO
        resultado = json.loads(conn.auditorias[0][3])
        assert resultado["campos_formulario"]["objetivo_desenvolvimento"] == "produto pra limpar pneu"


def test_secao_todas_categorias_quimico_injetada_quando_departamento_confirmado_sem_categoria():
    """Achado real em produção (2026-08-21): sem isso, a rodada em que a
    pessoa já diz o suficiente pra identificar a categoria ("sou do TI e
    quero um relatório de ocorrência pro Químico") fica sem o formulário
    disponível — o modelo cai de volta no roteiro genérico ("o que você
    precisa registrar?") por 1 rodada à toa, mesmo já sabendo a resposta.
    Mostrar as 4 categorias de uma vez resolve isso: assim que a categoria é
    reconhecida NESTA rodada, o formulário dela já está no prompt."""
    conversa = [{"papel": "usuario", "conteudo": "quero um relatório de ocorrência pro Químico"}]

    sem_categoria = whatsapp_intake.montar_mensagens(
        conversa, _CATALOGO_QUIMICO, setores=_SETORES,
        campos_confirmados={"departamento": "Dpto Químico"},
    )
    texto = sem_categoria[1]["content"]
    assert "## Formulários do Departamento Químico (categoria ainda não confirmada)" in texto
    # As 4 categorias conhecidas aparecem, não só a mais provável.
    for categoria in (CAT_OCORRENCIA, CAT_VISITA, CAT_ANALISE, CAT_DESENVOLVIMENTO):
        assert f'### Categoria "{categoria}"' in texto
    # Um campo de cada uma, prova que os campos de verdade vieram junto (não
    # só o título da categoria).
    assert "`regiao`" in texto  # Registro de Ocorrência
    assert "`objetivo_desenvolvimento`" in texto  # Solicitação de Desenvolvimento


def test_secao_formulario_quimico_injetada_so_apos_departamento_e_categoria_confirmados():
    conversa = [{"papel": "usuario", "conteudo": "preciso de uma análise de amostra"}]

    com_categoria = whatsapp_intake.montar_mensagens(
        conversa, _CATALOGO_QUIMICO, setores=_SETORES,
        campos_confirmados={"departamento": "Dpto Químico", "categoria": CAT_ANALISE},
        campos_formulario_confirmados={"unidade_entrega": "Matriz Canoas/RS"},
    )
    texto = com_categoria[1]["content"]
    assert f'## Formulário do Departamento Químico — categoria "{CAT_ANALISE}"' in texto
    # Categoria já resolvida: volta pra visão de UMA categoria só, não as 4.
    assert "categoria ainda não confirmada" not in texto
    # Campo já confirmado não é perguntado de novo (não aparece como pendente)...
    assert "`unidade_entrega`" not in texto
    # ...mas o campo seguinte, ainda pendente, continua listado.
    assert "`identificacao_cliente`" in texto
    # A lista de opções da múltipla escolha vai inteira, numerada.
    assert "1. Determinação de pH" in texto
