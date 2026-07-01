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

    def __init__(self, *, chamado=None, categorias=None, departamentos=None):
        self._chamado = chamado
        self._categorias = categorias or [{"id": "c1", "nome": "Logística / Entrega"}]
        self._departamentos = departamentos or [
            {"id": "d1", "nome": "TI"},
            {"id": "d2", "nome": "RH"},
            {"id": "d3", "nome": "Marketing"},
        ]
        self.avaliacoes: list[dict] = []
        self.criados: list[dict] = []

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

    async def categorias_ativas(self, claims):
        return self._categorias

    async def departamentos_ativos(self, claims):
        return self._departamentos

    async def criar(self, claims, **kwargs):
        self.criados.append(kwargs)
        return {"id": "novo-id", "codigo": "BOND-2026-00002"}

    async def avaliar(self, claims, chamado_id, *, nota, comentario):
        self.avaliacoes.append({"nota": nota, "comentario": comentario})
        return {
            "id": chamado_id, "avaliacao_nota": nota, "avaliacao_comentario": comentario,
            "avaliacao_em": datetime(2026, 6, 30, 15, 0, tzinfo=timezone.utc),
        }

    async def adicionar_mensagem(self, claims, chamado_id, *, remetente_id, conteudo):
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


def test_criar_com_departamento_redireciona_e_repassa_destino():
    repo = FakeRepo()
    with portal_client(repo) as client:
        token = _csrf_token(client)
        resp = client.post(
            "/portal/chamados",
            data={
                "titulo": "Acesso ao sistema de RH",
                "descricao": "Preciso de acesso",
                "departamento_id": "d2",
            },
            headers={"X-CSRF-Token": token},
            follow_redirects=False,
        )
    assert resp.status_code == 303
    assert len(repo.criados) == 1
    assert repo.criados[0]["departamento_id"] == "d2"


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
