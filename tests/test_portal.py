"""Testes do Portal do Cliente (Fase 3) — sem banco vivo.

As dependências de autenticação e de repositório são sobrepostas por fakes,
exercitando rotas, templates e a regra de avaliação (CSAT 1–5) de forma
determinística. O isolamento real de RLS é coberto pelos testes de integração
contra o Supabase local (Seção 4.2).
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app.auth.dependencies import CurrentUser, get_current_user
from app.main import app
from app.repositories.chamados import get_chamados_repo, validar_nota

UID = "11111111-1111-1111-1111-111111111111"
EMPRESA = "22222222-2222-2222-2222-222222222222"


def _cliente() -> CurrentUser:
    return CurrentUser(
        id=UID,
        email="cliente@empresa.com",
        role="CLIENTE",
        claims={"sub": UID, "app_metadata": {"role": "CLIENTE"}},
    )


class FakeRepo:
    """Implementa a superfície usada pelas rotas do portal."""

    def __init__(self, *, chamado=None, categorias=None, departamentos=None, subcategorias=None):
        self._chamado = chamado
        self._categorias = categorias or [{"id": "c1", "nome": "Logística / Entrega"}]
        # Catálogo unificado (0027): setores que recebem chamado (têm fila) +
        # setores que só abrem (ex.: Financeiro), como no seed real.
        self._departamentos = departamentos or [
            {"id": "d1", "nome": "TI", "recebe_chamados": True},
            {"id": "d2", "nome": "RH", "recebe_chamados": True},
            {"id": "d3", "nome": "Marketing", "recebe_chamados": True},
            {"id": "d4", "nome": "Financeiro", "recebe_chamados": False},
        ]
        # subcategorias por categoria_id
        self._subcategorias = subcategorias or {
            "c1": [{"id": "s1", "nome": "Sub A"}, {"id": "s2", "nome": "Sub B"}]
        }
        self.avaliacoes: list[dict] = []
        self.criados: list[dict] = []
        self.mensagens_criadas: list[dict] = []

    async def perfil(self, claims):
        return {"id": UID, "nome": "Cliente Teste", "role": "CLIENTE", "empresa_id": EMPRESA}

    async def listar(self, claims, limite=100):
        return [
            {
                "id": "aaa", "codigo": "BOND-2026-00001", "titulo": "Vazamento na linha 3",
                "status": "NOVO", "prioridade": "ALTA",
                "created_at": datetime(2026, 6, 30, 12, 0, tzinfo=timezone.utc),
                "limite_resolucao": None, "avaliacao_nota": None, "categoria": "Logística / Entrega",
            }
        ]

    async def stats(self, claims):
        return {"total": 1, "novo": 1, "em_atendimento": 0, "aguardando": 0, "resolvido": 0}

    async def obter(self, claims, chamado_id):
        return self._chamado

    async def mensagens(self, claims, chamado_id):
        return []

    async def categorias_ativas(self, claims, departamento_id=None):
        return self._categorias

    async def categoria_valida(self, claims, *, categoria_id, departamento_id):
        return any(str(c["id"]) == str(categoria_id) for c in self._categorias)

    async def departamentos_ativos(self, claims):
        return self._departamentos

    async def subcategorias_ativas(self, claims, categoria_id):
        return self._subcategorias.get(categoria_id, [])

    async def subcategoria_valida(self, claims, *, categoria_id, subcategoria_id):
        return any(
            str(s["id"]) == str(subcategoria_id)
            for s in self._subcategorias.get(categoria_id, [])
        )

    async def criar(self, claims, **kwargs):
        self.criados.append(kwargs)
        return {"id": "novo-id", "codigo": "BOND-2026-00002"}

    async def avaliar(self, claims, chamado_id, *, nota, comentario):
        self.avaliacoes.append({"nota": nota, "comentario": comentario})
        return {
            "id": chamado_id, "avaliacao_nota": nota, "avaliacao_comentario": comentario,
            "avaliacao_em": datetime(2026, 6, 30, 15, 0, tzinfo=timezone.utc),
        }

    async def adicionar_mensagem(self, claims, chamado_id, *, remetente_id, conteudo, anexos=None):
        self.mensagens_criadas.append(
            {"chamado_id": chamado_id, "conteudo": conteudo, "anexos": anexos or []}
        )
        return {"id": "m1", "created_at": datetime.now(timezone.utc)}


def _chamado(status="RESOLVIDO", cliente_id=UID, **extra):
    base = {
        "id": "aaa", "codigo": "BOND-2026-00001", "titulo": "Vazamento na linha 3",
        "descricao": "Detalhes...", "status": status, "prioridade": "ALTA",
        "cliente_id": cliente_id, "categoria": "Logística / Entrega",
        "cliente_nome": "Cliente Teste",
        "created_at": datetime(2026, 6, 30, 12, 0, tzinfo=timezone.utc),
        "limite_resposta": None, "limite_resolucao": None, "resolvido_em": None,
        "avaliacao_nota": None, "avaliacao_comentario": None, "avaliacao_em": None,
    }
    base.update(extra)
    return base


@contextmanager
def portal_client(repo: FakeRepo):
    """Cliente de teste com lifespan ativo (inicializa CSRF/JWT) e auth fake.

    Usa base_url https para que o cookie CSRF (``Secure``) seja reenviado pelo
    httpx — em http um cookie Secure não trafega, quebrando o double-submit.
    """
    app.dependency_overrides[get_current_user] = _cliente
    app.dependency_overrides[get_chamados_repo] = lambda: repo
    try:
        with TestClient(app, base_url="https://testserver") as client:
            yield client
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_chamados_repo, None)


# --------------------------------------------------------------------------
# Unidade: validação da nota
# --------------------------------------------------------------------------
@pytest.mark.parametrize("ok", [1, 2, 3, 4, 5, "4"])
def test_validar_nota_valida(ok):
    assert validar_nota(ok) == int(ok)


@pytest.mark.parametrize("ruim", [0, 6, -1, "x", None, "3.5", ""])
def test_validar_nota_invalida(ruim):
    with pytest.raises(ValueError):
        validar_nota(ruim)


# --------------------------------------------------------------------------
# Abertura: Categoria + Assunto, SEM "Produto"
# --------------------------------------------------------------------------
def test_form_novo_chamado_tem_departamento_categoria_assunto_sem_produto():
    repo = FakeRepo()
    with portal_client(repo) as client:
        resp = client.get("/portal/chamados/novo")
    assert resp.status_code == 200
    html = resp.text
    # Sistema interno: roteamento por departamento (TI/RH/Marketing).
    assert "Departamento" in html
    assert 'name="departamento_id"' in html
    assert "TI" in html and "RH" in html and "Marketing" in html
    assert "Categoria" in html
    assert "Assunto" in html
    # A dimensão "produto" foi removida da abertura (decisão de produto).
    assert "Produto relacionado" not in html
    assert "BD CLEAN" not in html
    assert 'name="categoria_id"' in html
    assert 'name="titulo"' in html


def _csrf_token(client):
    client.get("/portal/chamados/novo")
    return client.cookies.get("csrf_token")


def test_criar_sem_departamento_retorna_400():
    repo = FakeRepo()
    with portal_client(repo) as client:
        token = _csrf_token(client)
        resp = client.post(
            "/portal/chamados",
            data={"titulo": "Impressora quebrada", "descricao": "Não liga", "departamento_id": ""},
            headers={"X-CSRF-Token": token},
        )
    assert resp.status_code == 400
    assert "departamento" in resp.text.lower()
    assert repo.criados == []  # nada criado sem destino


def _abertura_valida(**over):
    """Payload de abertura com todos os campos obrigatórios preenchidos."""
    base = {
        "titulo": "Acesso ao sistema de RH",
        "descricao": "Preciso de acesso",
        "departamento_id": "d2",
        "categoria_id": "c1",
        "subcategoria_id": "s1",
        "prioridade": "MEDIA",
        "setor": "Financeiro",
    }
    base.update(over)
    return base


def test_criar_com_todos_campos_redireciona_e_repassa_destino():
    repo = FakeRepo()
    with portal_client(repo) as client:
        token = _csrf_token(client)
        resp = client.post(
            "/portal/chamados",
            data=_abertura_valida(),
            headers={"X-CSRF-Token": token},
            follow_redirects=False,
        )
    assert resp.status_code == 303
    assert len(repo.criados) == 1
    assert repo.criados[0]["departamento_id"] == "d2"
    assert repo.criados[0]["categoria_id"] == "c1"
    assert repo.criados[0]["subcategoria_id"] == "s1"
    assert repo.criados[0]["setor"] == "Financeiro"
    # Sem arquivos: nenhuma mensagem inicial de anexo.
    assert repo.mensagens_criadas == []


def test_criar_sem_setor_retorna_400():
    repo = FakeRepo()
    with portal_client(repo) as client:
        token = _csrf_token(client)
        data = _abertura_valida(setor="")
        resp = client.post(
            "/portal/chamados",
            data=data,
            headers={"X-CSRF-Token": token},
        )
    assert resp.status_code == 400
    assert "setor" in resp.text.lower()
    assert repo.criados == []


def test_criar_com_setor_invalido_retorna_400():
    repo = FakeRepo()
    with portal_client(repo) as client:
        token = _csrf_token(client)
        data = _abertura_valida(setor="Inexistente")
        resp = client.post(
            "/portal/chamados",
            data=data,
            headers={"X-CSRF-Token": token},
        )
    assert resp.status_code == 400
    assert "setor selecionado inválido" in resp.text.lower() or "inválido" in resp.text.lower()
    assert repo.criados == []



def test_criar_sem_categoria_retorna_400():
    repo = FakeRepo()
    with portal_client(repo) as client:
        token = _csrf_token(client)
        resp = client.post(
            "/portal/chamados",
            data=_abertura_valida(categoria_id="", subcategoria_id=""),
            headers={"X-CSRF-Token": token},
        )
    assert resp.status_code == 400
    assert "categoria" in resp.text.lower()
    assert repo.criados == []


def test_criar_sem_subcategoria_retorna_400():
    repo = FakeRepo()
    with portal_client(repo) as client:
        token = _csrf_token(client)
        resp = client.post(
            "/portal/chamados",
            data=_abertura_valida(subcategoria_id=""),
            headers={"X-CSRF-Token": token},
        )
    assert resp.status_code == 400
    assert "subcategoria" in resp.text.lower()
    assert repo.criados == []


def test_criar_subcategoria_de_outra_categoria_retorna_400():
    repo = FakeRepo()
    with portal_client(repo) as client:
        token = _csrf_token(client)
        resp = client.post(
            "/portal/chamados",
            # s99 não pertence à categoria c1 → defesa em profundidade
            data=_abertura_valida(subcategoria_id="s99"),
            headers={"X-CSRF-Token": token},
        )
    assert resp.status_code == 400
    assert repo.criados == []


def test_subcategorias_fragmento_lista_options_da_categoria():
    repo = FakeRepo()
    with portal_client(repo) as client:
        resp = client.get("/portal/chamados/subcategorias", params={"categoria_id": "c1"})
    assert resp.status_code == 200
    assert "Sub A" in resp.text and "Sub B" in resp.text
    assert 'value="s1"' in resp.text
    # É só o fragmento de <option>s (sem o layout completo).
    assert "Meus chamados" not in resp.text


def test_subcategorias_fragmento_sem_categoria_pede_escolha():
    repo = FakeRepo()
    with portal_client(repo) as client:
        resp = client.get("/portal/chamados/subcategorias")
    assert resp.status_code == 200
    assert "Escolha a categoria primeiro" in resp.text


def test_criar_com_anexo_tipo_invalido_retorna_422_e_nao_cria():
    """Anexo de tipo não permitido é barrado ANTES de criar o chamado
    (validação sem efeito colateral)."""
    repo = FakeRepo()
    with portal_client(repo) as client:
        token = _csrf_token(client)
        resp = client.post(
            "/portal/chamados",
            data=_abertura_valida(),
            files={"arquivos": ("notas.txt", b"conteudo qualquer", "text/plain")},
            headers={"X-CSRF-Token": token},
        )
    assert resp.status_code == 422
    assert repo.criados == []  # nenhum chamado criado por causa do anexo inválido


def test_mensagens_fragmento_renderiza_sem_layout():
    repo = FakeRepo(chamado=_chamado(status="EM_ATENDIMENTO"))
    with portal_client(repo) as client:
        resp = client.get("/portal/chamados/aaa/mensagens/fragmento")
    assert resp.status_code == 200
    # É só o fragmento da conversa (sem a sidebar do layout completo).
    assert "Ainda não há mensagens" in resp.text
    assert "Meus chamados" not in resp.text


def test_dashboard_lista_chamados():
    repo = FakeRepo()
    with portal_client(repo) as client:
        resp = client.get("/portal")
    assert resp.status_code == 200
    assert "BOND-2026-00001" in resp.text
    assert "Vazamento na linha 3" in resp.text


# --------------------------------------------------------------------------
# Avaliação 1–5: widget aparece só quando RESOLVIDO + autor
# --------------------------------------------------------------------------
def test_avaliacao_disponivel_quando_resolvido():
    repo = FakeRepo(chamado=_chamado(status="RESOLVIDO"))
    with portal_client(repo) as client:
        resp = client.get("/portal/chamados/aaa")
    assert resp.status_code == 200
    assert "Enviar avaliação" in resp.text
    assert 'name="nota"' in resp.text


def test_avaliacao_oculta_quando_nao_resolvido():
    repo = FakeRepo(chamado=_chamado(status="EM_ATENDIMENTO"))
    with portal_client(repo) as client:
        resp = client.get("/portal/chamados/aaa")
    assert resp.status_code == 200
    assert "Enviar avaliação" not in resp.text
    assert "assim que o chamado for" in resp.text


def test_avaliacao_ja_registrada_e_somente_leitura():
    chamado = _chamado(
        status="RESOLVIDO", avaliacao_nota=5, avaliacao_comentario="Excelente",
        avaliacao_em=datetime(2026, 6, 30, 15, 0, tzinfo=timezone.utc),
    )
    repo = FakeRepo(chamado=chamado)
    with portal_client(repo) as client:
        resp = client.get("/portal/chamados/aaa")
    assert resp.status_code == 200
    assert "5/5" in resp.text
    assert "Excelente" in resp.text
    assert "Enviar avaliação" not in resp.text


# --------------------------------------------------------------------------
# POST de avaliação (HTMX) — validação e persistência via fake
# --------------------------------------------------------------------------
def _csrf(client: TestClient) -> str:
    # Uma página qualquer emite o cookie CSRF assinado; reusamos como header.
    client.get("/portal/chamados/aaa")
    return client.cookies.get("csrf_token")


def test_post_avaliacao_nota_invalida_retorna_422():
    repo = FakeRepo(chamado=_chamado(status="RESOLVIDO"))
    with portal_client(repo) as client:
        token = _csrf(client)
        resp = client.post(
            "/portal/chamados/aaa/avaliacao",
            data={"nota": "9", "comentario": ""},
            headers={"X-CSRF-Token": token, "HX-Request": "true"},
        )
    assert resp.status_code == 422
    assert "intervalo" in resp.text.lower()
    assert repo.avaliacoes == []  # nada persistido


def test_post_avaliacao_valida_persiste_e_mostra_estrelas():
    repo = FakeRepo(chamado=_chamado(status="RESOLVIDO"))
    with portal_client(repo) as client:
        token = _csrf(client)
        resp = client.post(
            "/portal/chamados/aaa/avaliacao",
            data={"nota": "5", "comentario": "Muito bom"},
            headers={"X-CSRF-Token": token, "HX-Request": "true"},
        )
    assert resp.status_code == 200
    assert repo.avaliacoes == [{"nota": 5, "comentario": "Muito bom"}]
    assert "5/5" in resp.text


def test_post_avaliacao_nao_autor_e_bloqueada():
    # Chamado resolvido, mas de OUTRO cliente: a UI/route bloqueia (RLS reforça).
    repo = FakeRepo(chamado=_chamado(status="RESOLVIDO", cliente_id="outro-uid"))
    with portal_client(repo) as client:
        token = _csrf(client)
        resp = client.post(
            "/portal/chamados/aaa/avaliacao",
            data={"nota": "5"},
            headers={"X-CSRF-Token": token, "HX-Request": "true"},
        )
    assert resp.status_code == 403
    assert repo.avaliacoes == []
