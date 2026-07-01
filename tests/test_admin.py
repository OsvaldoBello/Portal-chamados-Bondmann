"""Testes do Painel Admin (Fase 5) — gating por TI, KPIs, gestão e CSV."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone

from fastapi.testclient import TestClient

from app.auth.dependencies import CurrentUser, get_current_user
from app.main import app
from app.repositories.admin import get_admin_repo

TI = "77777777-7777-7777-7777-777777777777"


def _user(uid=TI, role="ADMIN"):
    return lambda: CurrentUser(id=uid, email="ti@bond.com", role=role,
                               claims={"sub": uid, "app_metadata": {"role": role}})


class FakeAdmin:
    def __init__(self, is_ti=True):
        self._ti = is_ti
        self.acoes = []

    async def is_ti(self, claims):
        return self._ti

    async def kpis(self, claims):
        return {"total": 10, "abertos": 4, "resolvidos": 6, "resolvidos_no_prazo": 5,
                "conformidade_sla": 83.3, "csat_media": 4.5, "csat_respostas": 4,
                "tma_horas": 12.0, "tma_seg": 43200}

    async def por_status(self, claims):
        return {"NOVO": 2, "EM_ATENDIMENTO": 2, "AGUARDANDO": 0, "RESOLVIDO": 6}

    async def csat_distribuicao(self, claims):
        return {1: 0, 2: 0, 3: 1, 4: 1, 5: 2}

    async def por_departamento(self, claims):
        return [{"departamento": "TI", "total": 7}, {"departamento": "RH", "total": 3}]

    async def produtividade(self, claims):
        return [{"operador": "Op TI", "resolvidos": 6, "atribuidos": 8}]

    async def departamentos(self, claims):
        return [{"id": "d1", "nome": "TI", "ativo": True}]

    async def categorias(self, claims):
        return [{"id": "c1", "nome": "Suporte", "descricao": None, "ativo": True}]

    async def planos(self, claims):
        return [{"nome": "Padrão Interno", "resposta_alta_min": 120, "resolucao_alta_min": 1440,
                 "resposta_default_min": 720, "resolucao_default_min": 1440, "ativo": True}]

    async def criar_departamento(self, claims, nome):
        self.acoes.append(("dep", nome))

    async def toggle_departamento(self, claims, dep_id):
        self.acoes.append(("dep_toggle", dep_id))

    async def criar_categoria(self, claims, nome, descricao):
        self.acoes.append(("cat", nome, descricao))

    async def toggle_categoria(self, claims, cat_id):
        self.acoes.append(("cat_toggle", cat_id))

    async def exportar(self, claims):
        return [{"codigo": "BOND-2026-00001", "titulo": "Impressora", "status": "RESOLVIDO",
                 "prioridade": "ALTA", "departamento": "TI", "categoria": "Suporte",
                 "solicitante": "Ana", "operador": "Op TI",
                 "created_at": datetime(2026, 7, 1, tzinfo=timezone.utc),
                 "limite_resolucao": None, "respondido_em": None, "resolvido_em": None,
                 "avaliacao_nota": 5}]


@contextmanager
def admin_client(repo, user=None):
    app.dependency_overrides[get_current_user] = user or _user()
    app.dependency_overrides[get_admin_repo] = lambda: repo
    try:
        with TestClient(app, base_url="https://testserver") as c:
            yield c
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_admin_repo, None)


def _csrf(c):
    c.get("/admin")
    return c.cookies.get("csrf_token")


def test_nao_ti_recebe_403():
    with admin_client(FakeAdmin(is_ti=False)) as c:
        assert c.get("/admin").status_code == 403
        assert c.get("/admin/gestao").status_code == 403
        assert c.get("/admin/export/csv").status_code == 403


def test_dashboard_mostra_kpis_e_dados_grafico():
    with admin_client(FakeAdmin()) as c:
        r = c.get("/admin")
    assert r.status_code == 200
    assert "83.3%" in r.text                 # conformidade SLA
    assert "4.5" in r.text                    # CSAT médio
    assert 'id="chart-data"' in r.text        # JSON inerte p/ Chart.js
    assert "/static/vendor/chart.umd.js" in r.text


def test_gestao_lista_catalogos():
    with admin_client(FakeAdmin()) as c:
        r = c.get("/admin/gestao")
    assert r.status_code == 200
    assert "Departamentos" in r.text and "Categorias" in r.text
    assert "Padrão Interno" in r.text


def test_criar_departamento():
    repo = FakeAdmin()
    with admin_client(repo) as c:
        t = _csrf(c)
        r = c.post("/admin/departamentos", data={"nome": "Financeiro"},
                   headers={"X-CSRF-Token": t}, follow_redirects=False)
    assert r.status_code == 303
    assert ("dep", "Financeiro") in repo.acoes


def test_export_csv():
    with admin_client(FakeAdmin()) as c:
        r = c.get("/admin/export/csv")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    assert "attachment" in r.headers["content-disposition"]
    assert "codigo,titulo,status" in r.text
    assert "BOND-2026-00001" in r.text
