"""Testes do Workspace do Operador (Fase 4) — auth/repo fakes, sem banco."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from app.auth.dependencies import CurrentUser, get_current_user
from app.main import app
from app.repositories.chamados import get_chamados_repo

OP = "99999999-9999-9999-9999-999999999999"
NOW = datetime.now(timezone.utc)


def _operador() -> CurrentUser:
    return CurrentUser(id=OP, email="op@bond.com", role="OPERADOR",
                       claims={"sub": OP, "app_metadata": {"role": "OPERADOR"}})


def _funcionario() -> CurrentUser:
    return CurrentUser(id="aaa", email="f@bond.com", role="CLIENTE",
                       claims={"sub": "aaa", "app_metadata": {"role": "CLIENTE"}})


def _chamado(**extra):
    base = {
        "id": "c1", "codigo": "BOND-2026-00001", "titulo": "Impressora", "descricao": "quebrou",
        "status": "NOVO", "prioridade": "ALTA", "cliente_id": "aaa", "operador_id": None,
        "departamento_id": "d1", "departamento": "TI", "categoria": "Suporte",
        "cliente_nome": "Ana", "operador_nome": None,
        "created_at": NOW - timedelta(hours=20), "limite_resolucao": NOW + timedelta(hours=1),
        "limite_resposta": None, "respondido_em": None, "resolvido_em": None,
        "avaliacao_nota": None, "avaliacao_comentario": None, "avaliacao_em": None,
    }
    base.update(extra)
    return base


class FakeRepo:
    def __init__(self, *, is_ti=True, status="NOVO"):
        self.acoes = []
        self._is_ti = is_ti
        self._status = status
        self.operadores_dep = "__nao_chamado__"  # captura o filtro de departamento

    async def perfil(self, claims):
        return {"id": OP, "nome": "Op TI", "role": "OPERADOR", "empresa_id": "e1",
                "departamento_id": "d1", "departamento": "TI", "is_ti": self._is_ti}

    async def fila(self, claims, *, status=None, categoria_id=None,
                   prioridade=None, operador_id=None, limite=200):
        self.fila_filtros = {  # captura os filtros aplicados
            "categoria_id": categoria_id, "prioridade": prioridade, "operador_id": operador_id,
        }
        cs = [_chamado(), _chamado(id="c2", codigo="BOND-2026-00002", status="EM_ATENDIMENTO")]
        return [c for c in cs if status is None or c["status"] == status]

    async def fila_stats(self, claims):
        return {"total": 2, "NOVO": 1, "EM_ATENDIMENTO": 1, "AGUARDANDO": 0, "RESOLVIDO": 0}

    async def fila_assinatura(self, claims, *, status=None):
        return (2, NOW)

    async def categorias_ativas(self, claims, departamento_id=None):
        return [{"id": "cat1", "nome": "Suporte"}]

    async def obter(self, claims, cid):
        return _chamado(id=cid, status=self._status)

    async def mensagens(self, claims, cid):
        return []

    async def operadores(self, claims, *, departamento_id=None):
        self.operadores_dep = departamento_id
        return [{"id": OP, "nome": "Op TI", "departamento": "TI"}]

    async def departamentos_ativos(self, claims):
        return [{"id": "d1", "nome": "TI"}, {"id": "d2", "nome": "RH"}, {"id": "d3", "nome": "Marketing"}]

    async def alterar_status(self, claims, cid, novo):
        self.acoes.append(("status", cid, novo)); return {"id": cid, "status": novo}

    async def alterar_prioridade(self, claims, cid, nova):
        self.acoes.append(("prioridade", cid, nova)); return {"id": cid, "prioridade": nova}

    async def atribuir(self, claims, cid, operador_id):
        self.acoes.append(("atribuir", cid, operador_id)); return {"id": cid, "operador_id": operador_id}

    async def iniciar_atendimento(self, claims, cid, *, operador_id):
        self.acoes.append(("iniciar", cid, operador_id))
        return {"id": cid, "status": "EM_ATENDIMENTO", "operador_id": operador_id}

    async def transferir(self, claims, cid, *, departamento_id):
        self.acoes.append(("transferir", cid, departamento_id))
        return {"id": cid, "departamento_id": departamento_id}

    async def responder_staff(self, claims, cid, *, conteudo, is_interna, anexos=None):
        self.acoes.append(("msg", cid, conteudo, is_interna, anexos)); return {"id": "m1", "created_at": NOW}

    async def excluir(self, claims, cid):
        self.acoes.append(("excluir", cid)); return True


@contextmanager
def ws_client(repo, user=_operador):
    app.dependency_overrides[get_current_user] = user
    app.dependency_overrides[get_chamados_repo] = lambda: repo
    try:
        with TestClient(app, base_url="https://testserver") as c:
            yield c
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_chamados_repo, None)


def _csrf(c):
    c.get("/workspace")
    return c.cookies.get("csrf_token")


def test_funcionario_nao_acessa_workspace():
    with ws_client(FakeRepo(), user=_funcionario) as c:
        r = c.get("/workspace")
    assert r.status_code == 403


def test_fila_lista_mostra_sla_e_chamados():
    with ws_client(FakeRepo()) as c:
        r = c.get("/workspace")
    assert r.status_code == 200
    assert "BOND-2026-00001" in r.text
    assert "restantes" in r.text          # chip de SLA presente (faltando ~1h)
    assert "animate-pulse" in r.text      # <10% da janela -> crítico piscante
    assert "Fila de chamados" in r.text


def test_kanban_renderiza_colunas():
    with ws_client(FakeRepo()) as c:
        r = c.get("/workspace/kanban")
    assert r.status_code == 200
    for label in ["Novo", "Em atendimento", "Aguardando", "Resolvido"]:
        assert label in r.text
    assert "kanban-col" in r.text
    # Lixeira de exclusão rápida em cada cartão.
    assert "kanban-delete-btn" in r.text


def test_mudar_status_registra_e_redireciona():
    repo = FakeRepo()
    with ws_client(repo) as c:
        t = _csrf(c)
        r = c.post("/workspace/chamados/c1/status", data={"novo_status": "EM_ATENDIMENTO"},
                   headers={"X-CSRF-Token": t}, follow_redirects=False)
    assert r.status_code == 303
    assert ("status", "c1", "EM_ATENDIMENTO") in repo.acoes


def test_status_invalido_ignorado():
    repo = FakeRepo()
    with ws_client(repo) as c:
        t = _csrf(c)
        r = c.post("/workspace/chamados/c1/status", data={"novo_status": "XX"},
                   headers={"X-CSRF-Token": t}, follow_redirects=False)
    assert r.status_code == 303
    assert repo.acoes == []


def test_nota_interna_repassa_flag():
    repo = FakeRepo()
    with ws_client(repo) as c:
        t = _csrf(c)
        c.post("/workspace/chamados/c1/mensagens",
               data={"conteudo": "verificar campo", "is_interna": "1"},
               headers={"X-CSRF-Token": t}, follow_redirects=False)
        c.post("/workspace/chamados/c1/mensagens",
               data={"conteudo": "olá cliente"},
               headers={"X-CSRF-Token": t}, follow_redirects=False)
    assert ("msg", "c1", "verificar campo", True, []) in repo.acoes
    assert ("msg", "c1", "olá cliente", False, []) in repo.acoes


def test_atendimento_renderiza_acoes_e_composer():
    with ws_client(FakeRepo()) as c:
        r = c.get("/workspace/chamados/c1")
    assert r.status_code == 200
    assert "Ações" in r.text
    assert 'action="/workspace/chamados/c1/status"' in r.text
    assert 'action="/workspace/chamados/c1/prioridade"' in r.text
    assert 'action="/workspace/chamados/c1/atribuir"' in r.text
    assert 'id="is-interna"' in r.text          # toggle de nota interna
    assert 'id="composer"' in r.text
    # Composer aceita anexos (imagens/documentos) na conversa.
    assert 'enctype="multipart/form-data"' in r.text
    assert 'type="file" name="arquivos"' in r.text
    # Staff também tem acesso a abrir chamado (link no shell do workspace).
    assert 'href="/portal/chamados/novo"' in r.text


def test_atribuir_operador():
    repo = FakeRepo()
    with ws_client(repo) as c:
        t = _csrf(c)
        c.post("/workspace/chamados/c1/atribuir", data={"operador_id": OP},
               headers={"X-CSRF-Token": t}, follow_redirects=False)
    assert ("atribuir", "c1", OP) in repo.acoes


def test_atendimento_mostra_botao_excluir_e_pede_confirmacao():
    with ws_client(FakeRepo()) as c:
        r = c.get("/workspace/chamados/c1")
        assert "Excluir chamado" in r.text
        assert "Confirmar exclusão" not in r.text  # etapa 1: só o link, sem o form ainda

        r2 = c.get("/workspace/chamados/c1?excluir=1")
        assert "Confirmar exclusão" in r2.text
        assert 'action="/workspace/chamados/c1/excluir"' in r2.text


def test_excluir_chamado_registra_e_redireciona_para_fila():
    repo = FakeRepo()
    with ws_client(repo) as c:
        t = _csrf(c)
        r = c.post("/workspace/chamados/c1/excluir", headers={"X-CSRF-Token": t},
                    follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/workspace"
    assert ("excluir", "c1") in repo.acoes


def test_excluir_chamado_com_origem_kanban_redireciona_para_kanban():
    repo = FakeRepo()
    with ws_client(repo) as c:
        t = _csrf(c)
        r = c.post("/workspace/chamados/c1/excluir?origem=kanban", headers={"X-CSRF-Token": t},
                    follow_redirects=False)
    assert r.headers["location"] == "/workspace/kanban"


# --------------------------------------------------------------------------
# Fase 3: iniciar atendimento, escopo de operadores, repasse de departamento
# --------------------------------------------------------------------------
def test_botao_iniciar_visivel_quando_novo():
    with ws_client(FakeRepo(status="NOVO")) as c:
        r = c.get("/workspace/chamados/c1")
    assert r.status_code == 200
    assert "Iniciar atendimento" in r.text
    assert 'action="/workspace/chamados/c1/iniciar"' in r.text


def test_botao_iniciar_oculto_quando_em_atendimento():
    with ws_client(FakeRepo(status="EM_ATENDIMENTO")) as c:
        r = c.get("/workspace/chamados/c1")
    assert r.status_code == 200
    assert "Iniciar atendimento" not in r.text


def test_iniciar_atendimento_assume_como_responsavel():
    repo = FakeRepo(status="NOVO")
    with ws_client(repo) as c:
        t = _csrf(c)
        r = c.post("/workspace/chamados/c1/iniciar", headers={"X-CSRF-Token": t},
                   follow_redirects=False)
    assert r.status_code == 303
    assert ("iniciar", "c1", OP) in repo.acoes


def test_operadores_escopados_ao_departamento_do_chamado():
    repo = FakeRepo()
    with ws_client(repo) as c:
        c.get("/workspace/chamados/c1")
    # o chamado é do setor "d1" -> a lista de responsáveis é filtrada por ele
    assert repo.operadores_dep == "d1"


def test_ti_ve_opcao_de_repasse():
    with ws_client(FakeRepo(is_ti=True)) as c:
        r = c.get("/workspace/chamados/c1")
    assert "Repassar para outro departamento" in r.text
    assert 'action="/workspace/chamados/c1/transferir"' in r.text


def test_nao_ti_nao_ve_repasse():
    with ws_client(FakeRepo(is_ti=False)) as c:
        r = c.get("/workspace/chamados/c1")
    assert "Repassar para outro departamento" not in r.text


def test_ti_transfere_departamento():
    repo = FakeRepo(is_ti=True)
    with ws_client(repo) as c:
        t = _csrf(c)
        c.post("/workspace/chamados/c1/transferir", data={"departamento_id": "d2"},
               headers={"X-CSRF-Token": t}, follow_redirects=False)
    assert ("transferir", "c1", "d2") in repo.acoes


def test_nao_ti_nao_transfere_mesmo_via_post():
    repo = FakeRepo(is_ti=False)
    with ws_client(repo) as c:
        t = _csrf(c)
        c.post("/workspace/chamados/c1/transferir", data={"departamento_id": "d2"},
               headers={"X-CSRF-Token": t}, follow_redirects=False)
    # o gate de UI + a checagem na rota impedem; a RLS reforçaria no banco
    assert not any(a[0] == "transferir" for a in repo.acoes)
