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
        "cliente_nome": "Ana", "cliente_avatar_path": None, "operador_nome": None,
        "created_at": NOW - timedelta(hours=20), "limite_resolucao": NOW + timedelta(hours=1),
        "limite_resposta": None, "respondido_em": None, "resolvido_em": None,
        "avaliacao_nota": None, "avaliacao_comentario": None, "avaliacao_em": None,
    }
    base.update(extra)
    return base


class FakeRepo:
    def __init__(self, *, is_ti=True, status="NOVO", perfil_departamento_id="d1",
                 operador_id=None, cliente_id="aaa", observadores=None, departamento="TI"):
        self.acoes = []
        self._is_ti = is_ti
        self._status = status
        self._perfil_departamento_id = perfil_departamento_id
        self._operador_id = operador_id
        self._cliente_id = cliente_id
        self._observadores = observadores or []
        self._departamento = departamento
        self.operadores_dep = "__nao_chamado__"  # captura o filtro de departamento
        self.operadores_excluir = "__nao_chamado__"  # captura o excluir_id

    async def perfil(self, claims):
        return {"id": OP, "nome": "Op TI", "role": "OPERADOR", "empresa_id": "e1",
                "departamento_id": self._perfil_departamento_id, "departamento": self._departamento,
                "is_ti": self._is_ti}

    async def fila(self, claims, *, departamento_id=None, status=None, categoria_id=None,
                   prioridade=None, operador_id=None, setor=None, data_de=None, data_ate=None,
                   limite=200):
        self.fila_filtros = {  # captura os filtros aplicados
            "departamento_id": departamento_id, "categoria_id": categoria_id,
            "prioridade": prioridade, "operador_id": operador_id,
            "setor": setor, "data_de": data_de, "data_ate": data_ate,
        }
        cs = [
            _chamado(),
            _chamado(id="c2", codigo="BOND-2026-00002", status="EM_ATENDIMENTO"),
            _chamado(id="c3", codigo="BOND-2026-00003", status="AGUARDANDO_TERCEIROS"),
        ]
        return [c for c in cs if status is None or c["status"] == status]

    async def fila_stats(self, claims, *, departamento_id=None):
        return {"total": 3, "NOVO": 1, "EM_ATENDIMENTO": 1, "AGUARDANDO_TERCEIROS": 1,
                "AGUARDANDO": 0, "RESOLVIDO": 0}

    async def fila_assinatura(self, claims, *, departamento_id=None, status=None):
        return (2, NOW)

    async def categorias_ativas(self, claims, departamento_id=None):
        return [{"id": "cat1", "nome": "Suporte"}]

    async def setores_ativos(self, claims, departamento_id=None):
        return ["Comercial", "Financeiro"]

    async def obter(self, claims, cid):
        return _chamado(id=cid, status=self._status, operador_id=self._operador_id,
                         cliente_id=self._cliente_id)

    async def mensagens(self, claims, cid):
        return []

    async def observadores(self, claims, cid):
        return self._observadores

    async def operadores(self, claims, *, departamento_id=None, excluir_id=None):
        self.operadores_dep = departamento_id
        self.operadores_excluir = excluir_id
        return [{"id": OP, "nome": "Op TI", "departamento": "TI"}]

    async def departamentos_ativos(self, claims):
        return [
            {"id": "d1", "nome": "TI", "recebe_chamados": True},
            {"id": "d2", "nome": "RH", "recebe_chamados": True},
            {"id": "d3", "nome": "Marketing", "recebe_chamados": True},
        ]

    async def departamentos_destino_ativos(self, claims):
        return [d for d in await self.departamentos_ativos(claims) if d["recebe_chamados"]]

    async def alterar_status(self, claims, cid, novo):
        self.acoes.append(("status", cid, novo)); return {"id": cid, "status": novo}

    async def alterar_prioridade(self, claims, cid, nova):
        self.acoes.append(("prioridade", cid, nova)); return {"id": cid, "prioridade": nova}

    async def atribuir(self, claims, cid, operador_id):
        self.acoes.append(("atribuir", cid, operador_id)); return {"id": cid, "operador_id": operador_id}

    async def iniciar_atendimento(self, claims, cid, *, operador_id, novo_status="EM_ATENDIMENTO"):
        self.acoes.append(("iniciar", cid, operador_id))
        return {"id": cid, "status": novo_status, "operador_id": operador_id}

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


def test_kanban_marketing_tem_coluna_aguardando_terceiros_apos_em_andamento():
    """"Aguardando terceiros" (0043/0044) fica entre "Em andamento" e
    "Aguardando Validação" — chamado travado esperando um fornecedor
    externo, não o solicitante."""
    with ws_client(FakeRepo(departamento="Marketing")) as c:
        r = c.get("/workspace/kanban")
    assert r.status_code == 200
    ordem = ["Em andamento", "Aguardando terceiros", "Aguardando Validação", "Concluídos"]
    posicoes = [r.text.index(label) for label in ordem]
    assert posicoes == sorted(posicoes)
    assert 'data-status="AGUARDANDO_TERCEIROS"' in r.text
    assert "BOND-2026-00003" in r.text


def test_kanban_repassa_filtros_para_o_repo():
    from datetime import date

    repo = FakeRepo()
    with ws_client(repo) as c:
        r = c.get("/workspace/kanban", params={
            "categoria": "cat1", "prioridade": "alta", "operador": "op1",
            "setor": "Comercial", "data_de": "2026-07-01", "data_ate": "2026-07-31",
        })
    assert r.status_code == 200
    assert repo.fila_filtros["categoria_id"] == "cat1"
    assert repo.fila_filtros["prioridade"] == "ALTA"
    assert repo.fila_filtros["operador_id"] == "op1"
    assert repo.fila_filtros["setor"] == "Comercial"
    assert repo.fila_filtros["data_de"] == date(2026, 7, 1)
    assert repo.fila_filtros["data_ate"] == date(2026, 7, 31)
    # Formulário repopulado com os valores selecionados.
    assert 'value="2026-07-01"' in r.text
    assert 'value="2026-07-31"' in r.text
    assert "limpar filtros" in r.text


def test_kanban_sem_filtro_nao_mostra_link_de_limpar():
    with ws_client(FakeRepo()) as c:
        r = c.get("/workspace/kanban")
    assert r.status_code == 200
    assert "limpar filtros" not in r.text


def test_kanban_cartao_fora_do_setor_sem_drag_e_sem_excluir():
    """Sprint 1 / item 1.4 (M11): mesmo gate de permissão da tela de
    atendimento (dept_bate) aplicado à UI do Kanban — um cartão de outro
    departamento (ex.: líder de setor acompanhando via 0028) não pode ser
    arrastado nem excluído pela UI, mesmo que a RLS já bloqueasse no servidor."""

    class FakeRepoMultiDept(FakeRepo):
        async def fila(self, claims, **kw):
            return [
                _chamado(id="c1", codigo="BOND-2026-00001", departamento_id="d1"),
                _chamado(id="c2", codigo="BOND-2026-00002", departamento_id="d-outro", status="NOVO"),
            ]

    with ws_client(FakeRepoMultiDept(perfil_departamento_id="d1")) as c:
        r = c.get("/workspace/kanban")
    assert r.status_code == 200

    def _cartao(chamado_id: str) -> str:
        marcador = f'data-id="{chamado_id}"'
        inicio = r.text.rindex("<article", 0, r.text.index(marcador))
        fim = r.text.index("</article>", inicio)
        return r.text[inicio:fim]

    # Cartão do próprio setor (d1): arrastável e com botão de excluir.
    cartao_c1 = _cartao("c1")
    assert "kanban-card-locked" not in cartao_c1
    assert 'data-id="c1" data-codigo="BOND-2026-00001"' in cartao_c1

    # Cartão de outro setor (d-outro): sem drag (classe de bloqueio) e sem lixeira.
    cartao_c2 = _cartao("c2")
    assert "kanban-card-locked" in cartao_c2
    assert 'data-codigo="BOND-2026-00002"' not in cartao_c2


def test_mudar_status_registra_e_redireciona():
    """Chamado já assumido (``iniciar_atendimento`` não se aplica — no-op)
    só troca o status, mesmo pulando direto pra "Aguardando"."""
    async def _ja_assumido(claims, cid, *, operador_id, novo_status="EM_ATENDIMENTO"):
        return None

    repo = FakeRepo()
    repo.iniciar_atendimento = _ja_assumido
    with ws_client(repo) as c:
        t = _csrf(c)
        r = c.post("/workspace/chamados/c1/status", data={"novo_status": "AGUARDANDO"},
                   headers={"X-CSRF-Token": t}, follow_redirects=False)
    assert r.status_code == 303
    assert ("status", "c1", "AGUARDANDO") in repo.acoes


def test_mudar_status_para_em_atendimento_atribui_operador():
    """Arrastar para "Em atendimento" é "iniciar atendimento" — atribui o
    operador em vez de só trocar o rótulo da coluna (senão o chamado "andava"
    no Kanban sem que ninguém ficasse responsável por ele)."""
    repo = FakeRepo()
    with ws_client(repo) as c:
        t = _csrf(c)
        r = c.post("/workspace/chamados/c1/status", data={"novo_status": "EM_ATENDIMENTO"},
                   headers={"X-CSRF-Token": t}, follow_redirects=False)
    assert r.status_code == 303
    assert ("iniciar", "c1", OP) in repo.acoes
    assert not any(a[0] == "status" for a in repo.acoes)


def test_mudar_status_pula_em_atendimento_tambem_atribui_operador():
    """Arrastar direto de "A Fazer"/"Novo" pra "Aguardando" (pulando "Em
    andamento") também é uma primeira atribuição — bug real (BOND-2026-00035/
    00038): esse pulo só trocava o status e o chamado ficava sem operador
    para sempre (``iniciar_atendimento`` só reage a ``NOVO``/``A_FAZER``, e
    depois desse pulo o chamado nunca mais volta pra lá)."""
    repo = FakeRepo()
    with ws_client(repo) as c:
        t = _csrf(c)
        r = c.post("/workspace/chamados/c1/status", data={"novo_status": "AGUARDANDO"},
                   headers={"X-CSRF-Token": t}, follow_redirects=False)
    assert r.status_code == 303
    assert ("iniciar", "c1", OP) in repo.acoes
    assert not any(a[0] == "status" for a in repo.acoes)


def test_mudar_status_para_em_atendimento_cai_no_fallback_se_ja_assumido():
    """Quando o chamado já tem operador (ex.: voltando de "Aguardando"),
    ``iniciar_atendimento`` não se aplica mais — cai na troca simples de
    status, preservando o operador já atribuído."""
    async def _ja_assumido(claims, cid, *, operador_id, novo_status="EM_ATENDIMENTO"):
        return None

    repo = FakeRepo()
    repo.iniciar_atendimento = _ja_assumido
    with ws_client(repo) as c:
        t = _csrf(c)
        r = c.post("/workspace/chamados/c1/status", data={"novo_status": "EM_ATENDIMENTO"},
                   headers={"X-CSRF-Token": t}, follow_redirects=False)
    assert r.status_code == 303
    assert ("status", "c1", "EM_ATENDIMENTO") in repo.acoes


def test_mudar_status_via_kanban_drag_retorna_json():
    """O drag do Kanban manda o header X-Kanban-Drag e espera um JSON com o
    resultado (para desfazer o arraste na tela se o servidor não aplicou a
    mudança), em vez do redirect usado pelo form clássico da tela de detalhe."""
    repo = FakeRepo()
    with ws_client(repo) as c:
        t = _csrf(c)
        r = c.post(
            "/workspace/chamados/c1/status",
            data={"novo_status": "EM_ATENDIMENTO"},
            headers={"X-CSRF-Token": t, "X-Kanban-Drag": "1"},
            follow_redirects=False,
        )
    assert r.status_code == 200
    assert r.json() == {"ok": True}


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
    # Ações de staff (composer, status, prioridade, atribuir) só aparecem depois
    # que alguém (que não o autor) iniciou o atendimento.
    with ws_client(FakeRepo(status="EM_ATENDIMENTO", operador_id=OP)) as c:
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
    with ws_client(FakeRepo(status="EM_ATENDIMENTO", operador_id=OP)) as c:
        r = c.get("/workspace/chamados/c1")
    assert r.status_code == 200
    assert "Iniciar atendimento" not in r.text


def test_lider_de_outro_setor_so_acompanha_sem_atender():
    # Líder (ADMIN) de um setor diferente do destino do chamado (0028): vê o
    # chamado (RLS já cobre isso), mas a UI não deve oferecer ações de atendimento.
    repo = FakeRepo(is_ti=False, perfil_departamento_id="d-comercial")
    with ws_client(repo) as c:
        r = c.get("/workspace/chamados/c1")
    assert r.status_code == 200
    assert "acompanhando" in r.text.lower()
    assert "Iniciar atendimento" not in r.text
    assert 'action="/workspace/chamados/c1/status"' not in r.text
    assert 'action="/workspace/chamados/c1/mensagens' not in r.text


def test_iniciar_atendimento_assume_como_responsavel():
    repo = FakeRepo(status="NOVO")
    with ws_client(repo) as c:
        t = _csrf(c)
        r = c.post("/workspace/chamados/c1/iniciar", headers={"X-CSRF-Token": t},
                   follow_redirects=False)
    assert r.status_code == 303
    assert ("iniciar", "c1", OP) in repo.acoes


def test_operadores_escopados_ao_departamento_do_chamado_e_exclui_autor():
    repo = FakeRepo()
    with ws_client(repo) as c:
        c.get("/workspace/chamados/c1")
    # o chamado é do setor "d1" -> a lista de responsáveis é filtrada por ele,
    # e o autor ("aaa") é excluído da lista de atribuíveis.
    assert repo.operadores_dep == "d1"
    assert repo.operadores_excluir == "aaa"


def test_ti_ve_opcao_de_repasse():
    with ws_client(FakeRepo(is_ti=True, status="EM_ATENDIMENTO", operador_id=OP)) as c:
        r = c.get("/workspace/chamados/c1")
    assert "Repassar para outro departamento" in r.text
    assert 'action="/workspace/chamados/c1/transferir"' in r.text


def test_nao_ti_nao_ve_repasse():
    with ws_client(FakeRepo(is_ti=False, status="EM_ATENDIMENTO", operador_id=OP)) as c:
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


# --------------------------------------------------------------------------
# Segregação de função (2026-07-09): autor nunca atende o próprio chamado; o
# setor só responde/altera depois que ALGUÉM (que não o autor) assumir.
# --------------------------------------------------------------------------
def test_autor_do_chamado_nao_ve_acoes_de_staff_no_proprio_chamado():
    # O viewer (OP) é o próprio autor do chamado, mesmo sendo staff do setor.
    repo = FakeRepo(cliente_id=OP, status="NOVO")
    with ws_client(repo) as c:
        r = c.get("/workspace/chamados/c1")
    assert r.status_code == 200
    assert "Meus chamados" in r.text
    assert "Iniciar atendimento" not in r.text
    assert 'action="/workspace/chamados/c1/status"' not in r.text
    assert 'action="/workspace/chamados/c1/mensagens' not in r.text
    assert 'id="composer"' not in r.text


def test_autor_nao_ve_botao_iniciar_mesmo_chamado_novo_do_proprio_setor():
    repo = FakeRepo(cliente_id=OP, status="NOVO", operador_id=None)
    with ws_client(repo) as c:
        r = c.get("/workspace/chamados/c1")
    assert "Iniciar atendimento" not in r.text


def test_colega_do_setor_ve_aviso_de_nao_assumido_e_sem_composer():
    # Chamado do próprio setor, ainda não assumido, aberto por outra pessoa
    # (não o viewer): mostra "Iniciar atendimento", mas não o composer.
    repo = FakeRepo(cliente_id="aaa", status="NOVO", operador_id=None)
    with ws_client(repo) as c:
        r = c.get("/workspace/chamados/c1")
    assert "ainda não foi assumido" in r.text.lower()
    assert "Iniciar atendimento" in r.text
    assert 'id="composer"' not in r.text
    assert 'action="/workspace/chamados/c1/status"' not in r.text


def test_colega_do_setor_responde_apos_alguem_assumir():
    repo = FakeRepo(cliente_id="aaa", status="EM_ATENDIMENTO", operador_id=OP)
    with ws_client(repo) as c:
        r = c.get("/workspace/chamados/c1")
    assert 'id="composer"' in r.text
    assert 'action="/workspace/chamados/c1/status"' in r.text
    assert "Iniciar atendimento" not in r.text


# --------------------------------------------------------------------------
# Fase 1 (2026-07-09): Fila/Kanban só "de fora"; nova aba Chamados do Depto;
# Meus chamados elevado ao menu principal do workspace.
# --------------------------------------------------------------------------
def test_fila_e_kanban_passam_o_departamento_do_perfil():
    repo = FakeRepo()
    with ws_client(repo) as c:
        c.get("/workspace")
    assert repo.fila_filtros["departamento_id"] == "d1"

    repo2 = FakeRepo()
    with ws_client(repo2) as c:
        c.get("/workspace/kanban")
    assert repo2.fila_filtros["departamento_id"] == "d1"


def test_nav_do_workspace_tem_meus_chamados_sem_aba_separada_de_departamento():
    # Unificação (2026-07-09): "Chamados do Departamento" deixou de ser um item
    # de menu à parte — vira uma seção dentro de "Meus chamados" (/portal),
    # visível só pro líder de setor (ver tests/test_portal.py).
    with ws_client(FakeRepo()) as c:
        r = c.get("/workspace")
    assert 'href="/portal"' in r.text
    assert "Meus chamados" in r.text
    assert 'href="/workspace/departamento"' not in r.text


def test_meu_perfil_sai_da_barra_lateral_e_vai_pro_menu_do_usuario():
    # "Meu perfil" deixa de ser item da barra lateral e passa a viver no menu
    # que abre ao clicar no avatar/nome do usuário, no topo à direita.
    with ws_client(FakeRepo()) as c:
        r = c.get("/workspace")
    assert 'data-menu="user"' in r.text
    assert 'href="/perfil"' in r.text


def test_sem_avatar_nao_quebra_a_fila():
    # _chamado() default tem cliente_avatar_path=None — não deve gerar <img> nem erro.
    with ws_client(FakeRepo()) as c:
        r = c.get("/workspace")
    assert r.status_code == 200


# --------------------------------------------------------------------------
# Fase 8 (2026-07-09): observadores ("em cópia") — só leitura no Workspace.
# --------------------------------------------------------------------------
def test_atendimento_mostra_observadores_em_copia():
    repo = FakeRepo(observadores=[{"perfil_id": "u9", "nome": "Zeca Financeiro", "departamento": "Financeiro"}])
    with ws_client(repo) as c:
        r = c.get("/workspace/chamados/c1")
    assert r.status_code == 200
    assert "Em cópia" in r.text
    assert "Zeca Financeiro" in r.text
    assert 'href="/portal/chamados/c1"' in r.text


def test_atendimento_sem_observadores_nao_mostra_secao():
    with ws_client(FakeRepo(observadores=[])) as c:
        r = c.get("/workspace/chamados/c1")
    assert "Em cópia" not in r.text
