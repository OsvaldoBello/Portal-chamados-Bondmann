"""Testes do AdminService (Sprint 2 / item 2.2, M2) — puros, sem banco."""

from __future__ import annotations

import pytest

from app.services.admin import AdminService, ResultadoPapel

DEPARTAMENTOS = [
    {"id": "d1", "nome": "TI", "ativo": True, "recebe_chamados": True},
    {"id": "d2", "nome": "Comercial", "ativo": True, "recebe_chamados": False},
    {"id": "d3", "nome": "RH (inativo)", "ativo": False, "recebe_chamados": True},
]


def test_departamento_valido_vazio_retorna_none():
    assert AdminService.departamento_valido(DEPARTAMENTOS, "", exigir_fila=False) is None


def test_departamento_valido_inexistente_retorna_none():
    assert AdminService.departamento_valido(DEPARTAMENTOS, "d9", exigir_fila=False) is None


def test_departamento_valido_inativo_retorna_none():
    assert AdminService.departamento_valido(DEPARTAMENTOS, "d3", exigir_fila=False) is None


def test_departamento_valido_sem_exigir_fila_aceita_sem_fila():
    # Setor de origem (0028): qualquer setor ativo serve, mesmo sem fila.
    assert AdminService.departamento_valido(DEPARTAMENTOS, "d2", exigir_fila=False) == "d2"


def test_departamento_valido_exigindo_fila_rejeita_sem_fila():
    # Categoria (0027) e OPERADOR (0028) exigem setor com fila de atendimento.
    assert AdminService.departamento_valido(DEPARTAMENTOS, "d2", exigir_fila=True) is None


def test_departamento_valido_exigindo_fila_aceita_com_fila():
    assert AdminService.departamento_valido(DEPARTAMENTOS, "d1", exigir_fila=True) == "d1"


class _FakeRepo:
    def __init__(self, *, papel_gravado="ADMIN"):
        self._papel_gravado = papel_gravado
        self.chamadas = []

    async def atualizar_papel(self, claims, user_id, *, role, departamento_id):
        self.chamadas.append(("atualizar_papel", user_id, role, departamento_id))

    async def obter_papel(self, claims, user_id):
        return self._papel_gravado


class _FakeAdminAPI:
    def __init__(self, *, jwt_role="ADMIN", falha_update=False, falha_get=False):
        self._jwt_role = jwt_role
        self._falha_update = falha_update
        self._falha_get = falha_get
        self.chamadas = []

    async def update_user_by_id(self, user_id, body):
        self.chamadas.append(("update_user_by_id", user_id, body))
        if self._falha_update:
            raise RuntimeError("falha simulada no update")

    async def get_user_by_id(self, user_id):
        if self._falha_get:
            raise RuntimeError("falha simulada no get")
        u = type("U", (), {"app_metadata": {"role": self._jwt_role}})()
        return type("R", (), {"user": u})()


class _FakeClient:
    def __init__(self, **kw):
        self.admin_api = _FakeAdminAPI(**kw)
        self.auth = type("A", (), {"admin": self.admin_api})()


@pytest.mark.asyncio
async def test_promover_papel_sem_client_grava_e_confirma_sucesso():
    repo = _FakeRepo(papel_gravado="ADMIN")
    resultado = await AdminService.promover_papel(
        repo=repo, claims={}, user_id="u1", papel="ADMIN", departamento_id="d1", client=None,
    )
    assert resultado == ResultadoPapel(
        sucesso=True, mensagem="Papel atualizado. A mudança vale no próximo login do usuário."
    )
    assert ("atualizar_papel", "u1", "ADMIN", "d1") in repo.chamadas


@pytest.mark.asyncio
async def test_promover_papel_divergencia_no_banco_reporta_erro():
    repo = _FakeRepo(papel_gravado="OPERADOR")  # escrita não pegou
    resultado = await AdminService.promover_papel(
        repo=repo, claims={}, user_id="u1", papel="ADMIN", departamento_id="d1", client=None,
    )
    assert not resultado.sucesso
    assert "banco" in resultado.mensagem.lower()


@pytest.mark.asyncio
async def test_promover_papel_divergencia_no_jwt_avisa_sem_reportar_sucesso_puro():
    repo = _FakeRepo(papel_gravado="ADMIN")
    client = _FakeClient(jwt_role="OPERADOR")
    resultado = await AdminService.promover_papel(
        repo=repo, claims={}, user_id="u1", papel="ADMIN", departamento_id="d1", client=client,
    )
    assert resultado.sucesso  # banco está certo — só o JWT ficou defasado
    assert "diferente" in resultado.mensagem or "verifique" in resultado.mensagem.lower()


@pytest.mark.asyncio
async def test_promover_papel_sem_divergencia_confirma_sucesso():
    repo = _FakeRepo(papel_gravado="ADMIN")
    client = _FakeClient(jwt_role="ADMIN")
    resultado = await AdminService.promover_papel(
        repo=repo, claims={}, user_id="u1", papel="ADMIN", departamento_id="d1", client=client,
    )
    assert resultado == ResultadoPapel(
        sucesso=True, mensagem="Papel atualizado. A mudança vale no próximo login do usuário."
    )


@pytest.mark.asyncio
async def test_promover_papel_falha_no_espelhamento_nao_bloqueia_releitura():
    # update_user_by_id falha (ex.: rede) mas a releitura de perfis confirma o
    # banco certo — não deve quebrar, só não confirmar o lado do JWT.
    repo = _FakeRepo(papel_gravado="ADMIN")
    client = _FakeClient(falha_update=True, falha_get=True)
    resultado = await AdminService.promover_papel(
        repo=repo, claims={}, user_id="u1", papel="ADMIN", departamento_id="d1", client=client,
    )
    assert resultado.sucesso
    assert resultado.mensagem == "Papel atualizado. A mudança vale no próximo login do usuário."
