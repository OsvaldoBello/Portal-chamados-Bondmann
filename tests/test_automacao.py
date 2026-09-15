"""Automação de criação/desligamento de usuários — F2 (fila, gate, API do worker).

Sem banco: as funções ``admin_*`` do repositório são substituídas por fakes
(monkeypatch) e o repositório do staff por um fake em memória. Cobre:
domínio puro (payload, agendamento, classificação, textos), a API do worker
(token fail-closed, claim, heartbeat, resultado + efeitos) e o card/ações do
TI na tela de atendimento.
"""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.auth.dependencies import get_current_user
from app.config import get_settings
from app.domain import automacao as dom, formularios_acessos as ac
from app.main import app
from app.ratelimit import limiter
from app.repositories import automacao as repo_admin
from app.repositories.automacao import JobAtivoExistente, get_automacao_repo
from app.repositories.chamados import get_chamados_repo
from app.services import automacao as svc
from tests.test_workspace import NOW, OP, FakeRepo, _operador

TOKEN = "tok-de-teste-com-tamanho-razoavel"
CHAMADO = {"id": "c1", "codigo": "BD-2026-00001"}

DADOS_CRIACAO = {
    "nome_completo": "João Pedro Souza",
    "email": "joao.souza@bondmann.com.br",
    "perfil": ac.PERFIL_REPRESENTANTE,
    "telefone": "11988887777",
    "regiao_wmw": "082-ARARAQUARA",
    "dispositivo_wmw": "IOS (iPhone / iPad)",
    "data_inicio": "2026-09-21",  # segunda-feira
}
DADOS_INTERNO = {
    "nome_completo": "Maria da Silva",
    "email": "maria.silva@bondmann.com.br",
    "perfil": ac.PERFIL_INTERNO,
    "telefone": "51999998888",
    "cargo": "Assistente Financeira",
    "portal_papel": "Operador de setor (atende a fila)",
    "portal_setor": "Financeiro",
    "licencas_sap": ["PROFESSIONAL"],
}
DADOS_DESLIG = {
    "email": "joao.souza@bondmann.com.br",
    "nome_completo": "João Pedro Souza",
    "perfil": ac.PERFIL_SUPERVISOR,
    "data_desligamento": "2026-09-30",
    "regiao": "127-BETIM",
    "motivo": "Encerramento de Contrato",
    "encaminhar_para": "pedidos@bondmann.com.br",
}


@pytest.fixture(autouse=True)
def _sem_rate_limit():
    anterior = limiter.enabled
    limiter.enabled = False
    try:
        yield
    finally:
        limiter.enabled = anterior


@pytest.fixture
def settings_automacao(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "automacao_ativa", True)
    monkeypatch.setattr(s, "automacao_worker_token", TOKEN)
    monkeypatch.setattr(s, "automacao_tipos", "CRIACAO,DESLIGAMENTO,REVOGAR_LICENCA")
    monkeypatch.setattr(s, "automacao_alerta_email", "")
    monkeypatch.setattr(s, "site_url", "https://portal.test")
    return s


# --------------------------------------------------------------------------
# Domínio
# --------------------------------------------------------------------------
def test_payload_criacao_normaliza_enums_e_regiao():
    p = dom.montar_payload(dom.TIPO_CRIACAO, DADOS_CRIACAO, CHAMADO)
    assert p["versao"] == dom.VERSAO_CONTRATO and p["tipo"] == "CRIACAO"
    assert p["chamado"] == {"id": "c1", "codigo": "BD-2026-00001"}
    assert p["perfil"] == "REPRESENTANTE"
    assert p["regiao"] == {"codigo": "082", "nome": "ARARAQUARA", "completo": "082-ARARAQUARA"}
    assert p["dispositivo_wmw"] == "IOS"
    assert p["cargo"] is None and p["licencas_sap"] == [] and p["pular_etapas"] == []


def test_payload_interno_mapeia_papel_e_licencas():
    p = dom.montar_payload(dom.TIPO_CRIACAO, DADOS_INTERNO, CHAMADO)
    assert p["perfil"] == "INTERNO" and p["portal_papel"] == "OPERADOR"
    assert p["portal_setor"] == "Financeiro" and p["licencas_sap"] == ["PROFESSIONAL"]
    assert p["regiao"] is None and p["dispositivo_wmw"] is None


def test_payload_desligamento_sem_motivo_usa_padrao():
    p = dom.montar_payload(dom.TIPO_DESLIGAMENTO, {**DADOS_DESLIG, "motivo": ""}, CHAMADO)
    assert p["motivo"] == ac.MOTIVO_DESLIGAMENTO_PADRAO
    p = dom.montar_payload(dom.TIPO_DESLIGAMENTO, {k: v for k, v in DADOS_DESLIG.items() if k != "motivo"}, CHAMADO)
    assert p["motivo"] == ac.MOTIVO_DESLIGAMENTO_PADRAO


def test_payload_desligamento():
    p = dom.montar_payload(dom.TIPO_DESLIGAMENTO, DADOS_DESLIG, CHAMADO)
    assert p["perfil"] == "SUPERVISOR" and p["regiao"]["codigo"] == "127"
    assert p["data_desligamento"] == "2026-09-30" and p["encaminhar_para"] == "pedidos@bondmann.com.br"
    with pytest.raises(ValueError):
        dom.montar_payload(dom.TIPO_REVOGAR_LICENCA, {}, CHAMADO)


def test_dia_util_anterior_pula_fim_de_semana_e_feriado():
    # 2026-09-21 é segunda → sexta 18; com 18 feriado → quinta 17
    assert dom.dia_util_anterior(date(2026, 9, 21), set()) == date(2026, 9, 18)
    assert dom.dia_util_anterior(date(2026, 9, 21), {date(2026, 9, 18)}) == date(2026, 9, 17)


def test_executar_apos_criacao_dia_util_anterior_07h_brasilia():
    agora = datetime(2026, 9, 14, 12, 0, tzinfo=UTC)
    p = dom.montar_payload(dom.TIPO_CRIACAO, DADOS_CRIACAO, CHAMADO)
    alvo = dom.calcular_executar_apos(dom.TIPO_CRIACAO, p, agora=agora, feriados=set())
    # sexta 18/09 07:00 America/Sao_Paulo = 10:00 UTC
    assert alvo == datetime(2026, 9, 18, 10, 0, tzinfo=UTC)


def test_executar_apos_sem_data_ou_passada_e_imediato():
    agora = datetime(2026, 9, 25, 12, 0, tzinfo=UTC)
    p = dom.montar_payload(dom.TIPO_CRIACAO, {**DADOS_CRIACAO, "data_inicio": ""}, CHAMADO)
    assert dom.calcular_executar_apos(dom.TIPO_CRIACAO, p, agora=agora, feriados=set()) == agora
    p2 = dom.montar_payload(dom.TIPO_CRIACAO, DADOS_CRIACAO, CHAMADO)  # início 21/09 já passou
    assert dom.calcular_executar_apos(dom.TIPO_CRIACAO, p2, agora=agora, feriados=set()) == agora
    with pytest.raises(ValueError):
        dom.calcular_executar_apos(dom.TIPO_CRIACAO, p, agora=datetime(2026, 1, 1), feriados=set())  # noqa: DTZ001 — naive de propósito


def test_executar_apos_desligamento_18h_e_licenca_15_dias():
    agora = datetime(2026, 9, 14, 12, 0, tzinfo=UTC)
    p = dom.montar_payload(dom.TIPO_DESLIGAMENTO, DADOS_DESLIG, CHAMADO)
    assert dom.calcular_executar_apos(dom.TIPO_DESLIGAMENTO, p, agora=agora, feriados=set()) == datetime(
        2026, 9, 30, 21, 0, tzinfo=UTC
    )
    lic = dom.montar_payload_revogar_licenca(
        {"email": "x@bondmann.com.br", "ms_user_id": "guid", "offboard_date": "2026-09-30"}, CHAMADO
    )
    assert lic["tipo"] == "REVOGAR_LICENCA" and lic["ms_user_id"] == "guid"
    assert dom.calcular_executar_apos(dom.TIPO_REVOGAR_LICENCA, lic, agora=agora, feriados=set()) == datetime(
        2026, 10, 15, 10, 0, tzinfo=UTC
    )


def test_normalizar_etapas_mascara_segredos_e_tolera_lixo():
    etapas = dom.normalizar_etapas(
        [
            {"step_name": "M365", "status": "SUCCESS", "details": {"temp_password": "Abc123!", "email": "a@b", "nested": {"token": "t"}}},
            {"step_name": "WMW", "status": "banana", "error_message": None},
            {"status": "SUCCESS"},
            "lixo",
        ]
    )
    assert etapas[0]["detalhes"] == {"temp_password": "***", "email": "a@b", "nested": {"token": "***"}}
    assert etapas[1]["status"] == "FAILED" and "desconhecido" in etapas[1]["erro"]
    assert etapas[2]["nome"] == "(etapa sem nome)" and etapas[2]["status"] == "FAILED"
    assert len(etapas) == 3


def test_classificar_e_etapas_concluidas():
    ok = [{"nome": "a", "status": "SUCCESS"}, {"nome": "b", "status": "SUCCESS"}]
    misto = [{"nome": "a", "status": "SUCCESS"}, {"nome": "b", "status": "SKIPPED"}]
    ruim = [{"nome": "a", "status": "FAILED"}]
    assert dom.classificar(ok) == dom.STATUS_CONCLUIDO
    assert dom.classificar(misto) == dom.STATUS_COM_PENDENCIAS
    assert dom.classificar(ruim) == dom.STATUS_FALHOU
    assert dom.classificar([]) == dom.STATUS_FALHOU
    assert dom.classificar(ok, erro_geral="boom") == dom.STATUS_FALHOU
    assert dom.etapas_concluidas(misto) == ("a",)


def test_reexecucao_etapas_puladas_contam_como_concluidas():
    """Achado do e2e da F3 (2026-09-15): o worker devolve as etapas de
    `pular_etapas` como SKIPPED + mensagem fixa; sem esta regra a reexecução
    nunca chegava a CONCLUIDO e a nota listava a etapa como pendência."""
    pulada = {"nome": "M365", "status": "SKIPPED", "erro": dom.ETAPA_JA_CONCLUIDA_MSG, "detalhes": {}}
    feita = {"nome": "WMW", "status": "SUCCESS", "erro": None, "detalhes": {}}
    falha = {"nome": "SAP", "status": "FAILED", "erro": "timeout", "detalhes": {}}
    pulada_de_verdade = {"nome": "SIP", "status": "SKIPPED", "erro": "sem TTY", "detalhes": {}}
    assert dom.classificar([pulada, feita]) == dom.STATUS_CONCLUIDO
    assert dom.classificar([pulada, feita, falha]) == dom.STATUS_COM_PENDENCIAS
    assert dom.classificar([pulada, falha]) == dom.STATUS_FALHOU  # nada feito AGORA
    assert dom.classificar([pulada, pulada_de_verdade, feita]) == dom.STATUS_COM_PENDENCIAS
    assert dom.etapas_concluidas([pulada, feita, falha]) == ("M365", "WMW")
    nota = dom.texto_nota_interna({"tipo": "CRIACAO"}, [pulada, feita, falha])
    assert "Pendências para ação manual da TI: SAP" in nota and "TI: M365" not in nota


def test_textos_publico_com_credenciais_so_quando_concluido():
    job = {"tipo": "CRIACAO", "dry_run": False, "worker_id": "w1"}
    etapas = [{"nome": "M365", "status": "SUCCESS", "erro": None, "detalhes": {}}]
    cred = dom.filtrar_credenciais({"senha_temporaria_m365": "S3nh@", "email": "a@bondmann.com.br", "ignorada": "x"})
    txt = dom.texto_mensagem_publica(job, etapas, cred, dom.STATUS_CONCLUIDO)
    assert "concluída" in txt and "S3nh@" in txt and "a@bondmann.com.br" in txt and "ignorada" not in txt
    parcial = dom.texto_mensagem_publica(job, etapas, cred, dom.STATUS_COM_PENDENCIAS)
    assert "parcialmente" in parcial and "S3nh@" not in parcial
    sim = dom.texto_mensagem_publica({**job, "dry_run": True}, etapas, cred, dom.STATUS_CONCLUIDO)
    assert "Simulação" in sim and "S3nh@" not in sim
    nota = dom.texto_nota_interna(job, etapas + [{"nome": "WMW", "status": "FAILED", "erro": "timeout", "detalhes": {}}])
    assert "Pendências" in nota and "WMW" in nota and "timeout" in nota
    assert "senha" not in dom.texto_email_neutro("BD-1", dom.STATUS_CONCLUIDO).lower()


# --------------------------------------------------------------------------
# API do worker
# --------------------------------------------------------------------------
@contextmanager
def api_client():
    with TestClient(app, base_url="https://testserver") as c:
        yield c


def _h(worker="w1"):
    return {"X-Automacao-Token": TOKEN, "X-Automacao-Worker": worker}


def test_api_fail_closed_sem_token_configurado(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "automacao_worker_token", "")
    with api_client() as c:
        assert c.get("/api/automacao/saude", headers=_h()).status_code == 404


def test_api_token_errado_401(settings_automacao):
    with api_client() as c:
        r = c.get("/api/automacao/saude", headers={"X-Automacao-Token": "errado"})
    assert r.status_code == 401


def test_api_saude_e_estado_do_worker(settings_automacao, monkeypatch):
    async def _fila():
        return {"NA_FILA": 2}

    monkeypatch.setattr(repo_admin, "admin_contagem_fila", _fila)
    with api_client() as c:
        r = c.get("/api/automacao/saude", headers=_h("worker-x"))
    assert r.status_code == 200
    corpo = r.json()
    assert corpo["versao_contrato"] == "1" and corpo["fila"] == {"NA_FILA": 2}
    assert "REVOGAR_LICENCA" in corpo["tipos"]
    assert svc.estado_worker()["worker_id"] == "worker-x"


def test_api_proximo_204_com_kill_switch_e_quando_vazio(settings_automacao, monkeypatch):
    chamadas = []

    async def _claim(worker_id, tipos):
        chamadas.append((worker_id, tipos))
        return None

    monkeypatch.setattr(repo_admin, "admin_claim_proximo", _claim)
    with api_client() as c:
        monkeypatch.setattr(settings_automacao, "automacao_ativa", False)
        r = c.post("/api/automacao/jobs/proximo", headers=_h(), json={})
        assert r.status_code == 204 and r.headers.get("X-Automacao-Pausada") == "1"
        assert chamadas == []
        monkeypatch.setattr(settings_automacao, "automacao_ativa", True)
        monkeypatch.setattr(settings_automacao, "automacao_tipos", "CRIACAO")
        r = c.post("/api/automacao/jobs/proximo", headers=_h(), json={"tipos": ["CRIACAO", "DESLIGAMENTO"]})
    assert r.status_code == 204
    assert chamadas == [("w1", ["CRIACAO"])]  # interseção com AUTOMACAO_TIPOS


def test_api_proximo_entrega_job(settings_automacao, monkeypatch):
    async def _claim(worker_id, tipos):
        return {
            "id": "j1", "tipo": "CRIACAO", "dry_run": False, "tentativas": 1, "chamado_id": "c1",
            "chamado_codigo": "BD-2026-00001", "payload": {"versao": "1", "email": "a@bondmann.com.br"},
            "resultado": [], "status": "EXECUTANDO",
        }

    monkeypatch.setattr(repo_admin, "admin_claim_proximo", _claim)
    with api_client() as c:
        r = c.post("/api/automacao/jobs/proximo", headers=_h(), json={})
    assert r.status_code == 200
    assert r.json() == {
        "id": "j1", "tipo": "CRIACAO", "dry_run": False, "tentativa": 1,
        "chamado": {"id": "c1", "codigo": "BD-2026-00001"},
        "payload": {"versao": "1", "email": "a@bondmann.com.br"},
    }


def test_api_heartbeat_409_quando_job_nao_e_do_worker(settings_automacao, monkeypatch):
    async def _hb(job_id, worker_id, etapa):
        return worker_id == "dono"

    monkeypatch.setattr(repo_admin, "admin_heartbeat", _hb)
    with api_client() as c:
        assert c.post("/api/automacao/jobs/j1/heartbeat", headers=_h("dono"), json={"etapa": "WMW"}).status_code == 200
        assert c.post("/api/automacao/jobs/j1/heartbeat", headers=_h("outro"), json={}).status_code == 409


def test_api_resultado_persiste_e_dispara_efeitos(settings_automacao, monkeypatch):
    finalizados = []
    processados = []

    async def _fin(job_id, worker_id, *, status, resultado, erro):
        finalizados.append((job_id, worker_id, status, resultado, erro))
        return {"id": job_id, "chamado_id": "c1", "tipo": "CRIACAO", "status": status,
                "dry_run": False, "aprovado_por": OP, "payload": {}, "resultado": resultado,
                "chamado_codigo": "BD-1", "worker_id": worker_id}

    async def _proc(job, etapas, credenciais, licenca, **kw):
        processados.append((job["status"], etapas, credenciais, licenca))

    monkeypatch.setattr(repo_admin, "admin_finalizar", _fin)
    monkeypatch.setattr(svc, "processar_resultado", _proc)
    with api_client() as c:
        r = c.post(
            "/api/automacao/jobs/j1/resultado", headers=_h(),
            json={
                "etapas": [
                    {"step_name": "M365", "status": "SUCCESS", "details": {"temp_password": "x"}},
                    {"step_name": "WMW", "status": "SKIPPED", "error_message": "timeout"},
                ],
                "credenciais": {"senha_temporaria_m365": "x"},
                "licenca": None,
            },
        )
    assert r.status_code == 200 and r.json()["status"] == "CONCLUIDO_COM_PENDENCIAS"
    job_id, worker, status_final, resultado, erro = finalizados[0]
    assert (job_id, worker, status_final, erro) == ("j1", "w1", "CONCLUIDO_COM_PENDENCIAS", None)
    assert resultado[0]["detalhes"]["temp_password"] == "***"  # nunca em claro na tabela
    assert processados and processados[0][2] == {"senha_temporaria_m365": "x"}


def test_api_resultado_duplicado_409(settings_automacao, monkeypatch):
    async def _fin(*a, **k):
        return None

    monkeypatch.setattr(repo_admin, "admin_finalizar", _fin)
    with api_client() as c:
        r = c.post("/api/automacao/jobs/j1/resultado", headers=_h(), json={"etapas": []})
    assert r.status_code == 409


# --------------------------------------------------------------------------
# Efeitos do resultado (service) — admin_* substituídos
# --------------------------------------------------------------------------
class _Admin:
    def __init__(self, monkeypatch):
        self.mensagens: list[tuple] = []
        self.resolvidos: list[str] = []
        self.agendados: list[dict] = []
        self.historico: list[tuple] = []
        self.emails: list[tuple] = []

        async def gravar(chamado_id, remetente_id, conteudo, *, interna):
            self.mensagens.append((chamado_id, remetente_id, conteudo, interna))

        async def resolver(chamado_id, ator_id, job_id):
            self.resolvidos.append(chamado_id)
            return True

        async def agendar(**kw):
            self.agendados.append(kw)
            return {"id": "j-lic"}

        async def hist(chamado_id, ator_id, acao, detalhes):
            self.historico.append((acao, detalhes))

        async def notificar(chamado, remetente_id, conteudo, observadores=None):
            self.emails.append(("msg", chamado["id"], conteudo))

        async def conta(payload, aprovado_por):
            return True, "conta x criada"

        monkeypatch.setattr(repo_admin, "admin_gravar_mensagem", gravar)
        monkeypatch.setattr(repo_admin, "admin_resolver_chamado", resolver)
        monkeypatch.setattr(repo_admin, "admin_agendar_job", agendar)
        monkeypatch.setattr(repo_admin, "admin_registrar_historico", hist)
        monkeypatch.setattr(svc, "_criar_conta_portal", conta)
        import app.notification as notif

        monkeypatch.setattr(notif, "notificar_nova_mensagem_email", notificar)


def _job(**over):
    base = {
        "id": "j1", "chamado_id": "c1", "chamado_codigo": "BD-1", "chamado_titulo": "t",
        "chamado_cliente_id": "aaa", "chamado_operador_id": OP, "tipo": "CRIACAO",
        "status": "CONCLUIDO", "dry_run": False, "aprovado_por": OP, "worker_id": "w1", "erro": None,
        "payload": {"email": "a@bondmann.com.br", "nome_completo": "A", "perfil": "REPRESENTANTE"},
    }
    base.update(over)
    return base


def _run(coro):
    return asyncio.run(coro)


def test_processar_criacao_concluida_resolve_e_manda_credenciais(settings_automacao, monkeypatch):
    adm = _Admin(monkeypatch)
    etapas = [{"nome": "M365", "status": "SUCCESS", "erro": None, "detalhes": {}}]
    _run(svc.processar_resultado(_job(), etapas, {"senha_temporaria_m365": "S3nh@"}, None, settings=settings_automacao))
    internas = [m for m in adm.mensagens if m[3]]
    publicas = [m for m in adm.mensagens if not m[3]]
    assert len(internas) == 1 and "Portal de Chamados Bondmann" in internas[0][2]
    assert len(publicas) == 1 and "S3nh@" in publicas[0][2] and "Esqueci minha senha" in publicas[0][2]
    assert publicas[0][1] == OP  # remetente = quem aprovou
    assert adm.resolvidos == ["c1"]
    assert adm.emails and "S3nh@" not in adm.emails[0][2]  # e-mail neutro
    assert adm.agendados == []


def test_processar_com_pendencias_nao_resolve_nem_expoe_senha(settings_automacao, monkeypatch):
    adm = _Admin(monkeypatch)
    etapas = [
        {"nome": "M365", "status": "SUCCESS", "erro": None, "detalhes": {}},
        {"nome": "WMW", "status": "FAILED", "erro": "timeout", "detalhes": {}},
    ]
    _run(svc.processar_resultado(_job(status="CONCLUIDO_COM_PENDENCIAS"), etapas, {"senha_temporaria_m365": "S3nh@"}, None, settings=settings_automacao))
    publicas = [m for m in adm.mensagens if not m[3]]
    assert publicas and "parcialmente" in publicas[0][2] and "S3nh@" not in publicas[0][2]
    assert adm.resolvidos == []


def test_processar_falhou_so_nota_interna(settings_automacao, monkeypatch):
    adm = _Admin(monkeypatch)
    _run(svc.processar_resultado(_job(status="FALHOU", erro="worker explodiu"), [], None, None, settings=settings_automacao))
    assert len(adm.mensagens) == 1 and adm.mensagens[0][3] is True and "worker explodiu" in adm.mensagens[0][2]
    assert adm.resolvidos == [] and adm.emails == []


def test_processar_desligamento_agenda_revogacao_de_licenca(settings_automacao, monkeypatch):
    adm = _Admin(monkeypatch)
    etapas = [{"nome": "M365", "status": "SUCCESS", "erro": None, "detalhes": {}}]
    _run(svc.processar_resultado(
        _job(tipo="DESLIGAMENTO"), etapas, {},
        {"email": "a@bondmann.com.br", "ms_user_id": "guid", "offboard_date": "2026-09-30"},
        settings=settings_automacao,
    ))
    assert len(adm.agendados) == 1
    ag = adm.agendados[0]
    assert ag["tipo"] == "REVOGAR_LICENCA" and ag["payload"]["ms_user_id"] == "guid" and ag["reexecucao_de"] == "j1"
    assert ag["executar_apos"] == datetime(2026, 10, 15, 10, 0, tzinfo=UTC)
    assert adm.resolvidos == ["c1"]


def test_processar_dry_run_nao_resolve_nem_cria_conta(settings_automacao, monkeypatch):
    adm = _Admin(monkeypatch)
    etapas = [{"nome": "M365", "status": "SUCCESS", "erro": None, "detalhes": {}}]
    _run(svc.processar_resultado(_job(dry_run=True), etapas, {"senha_temporaria_m365": "x"}, None, settings=settings_automacao))
    publicas = [m for m in adm.mensagens if not m[3]]
    assert publicas and "Simulação" in publicas[0][2] and "x" not in publicas[0][2].split("Simulação")[0]
    assert adm.resolvidos == []
    assert not any("Portal de Chamados Bondmann" in m[2] for m in adm.mensagens if m[3])


def test_vigilancia_marca_travado_e_requeue_so_fora_do_desligamento(settings_automacao, monkeypatch):
    adm = _Admin(monkeypatch)
    marcados = []

    async def travados(timeout):
        return [
            {"id": "j1", "tipo": "CRIACAO", "tentativas": 1, "worker_id": "w1", "etapa_atual": "WMW",
             "chamado_id": "c1", "aprovado_por": OP, "chamado_codigo": "BD-1"},
            {"id": "j2", "tipo": "DESLIGAMENTO", "tentativas": 1, "worker_id": "w1", "etapa_atual": None,
             "chamado_id": "c2", "aprovado_por": OP, "chamado_codigo": "BD-2"},
        ]

    async def marcar(job_id, motivo, *, requeue):
        marcados.append((job_id, requeue))
        return {"id": job_id}

    async def vencida():
        return None

    monkeypatch.setattr(repo_admin, "admin_jobs_travados", travados)
    monkeypatch.setattr(repo_admin, "admin_marcar_travado", marcar)
    monkeypatch.setattr(repo_admin, "admin_fila_vencida_desde", vencida)
    _run(svc.vigiar_uma_vez(settings_automacao))
    assert marcados == [("j1", True), ("j2", False)]
    assert len([m for m in adm.mensagens if m[3]]) == 2


# --------------------------------------------------------------------------
# Card e ações do TI na tela de atendimento
# --------------------------------------------------------------------------
class _RepoAcessos(FakeRepo):
    """Chamado da TI em "Criação de Novo Usuário" com dados estruturados."""

    def __init__(self, *, subcategoria=ac.SUB_CRIACAO_USUARIO, dados=None, **kw):
        super().__init__(**kw)
        self._sub = subcategoria
        self._dados = dados if dados is not None else DADOS_CRIACAO

    async def obter(self, claims, cid):
        base = await super().obter(claims, cid)
        base.update({"subcategoria": self._sub, "dados_formulario": self._dados,
                     "categoria": ac.CAT_USUARIOS_E_ACESSOS})
        return base


class FakeAutomacaoRepo:
    def __init__(self, jobs=None, *, ativo_existente=False):
        self.jobs = jobs or []
        self.criados: list[dict] = []
        self.cancelados: list[tuple] = []
        self._ativo_existente = ativo_existente

    async def jobs_do_chamado(self, claims, chamado_id):
        return list(self.jobs)

    async def criar_job(self, claims, **kw):
        if self._ativo_existente:
            raise JobAtivoExistente()
        self.criados.append(kw)
        return {"id": "novo", **kw, "status": "NA_FILA"}

    async def cancelar_job(self, claims, chamado_id, job_id, ator_id):
        self.cancelados.append((chamado_id, job_id))
        return job_id == "j-fila"

    async def feriados_entre(self, claims, de, ate):
        return set()


@contextmanager
def ws(repo, arepo):
    app.dependency_overrides[get_current_user] = _operador
    app.dependency_overrides[get_chamados_repo] = lambda: repo
    app.dependency_overrides[get_automacao_repo] = lambda: arepo
    try:
        with TestClient(app, base_url="https://testserver") as c:
            yield c
    finally:
        for dep in (get_current_user, get_chamados_repo, get_automacao_repo):
            app.dependency_overrides.pop(dep, None)


def _csrf(c):
    c.get("/workspace")
    return c.cookies.get("csrf_token")


def _job_row(**over):
    base = {
        "id": "j-fila", "chamado_id": "c1", "tipo": "CRIACAO", "status": "NA_FILA", "dry_run": False,
        "payload": dom.montar_payload("CRIACAO", DADOS_CRIACAO, CHAMADO), "resultado": [],
        "executar_apos": NOW + timedelta(days=1), "aprovado_por": OP, "aprovado_em": NOW,
        "aprovado_por_nome": "Op TI", "worker_id": None, "claimed_at": None, "heartbeat_at": None,
        "etapa_atual": None, "finalizado_em": None, "tentativas": 0, "reexecucao_de": None, "erro": None,
    }
    base.update(over)
    return base


def test_card_aguardando_aprovacao_com_resumo_e_botoes(settings_automacao):
    repo = _RepoAcessos(status="EM_ATENDIMENTO", operador_id=OP)
    with ws(repo, FakeAutomacaoRepo()) as c:
        r = c.get("/workspace/chamados/c1")
    assert r.status_code == 200
    html = r.text
    assert "Automação de acessos — Criação de acessos" in html
    assert "Aguardando aprovação da TI" in html
    assert "082-ARARAQUARA" in html and "REPRESENTANTE" in html
    assert 'value="real"' in html and 'value="simulacao"' in html
    assert "Cancelar execução" not in html


def test_card_sem_atendimento_iniciado_nao_mostra_botoes(settings_automacao):
    repo = _RepoAcessos(status="NOVO", operador_id=None)
    with ws(repo, FakeAutomacaoRepo()) as c:
        html = c.get("/workspace/chamados/c1").text
    assert "Inicie o atendimento para poder executar" in html
    assert 'value="real"' not in html


def test_card_nao_aparece_fora_das_subcategorias(settings_automacao):
    repo = _RepoAcessos(subcategoria="Redefinição de Senha", status="EM_ATENDIMENTO", operador_id=OP)
    with ws(repo, FakeAutomacaoRepo()) as c:
        html = c.get("/workspace/chamados/c1").text
    assert "automacao-card" not in html


def test_card_pausado_desabilita_botoes(settings_automacao, monkeypatch):
    monkeypatch.setattr(settings_automacao, "automacao_ativa", False)
    repo = _RepoAcessos(status="EM_ATENDIMENTO", operador_id=OP)
    with ws(repo, FakeAutomacaoRepo()) as c:
        html = c.get("/workspace/chamados/c1").text
    assert "Automação pausada" in html
    assert 'value="real" disabled' in html


def test_card_job_na_fila_mostra_cancelar_e_esconde_executar(settings_automacao):
    repo = _RepoAcessos(status="EM_ATENDIMENTO", operador_id=OP)
    with ws(repo, FakeAutomacaoRepo([_job_row()])) as c:
        html = c.get("/workspace/chamados/c1").text
    assert "Na fila" in html and "Cancelar execução" in html and "Op TI" in html
    assert 'value="real"' not in html


def test_card_com_pendencias_oferece_reexecucao(settings_automacao):
    job = _job_row(
        status="CONCLUIDO_COM_PENDENCIAS", finalizado_em=NOW, worker_id="w1",
        resultado=[{"nome": "M365", "status": "SUCCESS", "erro": None, "detalhes": {}},
                   {"nome": "WMW", "status": "FAILED", "erro": "timeout", "detalhes": {}}],
    )
    repo = _RepoAcessos(status="EM_ATENDIMENTO", operador_id=OP)
    with ws(repo, FakeAutomacaoRepo([job])) as c:
        html = c.get("/workspace/chamados/c1").text
    assert "Concluída com pendências" in html and "timeout" in html
    assert 'value="reexecutar"' in html and "Executar do zero" in html


def test_executar_cria_job_na_fila_com_agendamento(settings_automacao):
    repo = _RepoAcessos(status="EM_ATENDIMENTO", operador_id=OP)
    arepo = FakeAutomacaoRepo()
    with ws(repo, arepo) as c:
        t = _csrf(c)
        r = c.post("/workspace/chamados/c1/automacao/executar", data={"modo": "real"},
                   headers={"X-CSRF-Token": t}, follow_redirects=False)
    assert r.status_code == 303
    assert len(arepo.criados) == 1
    job = arepo.criados[0]
    assert job["tipo"] == "CRIACAO" and job["dry_run"] is False and job["aprovado_por"] == OP
    assert job["payload"]["perfil"] == "REPRESENTANTE"
    assert job["executar_apos"].tzinfo is not None


def test_executar_simulacao_roda_ja(settings_automacao):
    repo = _RepoAcessos(status="EM_ATENDIMENTO", operador_id=OP)
    arepo = FakeAutomacaoRepo()
    with ws(repo, arepo) as c:
        t = _csrf(c)
        c.post("/workspace/chamados/c1/automacao/executar", data={"modo": "simulacao"},
               headers={"X-CSRF-Token": t}, follow_redirects=False)
    job = arepo.criados[0]
    assert job["dry_run"] is True
    assert job["executar_apos"] <= datetime.now(UTC) + timedelta(seconds=5)


def test_executar_reexecutar_pula_etapas_concluidas(settings_automacao):
    anterior = _job_row(
        id="j-old", status="CONCLUIDO_COM_PENDENCIAS",
        resultado=[{"nome": "M365", "status": "SUCCESS"}, {"nome": "WMW", "status": "FAILED"}],
    )
    repo = _RepoAcessos(status="EM_ATENDIMENTO", operador_id=OP)
    arepo = FakeAutomacaoRepo([anterior])
    with ws(repo, arepo) as c:
        t = _csrf(c)
        r = c.post("/workspace/chamados/c1/automacao/executar", data={"modo": "reexecutar"},
                   headers={"X-CSRF-Token": t}, follow_redirects=False)
    assert r.status_code == 303
    job = arepo.criados[0]
    assert job["reexecucao_de"] == "j-old" and job["payload"]["pular_etapas"] == ["M365"]


def test_executar_recusa_quando_ja_ha_job_ativo(settings_automacao):
    repo = _RepoAcessos(status="EM_ATENDIMENTO", operador_id=OP)
    arepo = FakeAutomacaoRepo([_job_row()])
    with ws(repo, arepo) as c:
        t = _csrf(c)
        r = c.post("/workspace/chamados/c1/automacao/executar", data={"modo": "real"},
                   headers={"X-CSRF-Token": t})
    assert r.status_code == 200 and "Já existe uma execução" in r.text
    assert arepo.criados == []


def test_executar_exige_pode_atender(settings_automacao):
    repo = _RepoAcessos(status="NOVO", operador_id=None)  # ninguém iniciou o atendimento
    with ws(repo, FakeAutomacaoRepo()) as c:
        t = _csrf(c)
        r = c.post("/workspace/chamados/c1/automacao/executar", data={"modo": "real"},
                   headers={"X-CSRF-Token": t})
    assert r.status_code == 403


def test_cancelar_job_na_fila(settings_automacao):
    repo = _RepoAcessos(status="EM_ATENDIMENTO", operador_id=OP)
    arepo = FakeAutomacaoRepo([_job_row()])
    with ws(repo, arepo) as c:
        t = _csrf(c)
        r = c.post("/workspace/chamados/c1/automacao/j-fila/cancelar", headers={"X-CSRF-Token": t},
                   follow_redirects=False)
        r2 = c.post("/workspace/chamados/c1/automacao/j-outro/cancelar", headers={"X-CSRF-Token": t})
    assert r.status_code == 303 and arepo.cancelados[0] == ("c1", "j-fila")
    assert r2.status_code == 200 and "não está mais na fila" in r2.text
