"""Intake de chamado via WhatsApp — fluxo do webhook até a criação do chamado.

Cobre os invariantes estruturais da feature: kill switch sem efeito colateral,
idempotência por ``wamid``, telefone não cadastrado nunca vira chamado,
o chamado é criado em nome do PERFIL RESOLVIDO (não de um perfil de sistema),
e destino alucinado pelo modelo nunca vira INSERT.
"""

import json
from contextlib import ExitStack, asynccontextmanager, contextmanager
from dataclasses import dataclass
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from app.config import Settings
from app.ia import whatsapp_intake
from app.ia.schemas import SaidaWhatsAppIntake
from app.main import app

_TELEFONE = "5551999998888"
_PERFIL = {
    "id": "perfil-uuid",
    "nome": "Fulano",
    "empresa_id": "empresa-uuid",
    "departamento_id": "dep-autor-uuid",
    "departamento_nome": "Compras",
}
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


def _settings(**overrides) -> Settings:
    base = dict(
        session_secret="segredo-real-de-teste-nao-default",
        csrf_secret="outro-segredo-real-de-teste-nao-default",
        whatsapp_intake_ativo=True,
        whatsapp_intake_departamentos="TI",
        ia_triagem_api_key="chave-de-teste",
        whatsapp_intake_max_rodadas=4,
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
    catalogo: list | None = None,
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
            whatsapp_intake, "chamar_modelo_estruturado", AsyncMock(return_value=(saida, None, 100, 50))
        ),
        patch.object(whatsapp_intake, "_responder", responder),
        patch.object(whatsapp_intake, "_imagem_da_conversa", AsyncMock(return_value=None)),
        patch.object(whatsapp_intake, "_anexar_imagem", AsyncMock()),
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
        departamento="TI",
        categoria="Equipamentos",
        subcategoria="Impressora",
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
        # `setor` = setor demandante (o do autor), não o de destino.
        assert kwargs["setor"] == "Compras"
        assert kwargs["telefone_contato"] == _TELEFONE

        # Confirmação com o código do chamado.
        amb.responder.assert_awaited_once()
        assert "BOND-2026-00999" in amb.responder.await_args.args[1]
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


async def test_perfil_sem_setor_nao_inventa_valor():
    """`setor` é obrigatório; sem departamento no perfil, orienta contato humano."""
    conn = FakeConn()
    perfil_sem_setor = dict(_PERFIL, departamento_id=None, departamento_nome=None)
    with ambiente(
        conn, _settings(), perfil=perfil_sem_setor, saida=_saida_completa()
    ) as amb:
        await whatsapp_intake.processar_conversa("conversa-uuid")

        amb.criar.assert_not_awaited()
        assert "setor" in amb.responder.await_args.args[1].lower()


async def test_informacao_insuficiente_pergunta_e_mantem_conversa_aberta():
    conn = FakeConn()
    saida = SaidaWhatsAppIntake(
        informacoes_suficientes=False,
        confianca="BAIXA",
        pergunta_esclarecimento="Qual equipamento apresentou o problema?",
    )
    with ambiente(conn, _settings(), saida=saida) as amb:
        await whatsapp_intake.processar_conversa("conversa-uuid")

        amb.criar.assert_not_awaited()
        amb.responder.assert_awaited_once()
        assert "Qual equipamento" in amb.responder.await_args.args[1]
        assert conn.auditorias[0][2] == "PERGUNTA"
        # Conversa volta a COLETANDO para aceitar a resposta do usuário.
        assert any(
            "COLETANDO" in str(args)
            for sql, args in conn.executes
            if "UPDATE whatsapp_conversas" in sql
        )


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
