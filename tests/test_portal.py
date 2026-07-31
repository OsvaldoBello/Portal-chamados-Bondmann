"""Testes do Portal do Cliente (Fase 3) — sem banco vivo.

As dependências de autenticação e de repositório são sobrepostas por fakes,
exercitando rotas, templates e a regra de avaliação (CSAT 1–5) de forma
determinística. O isolamento real de RLS é coberto pelos testes de integração
contra o Supabase local (Seção 4.2).
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from app.auth.dependencies import CurrentUser, get_current_user
from app.main import app
from app.ratelimit import limiter
from app.repositories.chamados import (
    get_chamados_repo,
    validar_comentario_avaliacao,
    validar_nota,
    validar_telefone_contato,
)

UID = "11111111-1111-1111-1111-111111111111"
EMPRESA = "22222222-2222-2222-2222-222222222222"


@pytest.fixture(autouse=True)
def _sem_rate_limit():
    """Desliga o rate limit da abertura (@limiter.limit) durante os testes: o
    storage in-memory é por processo e compartilhado entre os casos, então a
    soma de POSTs do arquivo estouraria o teto de 15/min e causaria 429 espúrio.
    Nenhum teste aqui verifica rate limit (isso é coberto em test_ratelimit.py)."""
    anterior = limiter.enabled
    limiter.enabled = False
    try:
        yield
    finally:
        limiter.enabled = anterior


def _cliente() -> CurrentUser:
    return CurrentUser(
        id=UID,
        email="cliente@empresa.com",
        role="CLIENTE",
        claims={"sub": UID, "app_metadata": {"role": "CLIENTE"}},
    )


def _admin() -> CurrentUser:
    return CurrentUser(
        id=UID,
        email="lider@empresa.com",
        role="ADMIN",
        claims={"sub": UID, "app_metadata": {"role": "ADMIN"}},
    )


class FakeRepo:
    """Implementa a superfície usada pelas rotas do portal."""

    def __init__(self, *, chamado=None, categorias=None, departamentos=None, subcategorias=None,
                 role="CLIENTE", departamento_id=None, chamados_colegas=None, avaliacao_pendente=None,
                 telefone=""):
        self._chamado = chamado
        self._telefone = telefone
        self.telefones_salvos: list[str] = []
        self._avaliacao_pendente = avaliacao_pendente
        self._role = role
        self._departamento_id = departamento_id
        self._chamados_colegas = chamados_colegas if chamados_colegas is not None else []
        self.chamados_departamento_filtros = None
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
        self.reaberturas: list[str] = []
        self.criados: list[dict] = []
        self.mensagens_criadas: list[dict] = []
        self.observadores_adicionados: list[tuple] = []
        self.observadores_removidos: list[tuple] = []
        self._observadores_por_chamado: dict[str, list[dict]] = {}

    async def perfil(self, claims):
        return {
            "id": UID, "nome": "Cliente Teste", "role": self._role, "empresa_id": EMPRESA,
            "departamento_id": self._departamento_id, "telefone": self._telefone,
        }

    async def atualizar_telefone(self, claims, *, telefone):
        self.telefones_salvos.append(telefone)
        self._telefone = telefone

    async def chamados_departamento(self, claims, *, departamento_id=None, status=None,
                                     categoria_id=None, prioridade=None, limite=200):
        self.chamados_departamento_filtros = {"departamento_id": departamento_id}
        return self._chamados_colegas

    async def listar(self, claims, limite=100):
        return [
            {
                "id": "aaa", "codigo": "BOND-2026-00001", "titulo": "Vazamento na linha 3",
                "status": "NOVO", "prioridade": "ALTA",
                "created_at": datetime(2026, 6, 30, 12, 0, tzinfo=UTC),
                "limite_resolucao": None, "avaliacao_nota": None, "categoria": "Logística / Entrega",
            }
        ]

    async def stats(self, claims):
        return {"total": 1, "novo": 1, "em_atendimento": 0, "aguardando": 0, "resolvido": 0}

    async def obter(self, claims, chamado_id):
        return self._chamado

    async def marcar_notificacao_vista(self, claims, chamado_id):
        pass

    async def avaliacao_pendente(self, claims):
        return self._avaliacao_pendente

    async def mensagens(self, claims, chamado_id):
        return []

    async def mensagens_assinatura(self, claims, chamado_id):
        return (0, None)

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

    async def nome_categoria(self, claims, categoria_id):
        return next(
            (c["nome"] for c in self._categorias if str(c["id"]) == str(categoria_id)), None
        )

    async def criar(self, claims, **kwargs):
        self.criados.append(kwargs)
        return {"id": "novo-id", "codigo": "BOND-2026-00002"}

    async def avaliar(self, claims, chamado_id, *, nota, comentario):
        self.avaliacoes.append({"nota": nota, "comentario": comentario})
        return {
            "id": chamado_id, "avaliacao_nota": nota, "avaliacao_comentario": comentario,
            "avaliacao_em": datetime(2026, 6, 30, 15, 0, tzinfo=UTC),
        }

    async def reabrir(self, claims, chamado_id):
        if not self._chamado or self._chamado.get("status") != "RESOLVIDO":
            return None
        self.reaberturas.append(chamado_id)
        return {"id": chamado_id, "status": "EM_ATENDIMENTO"}

    async def adicionar_mensagem(self, claims, chamado_id, *, remetente_id, conteudo, anexos=None):
        self.mensagens_criadas.append(
            {"chamado_id": chamado_id, "conteudo": conteudo, "anexos": anexos or []}
        )
        return {"id": "m1", "created_at": datetime.now(UTC)}

    async def usuarios_para_copia(self, claims, *, excluir_id=None):
        return [{"id": "u9", "nome": "Zeca Financeiro", "departamento": "Financeiro"}]

    async def observadores(self, claims, chamado_id):
        return self._observadores_por_chamado.get(chamado_id, [])

    async def adicionar_observador(self, claims, chamado_id, perfil_id):
        self.observadores_adicionados.append((chamado_id, perfil_id))

    async def remover_observador(self, claims, chamado_id, perfil_id):
        self.observadores_removidos.append((chamado_id, perfil_id))


def _chamado(status="RESOLVIDO", cliente_id=UID, **extra):
    base = {
        "id": "aaa", "codigo": "BOND-2026-00001", "titulo": "Vazamento na linha 3",
        "descricao": "Detalhes...", "status": status, "prioridade": "ALTA",
        "cliente_id": cliente_id, "categoria": "Logística / Entrega",
        "cliente_nome": "Cliente Teste", "telefone_contato": "11987654321",
        "created_at": datetime(2026, 6, 30, 12, 0, tzinfo=UTC),
        "limite_resposta": None, "limite_resolucao": None, "resolvido_em": None,
        "avaliacao_nota": None, "avaliacao_comentario": None, "avaliacao_em": None,
        # Departamento de destino diferente do departamento do autor por padrão
        # (pedido a OUTRO setor — cenário em que a avaliação é exigida; ver
        # PortalService.pode_avaliar). Testes do caso "autoatendimento" (pedido
        # para o próprio setor) sobrescrevem os dois para o mesmo valor.
        "departamento_id": "d-ti", "cliente_departamento_id": "d-marketing",
    }
    base.update(extra)
    return base


@contextmanager
def portal_client(repo: FakeRepo, user=_cliente):
    """Cliente de teste com lifespan ativo (inicializa CSRF/JWT) e auth fake.

    Usa base_url https para que o cookie CSRF (``Secure``) seja reenviado pelo
    httpx — em http um cookie Secure não trafega, quebrando o double-submit.
    """
    app.dependency_overrides[get_current_user] = user
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


@pytest.mark.parametrize("nota", [1, 2, 3, 4])
def test_validar_comentario_avaliacao_nota_baixa_exige_50_chars(nota):
    with pytest.raises(ValueError):
        validar_comentario_avaliacao(nota, "muito curto")


@pytest.mark.parametrize("nota", [1, 2, 3, 4])
def test_validar_comentario_avaliacao_nota_baixa_aceita_50_chars(nota):
    comentario = "x" * 50
    assert validar_comentario_avaliacao(nota, comentario) == comentario


@pytest.mark.parametrize("nota", [5])
def test_validar_comentario_avaliacao_nota_alta_aceita_vazio(nota):
    assert validar_comentario_avaliacao(nota, "") is None
    assert validar_comentario_avaliacao(nota, "   ") is None


@pytest.mark.parametrize("valor", ["11987654321", "(11) 98765-4321", "51 3222-1122"])
def test_validar_telefone_contato_valido(valor):
    assert validar_telefone_contato(valor) == valor.strip()


@pytest.mark.parametrize("ruim", ["", "   ", "123", "abc"])
def test_validar_telefone_contato_invalido(ruim):
    with pytest.raises(ValueError):
        validar_telefone_contato(ruim)


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
        "telefone_contato": "(11) 98765-4321",
    }
    base.update(over)
    return base


def test_form_novo_chamado_tem_seletor_de_em_copia():
    repo = FakeRepo()
    with portal_client(repo) as client:
        resp = client.get("/portal/chamados/novo")
    assert resp.status_code == 200
    assert 'name="observadores"' in resp.text
    assert "Zeca Financeiro" in resp.text


def test_criar_chamado_com_observadores_adiciona_em_copia():
    repo = FakeRepo()
    with portal_client(repo) as client:
        token = _csrf_token(client)
        resp = client.post(
            "/portal/chamados",
            data={**_abertura_valida(), "observadores": ["u9", "u10"]},
            headers={"X-CSRF-Token": token},
            follow_redirects=False,
        )
    assert resp.status_code == 303
    assert len(repo.criados) == 1
    assert set(repo.observadores_adicionados) == {("novo-id", "u9"), ("novo-id", "u10")}


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


# --------------------------------------------------------------------------
# Telefone de contato obrigatório na abertura (2026-07-24)
# --------------------------------------------------------------------------
def test_criar_sem_telefone_retorna_400():
    repo = FakeRepo()
    with portal_client(repo) as client:
        token = _csrf_token(client)
        data = _abertura_valida(telefone_contato="")
        resp = client.post(
            "/portal/chamados", data=data, headers={"X-CSRF-Token": token},
        )
    assert resp.status_code == 400
    assert "contato" in resp.text.lower()
    assert repo.criados == []


def test_criar_com_telefone_sem_digitos_suficientes_retorna_400():
    repo = FakeRepo()
    with portal_client(repo) as client:
        token = _csrf_token(client)
        data = _abertura_valida(telefone_contato="123")
        resp = client.post(
            "/portal/chamados", data=data, headers={"X-CSRF-Token": token},
        )
    assert resp.status_code == 400
    assert "contato" in resp.text.lower()
    assert repo.criados == []


def test_criar_com_telefone_valido_grava_no_chamado():
    repo = FakeRepo()
    with portal_client(repo) as client:
        token = _csrf_token(client)
        data = _abertura_valida(telefone_contato="(11) 98765-4321")
        resp = client.post(
            "/portal/chamados", data=data, headers={"X-CSRF-Token": token},
            follow_redirects=False,
        )
    assert resp.status_code == 303
    assert repo.criados[0]["telefone_contato"] == "(11) 98765-4321"


# --------------------------------------------------------------------------
# Telefone no perfil: pré-preenche a abertura (2026-07-29, migration 0062)
# --------------------------------------------------------------------------
def test_form_de_abertura_vem_com_o_telefone_do_perfil():
    repo = FakeRepo(telefone="(51) 98167-0729")
    with portal_client(repo) as client:
        resp = client.get("/portal/chamados/novo")
    assert resp.status_code == 200
    assert 'value="(51) 98167-0729"' in resp.text


def test_form_de_abertura_sem_telefone_no_perfil_vem_vazio():
    repo = FakeRepo()
    with portal_client(repo) as client:
        resp = client.get("/portal/chamados/novo")
    assert resp.status_code == 200
    assert 'name="telefone_contato" required value=""' in resp.text
    # E avisa que o número informado aqui será guardado no perfil.
    assert "não precisar digitar de novo" in resp.text


def test_primeira_abertura_salva_o_telefone_no_perfil():
    repo = FakeRepo()  # perfil ainda sem telefone
    with portal_client(repo) as client:
        token = _csrf_token(client)
        resp = client.post(
            "/portal/chamados", data=_abertura_valida(telefone_contato="(11) 98765-4321"),
            headers={"X-CSRF-Token": token}, follow_redirects=False,
        )
    assert resp.status_code == 303
    assert repo.telefones_salvos == ["(11) 98765-4321"]


def test_abertura_com_telefone_diferente_atualiza_o_do_perfil():
    """Telefone informado na abertura é o contato ATUAL da pessoa: trocar o
    número aqui atualiza o perfil, sem exigir uma segunda edição em "Meu perfil"
    (pedido do gestor, 2026-07-29). O chamado guarda o número daquela abertura."""
    repo = FakeRepo(telefone="(51) 98167-0729")
    with portal_client(repo) as client:
        token = _csrf_token(client)
        resp = client.post(
            "/portal/chamados", data=_abertura_valida(telefone_contato="(11) 3333-4444"),
            headers={"X-CSRF-Token": token}, follow_redirects=False,
        )
    assert resp.status_code == 303
    assert repo.criados[0]["telefone_contato"] == "(11) 3333-4444"
    assert repo.telefones_salvos == ["(11) 3333-4444"]


def test_abertura_com_o_mesmo_telefone_do_perfil_nao_reescreve():
    """Caso mais comum (campo veio pré-preenchido e ninguém mexeu): nada a
    gravar — sem UPDATE inútil no perfil a cada chamado aberto."""
    repo = FakeRepo(telefone="(11) 98765-4321")
    with portal_client(repo) as client:
        token = _csrf_token(client)
        resp = client.post(
            "/portal/chamados", data=_abertura_valida(telefone_contato="(11) 98765-4321"),
            headers={"X-CSRF-Token": token}, follow_redirects=False,
        )
    assert resp.status_code == 303
    assert repo.telefones_salvos == []


# --------------------------------------------------------------------------
# Marketing: fluxo por demanda (prioridade forçada + prazo mínimo de 48h)
# --------------------------------------------------------------------------
def test_criar_marketing_sem_data_nem_sem_prazo_retorna_400():
    repo = FakeRepo()
    with portal_client(repo) as client:
        token = _csrf_token(client)
        data = _abertura_valida(departamento_id="d3", setor="Financeiro", data_entrega="")
        resp = client.post(
            "/portal/chamados", data=data, headers={"X-CSRF-Token": token},
        )
    assert resp.status_code == 400
    assert "48h" in resp.text or "data limite" in resp.text.lower()
    assert repo.criados == []


def test_criar_marketing_data_abaixo_do_minimo_retorna_400():
    repo = FakeRepo()
    with portal_client(repo) as client:
        token = _csrf_token(client)
        data = _abertura_valida(
            departamento_id="d3", setor="Financeiro", data_entrega="2020-01-01",
        )
        resp = client.post(
            "/portal/chamados", data=data, headers={"X-CSRF-Token": token},
        )
    assert resp.status_code == 400
    assert "48h" in resp.text
    assert repo.criados == []


def test_criar_marketing_com_data_valida_forca_prioridade_media():
    from app.services.portal import PortalService

    repo = FakeRepo()
    with portal_client(repo) as client:
        token = _csrf_token(client)
        data = _abertura_valida(
            departamento_id="d3", setor="Financeiro", prioridade="ALTA",
            data_entrega=PortalService.data_entrega_min().isoformat(),
        )
        resp = client.post(
            "/portal/chamados", data=data, headers={"X-CSRF-Token": token},
            follow_redirects=False,
        )
    assert resp.status_code == 303
    assert len(repo.criados) == 1
    assert repo.criados[0]["prioridade"] == "MEDIA"
    assert repo.criados[0]["sem_prazo"] is False


def test_criar_marketing_sem_prazo_marcado_forca_prioridade_baixa():
    repo = FakeRepo()
    with portal_client(repo) as client:
        token = _csrf_token(client)
        data = _abertura_valida(
            departamento_id="d3", setor="Financeiro", prioridade="ALTA", sem_prazo="on",
        )
        resp = client.post(
            "/portal/chamados", data=data, headers={"X-CSRF-Token": token},
            follow_redirects=False,
        )
    assert resp.status_code == 303
    assert len(repo.criados) == 1
    assert repo.criados[0]["prioridade"] == "BAIXA"
    assert repo.criados[0]["sem_prazo"] is True
    assert repo.criados[0]["data_entrega"] is None


# --------------------------------------------------------------------------
# Origem da demanda (2026-07-31): decidida pelo departamento do AUTOR, não
# mais sempre "Solicitação" — pedido do usuário ("quando um operador de
# marketing abre um chamado, precisa contar como marketing").
# --------------------------------------------------------------------------
def test_criar_marketing_autor_do_proprio_marketing_conta_como_origem_marketing():
    from app.services.portal import PortalService

    repo = FakeRepo(departamento_id="d3")  # autor é do próprio Marketing
    with portal_client(repo) as client:
        token = _csrf_token(client)
        data = _abertura_valida(
            departamento_id="d3", setor="Financeiro",
            data_entrega=PortalService.data_entrega_min().isoformat(),
        )
        resp = client.post(
            "/portal/chamados", data=data, headers={"X-CSRF-Token": token},
            follow_redirects=False,
        )
    assert resp.status_code == 303
    assert repo.criados[0]["origem_demanda"] == "Marketing"


def test_criar_marketing_autor_de_outro_setor_conta_como_solicitacao():
    from app.services.portal import PortalService

    repo = FakeRepo(departamento_id="d2")  # autor é do RH, pedindo pro Marketing
    with portal_client(repo) as client:
        token = _csrf_token(client)
        data = _abertura_valida(
            departamento_id="d3", setor="Financeiro",
            data_entrega=PortalService.data_entrega_min().isoformat(),
        )
        resp = client.post(
            "/portal/chamados", data=data, headers={"X-CSRF-Token": token},
            follow_redirects=False,
        )
    assert resp.status_code == 303
    assert repo.criados[0]["origem_demanda"] == "Solicitação"


def test_criar_fora_do_marketing_origem_demanda_sempre_solicitacao():
    """O campo só existe pro Marketing — mesmo autor do próprio setor de
    destino (ex.: alguém da RH abrindo pra RH), fora do Marketing sempre
    grava "Solicitação" (nunca fica "Marketing" por coincidência de setor)."""
    repo = FakeRepo(departamento_id="d2")  # autor é do RH, abrindo pra RH
    with portal_client(repo) as client:
        token = _csrf_token(client)
        data = _abertura_valida(departamento_id="d2")
        resp = client.post(
            "/portal/chamados", data=data, headers={"X-CSRF-Token": token},
            follow_redirects=False,
        )
    assert resp.status_code == 303
    assert repo.criados[0]["origem_demanda"] == "Solicitação"


def test_criar_fora_do_marketing_preserva_prioridade_escolhida():
    repo = FakeRepo()
    with portal_client(repo) as client:
        token = _csrf_token(client)
        data = _abertura_valida(prioridade="ALTA")  # d2 = RH
        resp = client.post(
            "/portal/chamados", data=data, headers={"X-CSRF-Token": token},
            follow_redirects=False,
        )
    assert resp.status_code == 303
    assert repo.criados[0]["prioridade"] == "ALTA"


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
# Unificação (2026-07-09): "Meus chamados" + "Chamados do Departamento" viram
# uma única página — a seção de colegas só aparece pro líder de setor (ADMIN).
# --------------------------------------------------------------------------
def test_dashboard_mostra_chamados_do_departamento_para_lider_admin():
    repo = FakeRepo(
        role="ADMIN", departamento_id="d1",
        chamados_colegas=[{
            "id": "c9", "codigo": "BOND-2026-00009", "titulo": "Solicitação de férias",
            "status": "NOVO", "prioridade": "MEDIA", "departamento": "RH",
            "created_at": datetime(2026, 7, 9, 10, 0, tzinfo=UTC),
            "limite_resolucao": None, "respondido_em": None, "resolvido_em": None,
            "cliente_nome": "Giordano Burtet", "cliente_avatar_path": None,
            "cliente_avatar_atualizado_em": None, "operador_nome": None,
        }],
    )
    with portal_client(repo, user=_admin) as client:
        resp = client.get("/portal")
    assert resp.status_code == 200
    assert "Chamados do Departamento" in resp.text
    assert "BOND-2026-00009" in resp.text
    assert repo.chamados_departamento_filtros == {"departamento_id": "d1"}


def test_dashboard_nao_mostra_chamados_do_departamento_para_funcionario():
    repo = FakeRepo(role="CLIENTE")
    with portal_client(repo) as client:
        resp = client.get("/portal")
    assert resp.status_code == 200
    assert "Chamados do Departamento" not in resp.text
    assert repo.chamados_departamento_filtros is None


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
        avaliacao_em=datetime(2026, 6, 30, 15, 0, tzinfo=UTC),
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


def test_post_avaliacao_chamado_para_o_proprio_departamento_e_bloqueada():
    """Chamado aberto para o PRÓPRIO departamento do autor (ex.: Marketing
    pedindo pro Marketing, recorrente na prática) não exige avaliação — a
    trava só vale pra chamados abertos a OUTRO departamento (2026-07-23)."""
    repo = FakeRepo(chamado=_chamado(
        status="RESOLVIDO", departamento_id="d-marketing", cliente_departamento_id="d-marketing",
    ))
    with portal_client(repo) as client:
        token = _csrf(client)
        resp = client.post(
            "/portal/chamados/aaa/avaliacao",
            data={"nota": "5"},
            headers={"X-CSRF-Token": token, "HX-Request": "true"},
        )
    assert resp.status_code == 403
    assert repo.avaliacoes == []


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


# --------------------------------------------------------------------------
# 2026-07-24: nota <= 4 exige comentário de pelo menos 50 caracteres.
# --------------------------------------------------------------------------
def test_post_avaliacao_nota_baixa_sem_comentario_retorna_422():
    repo = FakeRepo(chamado=_chamado(status="RESOLVIDO"))
    with portal_client(repo) as client:
        token = _csrf(client)
        resp = client.post(
            "/portal/chamados/aaa/avaliacao",
            data={"nota": "3", "comentario": ""},
            headers={"X-CSRF-Token": token, "HX-Request": "true"},
        )
    assert resp.status_code == 422
    assert "50 caracteres" in resp.text
    assert repo.avaliacoes == []


def test_post_avaliacao_nota_baixa_comentario_curto_retorna_422():
    repo = FakeRepo(chamado=_chamado(status="RESOLVIDO"))
    with portal_client(repo) as client:
        token = _csrf(client)
        resp = client.post(
            "/portal/chamados/aaa/avaliacao",
            data={"nota": "4", "comentario": "Faltou atenção"},
            headers={"X-CSRF-Token": token, "HX-Request": "true"},
        )
    assert resp.status_code == 422
    assert "50 caracteres" in resp.text
    assert repo.avaliacoes == []


def test_post_avaliacao_nota_baixa_comentario_valido_persiste():
    repo = FakeRepo(chamado=_chamado(status="RESOLVIDO"))
    comentario = "O problema não foi resolvido de verdade e o retorno demorou muito mais do que o esperado."
    assert len(comentario) >= 50
    with portal_client(repo) as client:
        token = _csrf(client)
        resp = client.post(
            "/portal/chamados/aaa/avaliacao",
            data={"nota": "2", "comentario": comentario},
            headers={"X-CSRF-Token": token, "HX-Request": "true"},
        )
    assert resp.status_code == 200
    assert repo.avaliacoes == [{"nota": 2, "comentario": comentario}]


def test_post_avaliacao_nota_alta_sem_comentario_continua_opcional():
    repo = FakeRepo(chamado=_chamado(status="RESOLVIDO"))
    with portal_client(repo) as client:
        token = _csrf(client)
        resp = client.post(
            "/portal/chamados/aaa/avaliacao",
            data={"nota": "5", "comentario": ""},
            headers={"X-CSRF-Token": token, "HX-Request": "true"},
        )
    assert resp.status_code == 200
    assert repo.avaliacoes == [{"nota": 5, "comentario": None}]


# --------------------------------------------------------------------------
# 2026-07-24: reabertura pelo autor (insatisfeito com a solução).
# --------------------------------------------------------------------------
def test_reabrir_mostrado_quando_resolvido():
    repo = FakeRepo(chamado=_chamado(status="RESOLVIDO"))
    with portal_client(repo) as client:
        resp = client.get("/portal/chamados/aaa")
    assert resp.status_code == 200
    assert "Reabrir chamado" in resp.text


def test_reabrir_oculto_quando_nao_resolvido():
    repo = FakeRepo(chamado=_chamado(status="EM_ATENDIMENTO"))
    with portal_client(repo) as client:
        resp = client.get("/portal/chamados/aaa")
    assert resp.status_code == 200
    assert "Reabrir chamado" not in resp.text


def test_post_reabrir_autor_chamado_resolvido_redireciona():
    repo = FakeRepo(chamado=_chamado(status="RESOLVIDO"))
    with portal_client(repo) as client:
        token = _csrf(client)
        resp = client.post(
            "/portal/chamados/aaa/reabrir",
            headers={"X-CSRF-Token": token},
            follow_redirects=False,
        )
    assert resp.status_code == 303
    assert resp.headers["location"] == "/portal/chamados/aaa"
    assert repo.reaberturas == ["aaa"]


def test_post_reabrir_chamado_nao_resolvido_e_bloqueado():
    repo = FakeRepo(chamado=_chamado(status="EM_ATENDIMENTO"))
    with portal_client(repo) as client:
        token = _csrf(client)
        resp = client.post(
            "/portal/chamados/aaa/reabrir",
            headers={"X-CSRF-Token": token},
        )
    assert resp.status_code == 403
    assert repo.reaberturas == []


def test_post_reabrir_nao_autor_e_bloqueado():
    repo = FakeRepo(chamado=_chamado(status="RESOLVIDO", cliente_id="outro-uid"))
    with portal_client(repo) as client:
        token = _csrf(client)
        resp = client.post(
            "/portal/chamados/aaa/reabrir",
            headers={"X-CSRF-Token": token},
        )
    assert resp.status_code == 403
    assert repo.reaberturas == []


# --------------------------------------------------------------------------
# Fase 8 (2026-07-09): "em cópia" (observadores multi-setoriais)
# --------------------------------------------------------------------------
def test_detalhe_mostra_observadores_e_seletor_para_adicionar():
    repo = FakeRepo(chamado=_chamado())
    repo._observadores_por_chamado["aaa"] = [
        {"perfil_id": "u5", "nome": "Ana Comercial", "departamento": "Comercial"}
    ]
    with portal_client(repo) as client:
        resp = client.get("/portal/chamados/aaa")
    assert resp.status_code == 200
    assert "Em cópia" in resp.text
    assert "Ana Comercial" in resp.text
    assert 'action="/portal/chamados/aaa/observadores/u5/remover"' in resp.text
    assert 'action="/portal/chamados/aaa/observadores"' in resp.text


def test_detalhe_sem_observadores_mostra_mensagem_vazia():
    repo = FakeRepo(chamado=_chamado())
    with portal_client(repo) as client:
        resp = client.get("/portal/chamados/aaa")
    assert "Ninguém em cópia" in resp.text


def test_adicionar_observador():
    repo = FakeRepo(chamado=_chamado())
    with portal_client(repo) as client:
        token = _csrf(client)
        resp = client.post(
            "/portal/chamados/aaa/observadores",
            data={"perfil_id": "u9"},
            headers={"X-CSRF-Token": token},
            follow_redirects=False,
        )
    assert resp.status_code == 303
    assert repo.observadores_adicionados == [("aaa", "u9")]


def test_remover_observador():
    repo = FakeRepo(chamado=_chamado())
    with portal_client(repo) as client:
        token = _csrf(client)
        resp = client.post(
            "/portal/chamados/aaa/observadores/u9/remover",
            headers={"X-CSRF-Token": token},
            follow_redirects=False,
        )
    assert resp.status_code == 303
    assert repo.observadores_removidos == [("aaa", "u9")]


# --------------------------------------------------------------------------
# 2026-07-21: avaliação pendente bloqueia a abertura de um novo chamado.
# --------------------------------------------------------------------------
def test_abrir_chamado_com_avaliacao_pendente_e_redirecionado():
    repo = FakeRepo(avaliacao_pendente={"id": "res1", "codigo": "BOND-2026-00099", "titulo": "Impressora"})
    with portal_client(repo) as client:
        resp = client.get("/portal/chamados/novo", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/portal/chamados/res1?avaliar_pendente=1"


def test_abrir_chamado_sem_pendencia_mostra_formulario():
    repo = FakeRepo(avaliacao_pendente=None)
    with portal_client(repo) as client:
        resp = client.get("/portal/chamados/novo")
    assert resp.status_code == 200
    assert "Selecione o departamento" in resp.text


def test_detalhe_com_avaliar_pendente_mostra_aviso():
    repo = FakeRepo(chamado=_chamado(status="RESOLVIDO"))
    with portal_client(repo) as client:
        resp = client.get("/portal/chamados/aaa?avaliar_pendente=1")
    assert resp.status_code == 200
    assert "Antes de abrir um novo chamado" in resp.text


# --------------------------------------------------------------------------
# Abertura dinâmica do departamento Químico (campos por categoria + IA)
# --------------------------------------------------------------------------
def _repo_quimico(categoria_nome="Registro de Ocorrência", **kw):
    """FakeRepo com o Químico recebendo chamados e a categoria indicada."""
    return FakeRepo(
        departamentos=[
            {"id": "d1", "nome": "TI", "recebe_chamados": True},
            {"id": "dq", "nome": "Dpto Químico", "recebe_chamados": True},
            {"id": "d4", "nome": "Financeiro", "recebe_chamados": False},
        ],
        categorias=[{"id": "cq", "nome": categoria_nome}],
        subcategorias={"cq": []},  # categorias do Químico não usam subcategoria
        **kw,
    )


def _abertura_ocorrencia(**over):
    """Payload de abertura válido para a categoria Registro de Ocorrência."""
    base = {
        "titulo": "Vazamento na linha 3",
        "descricao": "Detalhes do ocorrido",
        "departamento_id": "dq",
        "categoria_id": "cq",
        "setor": "Financeiro",
        "telefone_contato": "51999998888",
        # campos dinâmicos (namespace campo__) — schema real do FB033
        "campo__regiao": "001-COLOMBO",
        "campo__supervisor": "CHRISTIAN ALVES SEVERO",
        "campo__gerente": "ANDRE LUIZ MANDELLI",
        "campo__nome_empresa_cliente": "Cliente X",
        "campo__cidade": "Canoas",
        "campo__nome_contato_cliente": "Fulano",
        "campo__cargo": "Comprador",
        "campo__setor_contato": "Compras",
        "campo__fone": "51999998888",
        "campo__email": "fulano@cliente.com",
        "campo__produto": "ALKARES",
        "campo__lote": "LOTE1234567890",
        "campo__descricao_situacao": "Produto vazou no piso",
    }
    base.update(over)
    return base


def test_campos_fragmento_do_quimico_renderiza_campos():
    repo = _repo_quimico()
    with portal_client(repo) as client:
        resp = client.get("/portal/chamados/campos", params={"categoria_id": "cq"})
    assert resp.status_code == 200
    assert 'name="campo__descricao_situacao"' in resp.text
    assert "Descrição da ocorrência" in resp.text


def test_criar_quimico_grava_dados_formulario():
    repo = _repo_quimico()
    with portal_client(repo) as client:
        token = _csrf_token(client)
        resp = client.post(
            "/portal/chamados",
            data=_abertura_ocorrencia(),
            headers={"X-CSRF-Token": token},
            follow_redirects=False,
        )
    assert resp.status_code == 303
    assert len(repo.criados) == 1
    dados = repo.criados[0]["dados_formulario"]
    assert dados["descricao_situacao"] == "Produto vazou no piso"
    assert dados["nome_empresa_cliente"] == "Cliente X"
    # Campo forjado fora do schema não é gravado.
    assert "campo_forjado" not in dados


def test_criar_quimico_sem_assunto_e_descricao_deriva_automaticamente():
    """A tela de abertura esconde Assunto/Descrição pro Químico (2026-07-22) —
    o navegador ainda manda os dois campos (vazios, sem `required`); o servidor
    precisa aceitar e derivar os dois a partir do formulário dinâmico."""
    repo = _repo_quimico()
    with portal_client(repo) as client:
        token = _csrf_token(client)
        resp = client.post(
            "/portal/chamados",
            data=_abertura_ocorrencia(titulo="", descricao=""),
            headers={"X-CSRF-Token": token},
            follow_redirects=False,
        )
    assert resp.status_code == 303
    criado = repo.criados[0]
    assert criado["titulo"] == "Registro de Ocorrência — Cliente X"
    assert criado["descricao"] == "Produto vazou no piso"


def test_criar_quimico_sem_campo_obrigatorio_retorna_erro():
    repo = _repo_quimico()
    with portal_client(repo) as client:
        token = _csrf_token(client)
        resp = client.post(
            "/portal/chamados",
            data=_abertura_ocorrencia(campo__descricao_situacao=""),
            headers={"X-CSRF-Token": token},
            follow_redirects=False,
        )
    assert resp.status_code == 400
    assert "Descrição da ocorrência" in resp.text
    assert repo.criados == []  # nada gravado quando a validação falha


def test_criar_quimico_lote_curto_retorna_erro_min_chars():
    repo = _repo_quimico()
    with portal_client(repo) as client:
        token = _csrf_token(client)
        resp = client.post(
            "/portal/chamados",
            data=_abertura_ocorrencia(campo__lote="ABC"),
            headers={"X-CSRF-Token": token},
            follow_redirects=False,
        )
    assert resp.status_code == 400
    assert "Lote" in resp.text
    assert repo.criados == []


def test_criar_fora_do_quimico_nao_grava_dados_formulario():
    repo = _repo_quimico()
    with portal_client(repo) as client:
        token = _csrf_token(client)
        # Departamento TI: mesmo enviando campo__*, nada de dados_formulario.
        resp = client.post(
            "/portal/chamados",
            data={
                "titulo": "Impressora", "descricao": "não liga", "departamento_id": "d1",
                "categoria_id": "cq", "setor": "Financeiro", "campo__cidade": "x",
                "telefone_contato": "51999998888",
            },
            headers={"X-CSRF-Token": token},
            follow_redirects=False,
        )
    assert resp.status_code == 303
    assert repo.criados[0]["dados_formulario"] == {}


def test_criar_quimico_analise_laboratorial_com_checkbox_multiplo():
    """Análise Laboratorial usa checkbox_multi (`analises_solicitadas`) — o
    HTML manda várias entradas com o MESMO name; confirma que o agrupamento em
    portal.py preserva todas (não só a última) e grava como lista."""
    repo = _repo_quimico(categoria_nome="Solicitação de Análise Laboratorial")
    data = {
        "titulo": "Amostra de óleo",
        "descricao": "Verificar especificação",
        "departamento_id": "dq",
        "categoria_id": "cq",
        "setor": "Financeiro",
        "telefone_contato": "51999998888",
        "campo__unidade_entrega": "Matriz Canoas/RS",
        "campo__identificacao_cliente": "Cliente Z",
        "campo__descricao_amostra": "Óleo, lote 123, aspecto turvo",
        "campo__objetivo_analises": "Confirmar especificação",
    }
    with portal_client(repo) as client:
        token = _csrf_token(client)
        resp = client.post(
            "/portal/chamados",
            data={
                **data,
                # httpx serializa valor-lista como múltiplas entradas do mesmo
                # campo — simula os checkboxes marcados do form real.
                "campo__analises_solicitadas": [
                    "Determinação de pH", "Determinação de densidade",
                ],
            },
            headers={"X-CSRF-Token": token},
            follow_redirects=False,
        )
    assert resp.status_code == 303
    dados = repo.criados[0]["dados_formulario"]
    assert dados["analises_solicitadas"] == ["Determinação de pH", "Determinação de densidade"]


def test_criar_quimico_analise_laboratorial_sem_checkbox_retorna_erro():
    repo = _repo_quimico(categoria_nome="Solicitação de Análise Laboratorial")
    data = {
        "titulo": "Amostra de óleo",
        "descricao": "Verificar especificação",
        "departamento_id": "dq",
        "categoria_id": "cq",
        "setor": "Financeiro",
        "telefone_contato": "51999998888",
        "campo__unidade_entrega": "Matriz Canoas/RS",
        "campo__identificacao_cliente": "Cliente Z",
        "campo__descricao_amostra": "Óleo, lote 123",
        "campo__objetivo_analises": "Confirmar especificação",
    }
    with portal_client(repo) as client:
        token = _csrf_token(client)
        resp = client.post(
            "/portal/chamados",
            data={**data, "csrf_token": token},
            headers={"X-CSRF-Token": token},
            follow_redirects=False,
        )
    assert resp.status_code == 400
    assert "Análises solicitadas" in resp.text
    assert repo.criados == []


# --------------------------------------------------------------------------
# Hook de triagem por IA na abertura (F1 — plano_md_mestre_IA.md, Seção 2.3)
# --------------------------------------------------------------------------
def _settings_triagem(**over):
    from app.config import Settings

    base = dict(
        session_secret="segredo-real-de-teste-nao-default",
        csrf_secret="outro-segredo-real-de-teste-nao-default",
        ia_triagem_ativa=True,
        ia_triagem_api_key="k-triagem",
        ia_triagem_departamentos="TI",
    )
    base.update(over)
    return Settings(**base)


@contextmanager
def _hook_triagem(settings):
    """Patcha settings da rota + agendador da triagem (disparo imediato via
    ``agendar_triagem``/create_task desde a otimização de latência 2026-07-23;
    o mock evita task real de asyncio no TestClient)."""
    from unittest.mock import MagicMock, patch

    from app.ia import triagem
    from app.routes import portal as portal_mod

    agendar = MagicMock()
    with (
        patch.object(portal_mod, "get_settings", return_value=settings),
        patch.object(triagem, "agendar_triagem", agendar),
    ):
        yield agendar


def test_criar_chamado_ti_agenda_triagem_em_background():
    repo = FakeRepo()
    with _hook_triagem(_settings_triagem()) as executar:
        with portal_client(repo) as client:
            token = _csrf_token(client)
            resp = client.post(
                "/portal/chamados",
                data=_abertura_valida(departamento_id="d1", categoria_id="c1"),
                headers={"X-CSRF-Token": token},
                follow_redirects=False,
            )
    assert resp.status_code == 303
    executar.assert_called_once_with("novo-id")


def test_criar_chamado_departamento_nao_habilitado_nao_agenda_triagem():
    repo = FakeRepo()
    with _hook_triagem(_settings_triagem(ia_triagem_departamentos="TI")) as executar:
        with portal_client(repo) as client:
            token = _csrf_token(client)
            resp = client.post(
                "/portal/chamados",
                data=_abertura_valida(departamento_id="d2"),  # RH: fora da lista
                headers={"X-CSRF-Token": token},
                follow_redirects=False,
            )
    assert resp.status_code == 303
    executar.assert_not_called()


def test_criar_chamado_com_kill_switch_desligado_nao_agenda_triagem():
    repo = FakeRepo()
    with _hook_triagem(_settings_triagem(ia_triagem_ativa=False)) as executar:
        with portal_client(repo) as client:
            token = _csrf_token(client)
            resp = client.post(
                "/portal/chamados",
                data=_abertura_valida(departamento_id="d1", categoria_id="c1"),
                headers={"X-CSRF-Token": token},
                follow_redirects=False,
            )
    assert resp.status_code == 303
    executar.assert_not_called()


def test_responder_como_autor_agenda_re_triagem():
    """F2: resposta do AUTOR num chamado de depto habilitado reagenda a triagem."""
    repo = FakeRepo(chamado=_chamado(status="NOVO", departamento="TI"))
    with _hook_triagem(_settings_triagem()) as executar:
        with portal_client(repo) as client:
            token = _csrf_token(client)
            resp = client.post(
                "/portal/chamados/aaa/mensagens",
                data={"conteudo": "O monitor acende sim."},
                headers={"X-CSRF-Token": token},
                follow_redirects=False,
            )
    assert resp.status_code == 303
    executar.assert_called_once_with("aaa")


def test_responder_em_depto_nao_habilitado_nao_agenda_re_triagem():
    repo = FakeRepo(chamado=_chamado(status="NOVO", departamento="RH"))
    with _hook_triagem(_settings_triagem(ia_triagem_departamentos="TI")) as executar:
        with portal_client(repo) as client:
            token = _csrf_token(client)
            resp = client.post(
                "/portal/chamados/aaa/mensagens",
                data={"conteudo": "Alguma resposta."},
                headers={"X-CSRF-Token": token},
                follow_redirects=False,
            )
    assert resp.status_code == 303
    executar.assert_not_called()


def test_responder_como_nao_autor_nao_agenda_re_triagem():
    """Só a resposta do AUTOR reagenda (observador/terceiro não conta)."""
    repo = FakeRepo(chamado=_chamado(status="NOVO", departamento="TI", cliente_id="outro-uuid"))
    with _hook_triagem(_settings_triagem()) as executar:
        with portal_client(repo) as client:
            token = _csrf_token(client)
            resp = client.post(
                "/portal/chamados/aaa/mensagens",
                data={"conteudo": "Mensagem de observador."},
                headers={"X-CSRF-Token": token},
                follow_redirects=False,
            )
    assert resp.status_code == 303
    executar.assert_not_called()


# --------------------------------------------------------------------------
# 2026-07-30: chamado combinado (duplicado) na visão do autor (migration 0065).
# --------------------------------------------------------------------------
def test_detalhe_de_chamado_combinado_avisa_o_autor_e_aponta_o_principal():
    """O autor não pode ficar no escuro: o chamado dele foi encerrado por ser
    repetido, e o atendimento (que ele acompanha em cópia) segue no principal."""
    repo = FakeRepo(
        chamado=_chamado(chamado_principal_id="p1", principal_codigo="BOND-2026-00007")
    )
    with portal_client(repo) as c:
        r = c.get("/portal/chamados/aaa")
    assert r.status_code == 200
    assert "combinados" in r.text
    assert "BOND-2026-00007" in r.text
    assert "em cópia" in r.text
    assert "/portal/chamados/p1" in r.text


def test_chamado_combinado_nao_pede_avaliacao_nem_reabertura():
    """Ninguém atendeu o duplicado — CSAT dele mediria um atendimento que
    aconteceu em outro chamado, e reabri-lo devolveria ao quadro algo que
    continua fora dos indicadores."""
    # Sem a combinação, este mesmo chamado (RESOLVIDO, do próprio autor, aberto
    # para outro setor) mostraria as duas coisas — é o que torna o teste válido.
    with portal_client(FakeRepo(chamado=_chamado())) as c:
        base = c.get("/portal/chamados/aaa").text
    assert "Reabrir chamado" in base
    assert "Como você avalia a resolução deste chamado?" in base

    repo = FakeRepo(chamado=_chamado(chamado_principal_id="p1"))
    with portal_client(repo) as c:
        r = c.get("/portal/chamados/aaa")
    assert "Reabrir chamado" not in r.text
    assert "Como você avalia a resolução deste chamado?" not in r.text
