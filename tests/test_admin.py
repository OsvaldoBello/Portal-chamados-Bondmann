"""Testes do Painel Admin (Fase 5) — gating por TI, KPIs, gestão e CSV."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import date, datetime, timezone

from fastapi.testclient import TestClient

from app.auth.dependencies import CurrentUser, get_current_user
from app.main import app
from app.repositories.admin import get_admin_repo
from app.repositories.chamados import get_chamados_repo

TI = "77777777-7777-7777-7777-777777777777"


def _user(uid=TI, role="ADMIN"):
    return lambda: CurrentUser(id=uid, email="ti@bond.com", role=role,
                               claims={"sub": uid, "app_metadata": {"role": role}})


class FakePerfilRepo:
    """Fake de ChamadosRepo só com ``perfil`` — usado pelo admin_context para
    resolver acesso/escopo (TI vs Admin de setor vs operador)."""

    def __init__(self, *, is_ti=True, role="ADMIN", departamento="TI"):
        self._perfil = {
            "id": TI, "nome": "Fulano", "role": role,
            "departamento": departamento, "is_ti": is_ti, "empresa_id": "e1",
        }

    async def perfil(self, claims):
        return self._perfil


class FakeAdmin:
    def __init__(self, is_ti=True):
        self._ti = is_ti
        self.acoes = []
        self._papeis = {}  # user_id -> role, simula perfis.role para a releitura pós-promoção

    async def is_ti(self, claims):
        return self._ti

    async def kpis(self, claims, *, departamento_id=None, todos_setores=False):
        return {"total": 10, "abertos": 4, "resolvidos": 6, "resolvidos_no_prazo": 5,
                "conformidade_sla": 83.3, "csat_media": 4.5, "csat_respostas": 4,
                "tma_horas": 12.0, "tma_seg": 43200}

    async def por_status(self, claims, *, departamento_id=None, todos_setores=False):
        return {"NOVO": 2, "EM_ATENDIMENTO": 2, "AGUARDANDO": 0, "RESOLVIDO": 6}

    async def csat_distribuicao(self, claims, *, departamento_id=None, todos_setores=False):
        return {1: 0, 2: 0, 3: 1, 4: 1, 5: 2}

    async def por_departamento(self, claims, *, departamento_id=None, todos_setores=False):
        return [{"departamento": "TI", "total": 7}, {"departamento": "RH", "total": 3}]

    async def por_setor(self, claims, *, departamento_id=None, todos_setores=False):
        return [{"setor": "Financeiro", "total": 6}, {"setor": "Vendas", "total": 4}]

    async def produtividade(self, claims, *, departamento_id=None, todos_setores=False):
        return [{"operador": "Op TI", "resolvidos": 6, "atribuidos": 8}]

    async def avaliacoes_recentes(self, claims, *, limite=8, departamento_id=None, todos_setores=False):
        return [{"codigo": "BOND-2026-00001", "titulo": "Impressora", "nota": 5,
                 "comentario": "Ótimo atendimento", "solicitante": "Ana",
                 "em": datetime(2026, 7, 1, tzinfo=timezone.utc)}]

    async def departamentos(self, claims):
        # d2 = setor que só ABRE chamado (sem fila) — testa o líder sem atendimento (0028).
        return [
            {"id": "d1", "nome": "TI", "ativo": True, "recebe_chamados": True},
            {"id": "d2", "nome": "Comercial", "ativo": True, "recebe_chamados": False},
        ]

    async def categorias(self, claims):
        return [{"id": "c1", "nome": "Suporte", "descricao": None, "ativo": True}]

    async def planos(self, claims):
        return [{"id": "p1", "nome": "Padrão Interno",
                 "resposta_baixa_min": 480, "resposta_media_min": 240, "resposta_alta_min": 120,
                 "resolucao_baixa_min": 2880, "resolucao_media_min": 1440, "resolucao_alta_min": 1440,
                 "resposta_default_min": 720, "resolucao_default_min": 1440, "ativo": True}]

    async def atualizar_plano(self, claims, plano_id, *, campos):
        self.acoes.append(("plano", plano_id, campos))

    async def recalcular_prioridade_marketing(self, claims):
        self.acoes.append(("recalcular_prioridade_marketing",))
        return 3

    async def sincronizar_feriados(self, claims, feriados):
        self.acoes.append(("sincronizar_feriados", len(feriados)))
        return 7

    async def marketing_midia_regional(self, claims):
        return [{"id": "mr1", "mes": date(2026, 1, 1), "investimento": 1000.0,
                 "regioes": 10, "descontinuidades": 1, "aderencias": 1}]

    async def upsert_marketing_midia_regional(self, claims, *, mes, investimento,
                                                regioes, descontinuidades, aderencias):
        self.acoes.append(("midia", mes, investimento, regioes, descontinuidades, aderencias))

    async def usuarios(self, claims):
        return [{"id": "u1", "nome": "Rita Nunes", "role": "OPERADOR", "ativo": True,
                 "departamento": "RH", "departamento_id": "d1"}]

    async def atualizar_papel(self, claims, user_id, *, role, departamento_id):
        self.acoes.append(("papel", user_id, role, departamento_id))
        self._papeis[user_id] = role

    async def obter_papel(self, claims, user_id):
        """Simula ``perfis.role`` após a escrita — usado pela releitura pós-
        promoção (Sprint 1 / item 1.5, M12)."""
        return self._papeis.get(user_id)

    async def subcategorias(self, claims):
        return [{"id": "s1", "nome": "Acesso VPN", "ativo": True,
                 "categoria_id": "c1", "categoria": "Suporte"}]

    async def criar_subcategoria(self, claims, categoria_id, nome):
        self.acoes.append(("sub", categoria_id, nome))

    async def toggle_subcategoria(self, claims, sub_id):
        self.acoes.append(("sub_toggle", sub_id)); return "c1"

    async def criar_departamento(self, claims, nome, *, recebe_chamados=False):
        self.acoes.append(("dep", nome, recebe_chamados))

    async def toggle_departamento(self, claims, dep_id):
        self.acoes.append(("dep_toggle", dep_id))

    async def toggle_recebe_departamento(self, claims, dep_id):
        self.acoes.append(("dep_toggle_recebe", dep_id))

    async def criar_categoria(self, claims, nome, descricao):
        self.acoes.append(("cat", nome, descricao))

    async def toggle_categoria(self, claims, cat_id):
        self.acoes.append(("cat_toggle", cat_id))

    async def mkt_dashboard_data(self, claims):
        return {
            "monthly": [
                {
                    "label": "JAN/26",
                    "total": 5,
                    "concluidas": 4,
                    "em_andamento": 1,
                    "abertas": 0,
                    "volume": 8,
                    "mkt_orig": 2,
                    "sol_orig": 3,
                    "tempo_soma": 6.0,
                    "tempo_qtd": 4,
                    "tempo_medio": 1.5,
                    "atrasos": 1,
                    "pct_conc": 80.0,
                    "pct_mkt": 40.0
                }
            ],
            "deptByMonth": {
                "JAN/26": {"RH": 3, "Marketing": 2}
            },
            "atrasosData": [
                {"nome": "Demanda A", "mes": "JAN/26", "dias": 6, "causa": "Sem causa registrada"}
            ],
            "midia": {
                "meses": ["Jan"],
                "investimento": [1000.0],
                "regioes": [10],
                "descontinuidades": [1],
                "aderencias": [1]
            }
        }

    async def exportar(self, claims):
        return [{"codigo": "BOND-2026-00001", "titulo": "Impressora",
                 "descricao": "A impressora do 2º andar não liga; luz vermelha piscando.",
                 "status": "RESOLVIDO", "prioridade": "ALTA", "departamento": "TI",
                 "categoria": "Suporte", "subcategoria": "Hardware",
                 "solicitante": "Ana", "operador": "Op TI",
                 "created_at": datetime(2026, 7, 1, tzinfo=timezone.utc),
                 "limite_resolucao": None, "respondido_em": None, "resolvido_em": None,
                 "avaliacao_nota": 5, "avaliacao_em": datetime(2026, 7, 2, tzinfo=timezone.utc),
                 "avaliacao_comentario": "Resolveu rápido, obrigado"}]


@contextmanager
def admin_client(repo, user=None, perfil=None):
    app.dependency_overrides[get_current_user] = user or _user()
    app.dependency_overrides[get_admin_repo] = lambda: repo
    app.dependency_overrides[get_chamados_repo] = lambda: perfil or FakePerfilRepo()
    try:
        with TestClient(app, base_url="https://testserver") as c:
            yield c
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_admin_repo, None)
        app.dependency_overrides.pop(get_chamados_repo, None)


def _csrf(c):
    c.get("/admin")
    return c.cookies.get("csrf_token")


def test_operador_recebe_403():
    # Operador (sem ADMIN) não acessa o painel de relatórios.
    perfil = FakePerfilRepo(is_ti=False, role="OPERADOR", departamento="RH")
    with admin_client(FakeAdmin(is_ti=False), user=_user(role="OPERADOR"), perfil=perfil) as c:
        assert c.get("/admin").status_code == 403
        assert c.get("/admin/gestao").status_code == 403
        assert c.get("/admin/export/csv").status_code == 403


def test_admin_de_setor_ve_dashboard_mas_nao_gere_catalogos():
    # ADMIN do RH (não-TI): vê os indicadores do seu setor, mas gestão é só do TI.
    perfil = FakePerfilRepo(is_ti=False, role="ADMIN", departamento="RH")
    with admin_client(FakeAdmin(is_ti=False), user=_user(role="ADMIN"), perfil=perfil) as c:
        r = c.get("/admin")
        assert r.status_code == 200
        assert "RH" in r.text                       # escopo do setor no painel
        assert c.get("/admin/export/csv").status_code == 200  # export escopado por RLS
        assert c.get("/admin/gestao").status_code == 403      # gestão de catálogos = TI
        t = _csrf(c)
        # POST de gestão também barra o admin de setor.
        assert c.post("/admin/departamentos", data={"nome": "X"},
                      headers={"X-CSRF-Token": t}, follow_redirects=False).status_code == 403


def test_dashboard_mostra_kpis_e_dados_grafico():
    with admin_client(FakeAdmin()) as c:
        r = c.get("/admin")
    assert r.status_code == 200
    assert "83.3%" in r.text                 # conformidade SLA
    assert "4.5" in r.text                    # CSAT médio
    assert 'id="chart-data"' in r.text        # JSON inerte p/ Chart.js
    assert "/static/vendor/chart.umd.js" in r.text


def test_ti_dashboard_nao_tem_seletor_e_mostra_so_o_proprio_setor():
    # Decisão de produto 2026-07-09: TI deixou de ver "Todos os setores"/outros
    # departamentos nos indicadores — é escopado ao próprio setor (TI), igual a
    # qualquer Admin de departamento.
    with admin_client(FakeAdmin()) as c:
        r = c.get("/admin")
    assert r.status_code == 200
    assert 'name="departamento"' not in r.text
    assert "Todos os setores" not in r.text
    assert "Indicadores de: <strong>TI</strong>" in r.text


def test_ti_dashboard_ignora_tentativa_de_filtrar_outro_departamento():
    # Mesmo que alguém force a querystring antiga, não há mais seletor/filtro:
    # o TI só vê o próprio setor.
    with admin_client(FakeAdmin()) as c:
        r = c.get("/admin?departamento=d2")
    assert r.status_code == 200
    assert "Indicadores de: <strong>TI</strong>" in r.text


def test_funcionario_do_ti_sem_papel_de_staff_recebe_403():
    # perfil.is_ti é só "departamento = TI" (não olha papel) — um CLIENTE
    # (funcionário comum) do setor TI não deve entrar no painel admin só por
    # pertencer ao departamento.
    perfil = FakePerfilRepo(is_ti=True, role="CLIENTE", departamento="TI")
    with admin_client(FakeAdmin(), user=_user(role="CLIENTE"), perfil=perfil) as c:
        assert c.get("/admin").status_code == 403


def test_admin_de_setor_nao_tem_seletor():
    # Admin de setor é sempre escopado pela RLS ao seu departamento — sem seletor.
    perfil = FakePerfilRepo(is_ti=False, role="ADMIN", departamento="RH")
    with admin_client(FakeAdmin(is_ti=False), user=_user(role="ADMIN"), perfil=perfil) as c:
        r = c.get("/admin")
    assert r.status_code == 200
    assert 'name="departamento"' not in r.text


def test_recalcular_prioridade_marketing_so_ti():
    repo = FakeAdmin()
    with admin_client(repo) as c:
        t = _csrf(c)
        r = c.post("/admin/jobs/recalcular-prioridade-marketing",
                   headers={"X-CSRF-Token": t}, follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/admin/gestao?prioridade_ok=3"
    assert ("recalcular_prioridade_marketing",) in repo.acoes


def test_recalcular_prioridade_marketing_restrito_ao_ti():
    perfil = FakePerfilRepo(is_ti=False, role="ADMIN", departamento="RH")
    with admin_client(FakeAdmin(is_ti=False), user=_user(role="ADMIN"), perfil=perfil) as c:
        t = _csrf(c)
        r = c.post("/admin/jobs/recalcular-prioridade-marketing", headers={"X-CSRF-Token": t})
    assert r.status_code == 403


def test_gestao_mostra_botao_de_recalcular_prioridade():
    with admin_client(FakeAdmin()) as c:
        r = c.get("/admin/gestao?prioridade_ok=5")
    assert r.status_code == 200
    assert 'action="/admin/jobs/recalcular-prioridade-marketing"' in r.text
    assert "5 chamado(s)" in r.text


def test_sincronizar_feriados_so_ti():
    repo = FakeAdmin()
    with admin_client(repo) as c:
        t = _csrf(c)
        r = c.post("/admin/jobs/sincronizar-feriados",
                   headers={"X-CSRF-Token": t}, follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/admin/gestao?feriados_ok=7"
    assert any(a[0] == "sincronizar_feriados" and a[1] > 0 for a in repo.acoes)


def test_sincronizar_feriados_restrito_ao_ti():
    perfil = FakePerfilRepo(is_ti=False, role="ADMIN", departamento="RH")
    with admin_client(FakeAdmin(is_ti=False), user=_user(role="ADMIN"), perfil=perfil) as c:
        t = _csrf(c)
        r = c.post("/admin/jobs/sincronizar-feriados", headers={"X-CSRF-Token": t})
    assert r.status_code == 403


def test_gestao_mostra_botao_de_sincronizar_feriados():
    with admin_client(FakeAdmin()) as c:
        r = c.get("/admin/gestao?feriados_ok=10")
    assert r.status_code == 200
    assert 'action="/admin/jobs/sincronizar-feriados"' in r.text
    assert "10 feriado(s)" in r.text


def test_dashboard_marketing_renderiza_mkt_data():
    # Admin do Marketing (ou TI cujo próprio setor é Marketing) cai no template
    # dedicado (dashboard_marketing.html), que serializa mkt_dashboard_data() —
    # cobre o caminho de views/CRUD reescrito na Fase 6.
    perfil = FakePerfilRepo(is_ti=False, role="ADMIN", departamento="Marketing")
    with admin_client(FakeAdmin(is_ti=False), user=_user(role="ADMIN"), perfil=perfil) as c:
        r = c.get("/admin")
    assert r.status_code == 200
    assert 'id="mkt-data"' in r.text
    assert "JAN/26" in r.text


def test_gestao_mostra_crud_de_midia_regional():
    with admin_client(FakeAdmin()) as c:
        r = c.get("/admin/gestao")
    assert r.status_code == 200
    assert 'action="/admin/marketing-midia"' in r.text
    assert 'name="mes"' in r.text
    assert "01/2026" in r.text  # linha existente formatada mm/yyyy


def test_salvar_marketing_midia_cria_mes_novo():
    repo = FakeAdmin()
    with admin_client(repo) as c:
        t = _csrf(c)
        r = c.post("/admin/marketing-midia",
                   data={"mes": "2026-06", "investimento": "1234.50", "regioes": "12",
                         "descontinuidades": "2", "aderencias": "3"},
                   headers={"X-CSRF-Token": t}, follow_redirects=False)
    assert r.status_code == 303
    acao = next(a for a in repo.acoes if a[0] == "midia")
    assert acao[1] == date(2026, 6, 1)
    assert acao[2] == 1234.5
    assert acao[3] == 12 and acao[4] == 2 and acao[5] == 3


def test_salvar_marketing_midia_restrito_ao_ti():
    perfil = FakePerfilRepo(is_ti=False, role="ADMIN", departamento="RH")
    with admin_client(FakeAdmin(is_ti=False), user=_user(role="ADMIN"), perfil=perfil) as c:
        t = _csrf(c)
        r = c.post("/admin/marketing-midia",
                   data={"mes": "2026-06", "investimento": "1", "regioes": "1",
                         "descontinuidades": "0", "aderencias": "0"},
                   headers={"X-CSRF-Token": t})
    assert r.status_code == 403


def test_salvar_marketing_midia_mes_invalido_nao_quebra():
    repo = FakeAdmin()
    with admin_client(repo) as c:
        t = _csrf(c)
        r = c.post("/admin/marketing-midia", data={"mes": "lixo"},
                   headers={"X-CSRF-Token": t}, follow_redirects=False)
    assert r.status_code == 303
    assert not any(a[0] == "midia" for a in repo.acoes)


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
    assert ("dep", "Financeiro", False) in repo.acoes


def test_gestao_edita_plano_sla():
    repo = FakeAdmin()
    with admin_client(repo) as c:
        r = c.get("/admin/gestao")
        assert 'action="/admin/planos/p1"' in r.text   # form editável do plano
        assert 'name="resposta_alta_min"' in r.text
        t = _csrf(c)
        resp = c.post("/admin/planos/p1",
                      data={"resposta_alta_min": "90", "resolucao_alta_min": "600",
                            "resposta_media_min": "", "resposta_baixa_min": "480",
                            "resolucao_media_min": "1440", "resolucao_baixa_min": "2880",
                            "resposta_default_min": "720", "resolucao_default_min": "1440"},
                      headers={"X-CSRF-Token": t}, follow_redirects=False)
    assert resp.status_code == 303
    plano = next(a for a in repo.acoes if a[0] == "plano")
    assert plano[1] == "p1"
    assert plano[2]["resposta_alta_min"] == 90        # int parseado
    assert plano[2]["resposta_media_min"] is None     # vazio → None (usa default)


def test_usuarios_lista_e_form_de_criar():
    with admin_client(FakeAdmin()) as c:
        r = c.get("/admin/usuarios")
    assert r.status_code == 200
    assert "Nova conta" in r.text
    assert "Rita Nunes" in r.text
    assert 'action="/admin/usuarios"' in r.text


def test_usuarios_restrito_ao_ti():
    perfil = FakePerfilRepo(is_ti=False, role="ADMIN", departamento="RH")
    with admin_client(FakeAdmin(is_ti=False), user=_user(role="ADMIN"), perfil=perfil) as c:
        assert c.get("/admin/usuarios").status_code == 403


def test_mudar_papel_grava_no_perfil():
    repo = FakeAdmin()
    with admin_client(repo) as c:
        t = _csrf(c)
        r = c.post("/admin/usuarios/u1/papel",
                   data={"papel": "ADMIN", "departamento_id": "d1"},
                   headers={"X-CSRF-Token": t}, follow_redirects=False)
    assert r.status_code == 303
    assert ("papel", "u1", "ADMIN", "d1") in repo.acoes


def test_mudar_papel_funcionario_exige_setor():
    # Setor de origem virou obrigatório pra qualquer papel, inclusive Funcionário (0028).
    repo = FakeAdmin()
    with admin_client(repo) as c:
        t = _csrf(c)
        r = c.post("/admin/usuarios/u1/papel", data={"papel": "CLIENTE", "departamento_id": ""},
                   headers={"X-CSRF-Token": t}, follow_redirects=False)
    assert r.status_code == 303
    assert not any(a[0] == "papel" for a in repo.acoes)


def test_mudar_papel_funcionario_setor_sem_fila_funciona():
    repo = FakeAdmin()
    with admin_client(repo) as c:
        t = _csrf(c)
        r = c.post("/admin/usuarios/u1/papel", data={"papel": "CLIENTE", "departamento_id": "d2"},
                   headers={"X-CSRF-Token": t}, follow_redirects=False)
    assert r.status_code == 303
    assert ("papel", "u1", "CLIENTE", "d2") in repo.acoes


def test_mudar_papel_lider_em_setor_sem_fila_funciona():
    # ADMIN de um setor que só abre chamado (Comercial) = líder que só acompanha,
    # sem fila pra atender.
    repo = FakeAdmin()
    with admin_client(repo) as c:
        t = _csrf(c)
        r = c.post("/admin/usuarios/u1/papel", data={"papel": "ADMIN", "departamento_id": "d2"},
                   headers={"X-CSRF-Token": t}, follow_redirects=False)
    assert r.status_code == 303
    assert ("papel", "u1", "ADMIN", "d2") in repo.acoes


def test_mudar_papel_operador_em_setor_sem_fila_da_erro():
    # OPERADOR só faz sentido num setor com fila — não há o que atender em Comercial.
    repo = FakeAdmin()
    with admin_client(repo) as c:
        t = _csrf(c)
        r = c.post("/admin/usuarios/u1/papel", data={"papel": "OPERADOR", "departamento_id": "d2"},
                   headers={"X-CSRF-Token": t}, follow_redirects=False)
    assert r.status_code == 303


def _query(location: str) -> dict:
    from urllib.parse import parse_qs, urlsplit

    return {k: v[0] for k, v in parse_qs(urlsplit(location).query).items()}


def test_mudar_papel_divergencia_no_banco_reporta_erro():
    """Sprint 1 / item 1.5 (M12): se a releitura de ``perfis.role`` não bate
    com o papel alvo (ex.: UPDATE afetou 0 linhas — user_id inexistente), a
    resposta é um erro explícito, não um "sucesso" silenciosamente incorreto."""

    class FakeAdminEscritaFalha(FakeAdmin):
        async def atualizar_papel(self, claims, user_id, *, role, departamento_id):
            self.acoes.append(("papel", user_id, role, departamento_id))
            # Simula a escrita não pegando (0 linhas afetadas): perfis.role
            # continua com o valor antigo.

    repo = FakeAdminEscritaFalha()
    with admin_client(repo) as c:
        t = _csrf(c)
        r = c.post("/admin/usuarios/u1/papel", data={"papel": "ADMIN", "departamento_id": "d1"},
                   headers={"X-CSRF-Token": t}, follow_redirects=False)
    assert r.status_code == 303
    assert "erro" in _query(r.headers["location"])


def test_mudar_papel_divergencia_no_jwt_avisa_sem_reportar_sucesso_puro():
    """JWT (app_metadata.role) diverge do papel gravado em ``perfis`` — o
    aviso explica a divergência em vez de dizer só "Papel atualizado"."""
    from unittest.mock import AsyncMock, patch

    admin_sdk = AsyncMock()
    admin_sdk.auth.admin.update_user_by_id = AsyncMock(return_value=None)
    admin_sdk.auth.admin.get_user_by_id = AsyncMock(
        return_value=type("R", (), {"user": type("U", (), {"app_metadata": {"role": "OPERADOR"}})()})()
    )

    repo = FakeAdmin()
    with patch("app.auth.supabase_client.ensure_admin_client", AsyncMock(return_value=admin_sdk)):
        with admin_client(repo) as c:
            t = _csrf(c)
            r = c.post("/admin/usuarios/u1/papel", data={"papel": "ADMIN", "departamento_id": "d1"},
                       headers={"X-CSRF-Token": t}, follow_redirects=False)
    assert r.status_code == 303
    ok = _query(r.headers["location"]).get("ok", "")
    assert "diferente" in ok or "verifique" in ok.lower()


def test_mudar_papel_sem_divergencia_confirma_sucesso():
    from unittest.mock import AsyncMock, patch

    admin_sdk = AsyncMock()
    admin_sdk.auth.admin.update_user_by_id = AsyncMock(return_value=None)
    admin_sdk.auth.admin.get_user_by_id = AsyncMock(
        return_value=type("R", (), {"user": type("U", (), {"app_metadata": {"role": "ADMIN"}})()})()
    )

    repo = FakeAdmin()
    with patch("app.auth.supabase_client.ensure_admin_client", AsyncMock(return_value=admin_sdk)):
        with admin_client(repo) as c:
            t = _csrf(c)
            r = c.post("/admin/usuarios/u1/papel", data={"papel": "ADMIN", "departamento_id": "d1"},
                       headers={"X-CSRF-Token": t}, follow_redirects=False)
    assert r.status_code == 303
    q = _query(r.headers["location"])
    assert "erro" not in q
    assert q.get("ok") == "Papel atualizado. A mudança vale no próximo login do usuário."


def test_criar_usuario_sem_service_role_avisa():
    # Sem service_role configurada, a criação degrada com mensagem (não quebra).
    repo = FakeAdmin()
    with admin_client(repo) as c:
        t = _csrf(c)
        r = c.post("/admin/usuarios",
                   data={"nome": "Novo", "email": "novo@bondmann.com.br",
                         "senha": "12345678", "papel": "ADMIN", "departamento_id": "d1"},
                   headers={"X-CSRF-Token": t}, follow_redirects=False)
    assert r.status_code == 303
    assert "/admin/usuarios" in r.headers["location"]
    assert not any(a[0] == "papel" for a in repo.acoes)   # não promoveu (conta não criada)


def test_gestao_mostra_subcategorias():
    with admin_client(FakeAdmin()) as c:
        r = c.get("/admin/gestao")
    assert r.status_code == 200
    assert "Subcategorias" in r.text
    assert 'action="/admin/subcategorias"' in r.text
    assert "Acesso VPN" in r.text


def test_criar_subcategoria():
    repo = FakeAdmin()
    with admin_client(repo) as c:
        t = _csrf(c)
        r = c.post("/admin/subcategorias", data={"nome": "Reset de senha", "categoria_id": "c1"},
                   headers={"X-CSRF-Token": t}, follow_redirects=False)
    assert r.status_code == 303
    assert ("sub", "c1", "Reset de senha") in repo.acoes


def test_criar_subcategoria_categoria_invalida_ignora():
    repo = FakeAdmin()
    with admin_client(repo) as c:
        t = _csrf(c)
        c.post("/admin/subcategorias", data={"nome": "X", "categoria_id": "inexistente"},
               headers={"X-CSRF-Token": t}, follow_redirects=False)
    assert not any(a[0] == "sub" for a in repo.acoes)


def test_toggle_subcategoria():
    repo = FakeAdmin()
    with admin_client(repo) as c:
        t = _csrf(c)
        c.post("/admin/subcategorias/s1/toggle", headers={"X-CSRF-Token": t}, follow_redirects=False)
    assert ("sub_toggle", "s1") in repo.acoes


def test_subcategorias_restrito_ao_ti():
    perfil = FakePerfilRepo(is_ti=False, role="ADMIN", departamento="RH")
    with admin_client(FakeAdmin(is_ti=False), user=_user(role="ADMIN"), perfil=perfil) as c:
        t = _csrf(c)
        assert c.post("/admin/subcategorias", data={"nome": "X", "categoria_id": "c1"},
                      headers={"X-CSRF-Token": t}).status_code == 403


def test_excluir_usuario_barra_autoexclusao():
    # TI não pode excluir a própria conta (o id do TI é o mesmo do _user()).
    with admin_client(FakeAdmin()) as c:
        t = _csrf(c)
        r = c.post(f"/admin/usuarios/{TI}/excluir", headers={"X-CSRF-Token": t},
                   follow_redirects=False)
    assert r.status_code == 303
    assert "pr%C3%B3pria+conta" in r.headers["location"] or "própria" in r.headers["location"]


def test_excluir_usuario_restrito_ao_ti():
    perfil = FakePerfilRepo(is_ti=False, role="ADMIN", departamento="RH")
    with admin_client(FakeAdmin(is_ti=False), user=_user(role="ADMIN"), perfil=perfil) as c:
        t = _csrf(c)
        assert c.post("/admin/usuarios/u1/excluir", headers={"X-CSRF-Token": t}).status_code == 403


def test_excluir_usuario_exige_confirmacao_em_duas_etapas():
    with admin_client(FakeAdmin()) as c:
        # Etapa 1: a lista mostra só o link de pedir confirmação, sem POST direto.
        r1 = c.get("/admin/usuarios")
        assert "/admin/usuarios?confirmar=u1" in r1.text
        assert 'action="/admin/usuarios/u1/excluir"' not in r1.text
        # Etapa 2: com ?confirmar=u1, aparece o botão de confirmar + o form de POST.
        r2 = c.get("/admin/usuarios?confirmar=u1")
        assert "Confirmar exclusão" in r2.text
        assert 'action="/admin/usuarios/u1/excluir"' in r2.text


class _FakeAdminAPI:
    def __init__(self):
        self.calls = []

    async def create_user(self, body):
        self.calls.append(("create", body))
        return type("R", (), {"user": type("U", (), {"id": "newid-123"})()})()

    async def update_user_by_id(self, uid, body):
        self.calls.append(("update", uid, body))

    async def delete_user(self, uid):
        self.calls.append(("delete", uid))


class _FakeSupaClient:
    def __init__(self):
        self.admin_api = _FakeAdminAPI()
        self.auth = type("A", (), {"admin": self.admin_api})()


def _patch_admin_client(monkeypatch, fake):
    import app.auth.supabase_client as sc

    async def _ensure():
        return fake

    monkeypatch.setattr(sc, "ensure_admin_client", _ensure)


def test_criar_usuario_funcionario_mostra_rotulo_amigavel(monkeypatch):
    # Bug reportado: funcionário aparecia como "CLIENTE"; deve dizer "Funcionário".
    fake = _FakeSupaClient()
    _patch_admin_client(monkeypatch, fake)
    with admin_client(FakeAdmin()) as c:
        t = _csrf(c)
        r = c.post("/admin/usuarios",
                   data={"nome": "Gabriel", "email": "gabriel@bondmann.com.br",
                         "senha": "12345678", "papel": "CLIENTE", "departamento_id": "d2"},
                   headers={"X-CSRF-Token": t}, follow_redirects=False)
    assert r.status_code == 303
    from urllib.parse import unquote
    loc = unquote(r.headers["location"])
    assert "Funcionário" in loc
    assert "CLIENTE" not in loc


def test_excluir_usuario_sucesso(monkeypatch):
    fake = _FakeSupaClient()
    _patch_admin_client(monkeypatch, fake)
    with admin_client(FakeAdmin()) as c:
        t = _csrf(c)
        r = c.post("/admin/usuarios/u1/excluir", headers={"X-CSRF-Token": t},
                   follow_redirects=False)
    assert r.status_code == 303
    assert ("delete", "u1") in fake.admin_api.calls


def test_export_csv():
    with admin_client(FakeAdmin()) as c:
        r = c.get("/admin/export/csv")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    assert "attachment" in r.headers["content-disposition"]
    # BOM UTF-8 (Excel pt-BR lê acentos sem virar mojibake) + ";" como
    # delimitador (padrão de planilha usado pela empresa — ver plano mestre).
    assert r.text.startswith("﻿")
    assert "Chamado;Título;Descrição;Status" in r.text
    assert "Comentário da avaliação" in r.text        # feedback no relatório do TI
    assert "Resolveu rápido, obrigado" in r.text
    assert "BOND-2026-00001" in r.text
    assert "A impressora do 2º andar não liga" in r.text   # descrição do chamado
    assert "Hardware" in r.text                             # subcategoria
