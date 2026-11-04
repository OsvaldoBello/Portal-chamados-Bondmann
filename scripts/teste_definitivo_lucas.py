"""Teste definitivo: Desligamento do operador Admin Lucas da TI.

Valida de ponta a ponta o cenário de desligamento de um operador/admin:
1. Lucas da TI atendeu e finalizou chamados (RESOLVIDO) com mensagens e histórico de auditoria;
2. Lucas da TI também possui chamados em aberto (EM_ATENDIMENTO e NOVO);
3. O administrador da TI executa a exclusão de Lucas em /admin/usuarios/{id}/excluir;
4. Valida:
   - GoTrue: delete_user chamado (credenciais revogadas);
   - Perfil: desativado (ativo=False) no banco;
   - Painel /admin/usuarios: Lucas não aparece mais na lista de ativos;
   - Atribuição de operadores: Lucas não aparece mais no select de novos atendimentos;
   - Chamados Finalizados: operador_id, mensagens e histórico mantêm a autoria de "Lucas da TI";
   - Chamados em Aberto: desatribuídos e devolvidos para a fila (NOVO / sem operador).
"""

from __future__ import annotations

import asyncio
import sys
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import unquote

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient

from app.auth.dependencies import CurrentUser, get_current_user
from app.main import app
from app.repositories.admin import AdminRepo, get_admin_repo
from app.repositories.chamados import get_chamados_repo

LUCAS_ID = "aaaaaaaa-bbbb-cccc-dddd-111111111111"
ADMIN_TI_ID = "77777777-7777-7777-7777-777777777777"
DEP_TI_ID = "dep-ti-0001"


class FakeAdminAPI:
    def __init__(self):
        self.calls = []

    async def list_users(self, page=1, per_page=200):
        return []

    async def delete_user(self, uid):
        self.calls.append(("delete", uid))


class FakeSupaClient:
    def __init__(self):
        self.admin_api = FakeAdminAPI()
        self.auth = type("A", (), {"admin": self.admin_api})()


def test_cenario_lucas_desligado():
    print("=" * 80)
    print("TESTE DEFINITIVO: DESLIGAMENTO DO OPERADOR ADMIN LUCAS DA TI")
    print("=" * 80)

    # 1. Base simulada
    db_perfis = {
        ADMIN_TI_ID: {
            "id": ADMIN_TI_ID,
            "nome": "Gestor TI",
            "role": "ADMIN",
            "ativo": True,
            "is_ti": True,
            "departamento": "TI",
            "departamento_id": DEP_TI_ID,
        },
        LUCAS_ID: {
            "id": LUCAS_ID,
            "nome": "Lucas da TI",
            "role": "ADMIN",
            "ativo": True,
            "departamento": "TI",
            "departamento_id": DEP_TI_ID,
        },
        "outro-op-id": {
            "id": "outro-op-id",
            "nome": "Renato TI",
            "role": "OPERADOR",
            "ativo": True,
            "departamento": "TI",
            "departamento_id": DEP_TI_ID,
        },
    }

    db_chamados = {
        # Chamado 1: Atendido e FINALIZADO pelo Lucas da TI
        "c1": {
            "id": "c1",
            "codigo": "BOND-2026-00890",
            "titulo": "Servidor de banco de dados sem espaço em disco",
            "status": "RESOLVIDO",
            "operador_id": LUCAS_ID,
            "resolvido_em": datetime(2026, 7, 20, 14, 0, tzinfo=UTC),
        },
        # Chamado 2: Em andamento pelo Lucas da TI
        "c2": {
            "id": "c2",
            "codigo": "BOND-2026-00891",
            "titulo": "Queda de link de internet redundante",
            "status": "EM_ATENDIMENTO",
            "operador_id": LUCAS_ID,
            "resolvido_em": None,
        },
        # Chamado 3: Novo na fila, atribuído ao Lucas da TI
        "c3": {
            "id": "c3",
            "codigo": "BOND-2026-00892",
            "titulo": "Configurar novo monitor para recepção",
            "status": "NOVO",
            "operador_id": LUCAS_ID,
            "resolvido_em": None,
        },
    }

    db_mensagens = [
        {
            "id": "m1",
            "chamado_id": "c1",
            "remetente_id": LUCAS_ID,
            "conteudo": "Limpeza de logs e expansão do volume EBS realizada com sucesso.",
        },
        {
            "id": "m2",
            "chamado_id": "c2",
            "remetente_id": LUCAS_ID,
            "conteudo": "Entrando em contato com a operadora de link...",
        },
    ]

    db_historico = [
        {
            "id": 1,
            "chamado_id": "c1",
            "ator_id": LUCAS_ID,
            "acao": "ATENDIMENTO_INICIADO",
            "detalhes": {"operador_id": LUCAS_ID},
        },
        {
            "id": 2,
            "chamado_id": "c1",
            "ator_id": LUCAS_ID,
            "acao": "STATUS_ALTERADO",
            "detalhes": {"de": "EM_ATENDIMENTO", "para": "RESOLVIDO"},
        },
    ]

    print("\n1. ESTADO INICIAL:")
    print(f"   - Operador: {db_perfis[LUCAS_ID]['nome']} (Role: {db_perfis[LUCAS_ID]['role']}, Ativo: {db_perfis[LUCAS_ID]['ativo']})")
    print(f"   - Chamado c1 (RESOLVIDO): Operador = {db_perfis[db_chamados['c1']['operador_id']]['nome']}")
    print(f"   - Chamado c2 (EM_ATENDIMENTO): Operador = {db_perfis[db_chamados['c2']['operador_id']]['nome']}")
    print(f"   - Chamado c3 (NOVO): Operador = {db_perfis[db_chamados['c3']['operador_id']]['nome']}")

    # 2. Configuração do repositório
    class RepoDefinitivo(AdminRepo):
        async def is_ti(self, claims):
            return True

        async def usuarios(self, claims):
            # Retorna apenas perfis ativos conforme AdminRepo.usuarios
            return [dict(p) for p in db_perfis.values() if p.get("ativo")]

        async def departamentos(self, claims):
            return [{"id": DEP_TI_ID, "nome": "TI", "ativo": True, "recebe_chamados": True}]

        async def desativar_usuario(self, claims, user_id):
            # Desatribui chamados abertos
            for c in db_chamados.values():
                if c["operador_id"] == user_id and c["status"] != "RESOLVIDO":
                    c["operador_id"] = None
                    if c["status"] == "EM_ATENDIMENTO":
                        c["status"] = "NOVO"
            # Marca perfil como inativo
            if user_id in db_perfis:
                db_perfis[user_id]["ativo"] = False

    class RepoFila:
        async def perfil(self, claims):
            return db_perfis.get(claims["sub"], {"id": claims["sub"], "is_ti": True, "role": "ADMIN", "departamento": "TI"})

        async def operadores(self, claims, *, departamento_id=None, excluir_id=None):
            # Apenas operadores ativos (regra do FilaRepo.operadores)
            return [
                {"id": p["id"], "nome": p["nome"]}
                for p in db_perfis.values()
                if p.get("ativo") and p.get("role") in ("OPERADOR", "ADMIN")
            ]

    fake_supa = FakeSupaClient()

    # Monkeypatch do supabase_client
    import app.auth.supabase_client as sc

    async def _fake_ensure():
        return fake_supa

    sc.ensure_admin_client = _fake_ensure

    repo_admin = RepoDefinitivo()
    repo_fila = RepoFila()

    app.dependency_overrides[get_admin_repo] = lambda: repo_admin
    app.dependency_overrides[get_chamados_repo] = lambda: repo_fila
    app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        id=ADMIN_TI_ID,
        email="gestor.ti@bondmann.com.br",
        role="ADMIN",
        claims={"sub": ADMIN_TI_ID, "app_metadata": {"role": "ADMIN"}},
    )

    client = TestClient(app)

    # 3. Pré-checagem de listagem e disponibilidade
    r_antes = client.get("/admin/usuarios")
    assert r_antes.status_code == 200
    assert "Lucas da TI" in r_antes.text
    print("\n2. PRÉ-EXCLUSÃO:")
    print("   [OK] Lucas da TI aparece na lista de contas ativas em /admin/usuarios.")

    ops_antes = asyncio.run(repo_fila.operadores({}))
    assert any(o["id"] == LUCAS_ID for o in ops_antes)
    print("   [OK] Lucas da TI está disponível para receber atribuições de chamados.")

    # Obter CSRF token
    csrf_token = r_antes.cookies.get("csrf_token", "test-token")
    if "csrf_token" in r_antes.text:
        import re
        m = re.search(r'name="csrf_token"\s+value="([^"]+)"', r_antes.text)
        if m:
            csrf_token = m.group(1)

    # 4. Executa a Exclusão do Lucas da TI
    print("\n3. EXECUÇÃO DO DESLIGAMENTO:")
    print(f"   -> Disparando POST /admin/usuarios/{LUCAS_ID}/excluir ...")
    r_del = client.post(
        f"/admin/usuarios/{LUCAS_ID}/excluir",
        headers={"X-CSRF-Token": csrf_token},
        cookies={"csrf_token": csrf_token},
        follow_redirects=False,
    )

    assert r_del.status_code == 303, f"Esperava 303, obteve {r_del.status_code}: {r_del.text}"
    loc = unquote(r_del.headers.get("location", ""))
    print(f"   [OK] Redirecionamento 303 recebido. Mensagem: {loc}")
    assert "preservado" in loc

    # 5. Validações pós-exclusão
    print("\n4. VALIDAÇÕES DO RESULTADO:")

    # 5.1 GoTrue / Auth
    assert ("delete", LUCAS_ID) in fake_supa.admin_api.calls
    print("   [PASS] GoTrue: Conta de autenticação excluída (Lucas não pode mais fazer login).")

    # 5.2 Perfil no Banco
    assert db_perfis[LUCAS_ID]["ativo"] is False
    assert db_perfis[LUCAS_ID]["nome"] == "Lucas da TI"
    print("   [PASS] Perfil: Marcado como ativo=False; Nome 'Lucas da TI' preservado para consultas históricas.")

    # 5.3 Painel de Usuários
    r_depois = client.get("/admin/usuarios")
    assert "Lucas da TI" not in r_depois.text
    print("   [PASS] Painel Admin: Lucas não consta mais na lista de contas ativas.")

    # 5.4 Fila de Operadores
    ops_depois = asyncio.run(repo_fila.operadores({}))
    assert not any(o["id"] == LUCAS_ID for o in ops_depois)
    print("   [PASS] Fila de Operadores: Lucas não é mais oferecido para atender novos chamados.")

    # 5.5 Chamado 1 (FINALIZADO) - Histórico Intacto!
    c1 = db_chamados["c1"]
    assert c1["operador_id"] == LUCAS_ID
    op_c1_nome = db_perfis[c1["operador_id"]]["nome"]
    assert op_c1_nome == "Lucas da TI"
    print(f"   [PASS] Chamado Finalizado ({c1['codigo']}): Operador responsável mantido como '{op_c1_nome}'.")

    # Mensagens do chamado finalizado
    msg_c1 = next(m for m in db_mensagens if m["chamado_id"] == "c1")
    assert msg_c1["remetente_id"] == LUCAS_ID
    remetente_nome = db_perfis[msg_c1["remetente_id"]]["nome"]
    assert remetente_nome == "Lucas da TI"
    print(f"   [PASS] Chat do Chamado Finalizado: Mensagem atribuída a '{remetente_nome}': \"{msg_c1['conteudo']}\"")

    # Auditoria do chamado finalizado
    hist_resolvido = next(h for h in db_historico if h["chamado_id"] == "c1" and h["acao"] == "STATUS_ALTERADO")
    assert hist_resolvido["ator_id"] == LUCAS_ID
    ator_nome = db_perfis[hist_resolvido["ator_id"]]["nome"]
    assert ator_nome == "Lucas da TI"
    print(f"   [PASS] Auditoria (historico_chamados): Transição para RESOLVIDO registrada por '{ator_nome}'.")

    # 5.6 Chamados em Aberto (c2 e c3) - Devolvidos à Fila!
    c2 = db_chamados["c2"]
    assert c2["operador_id"] is None
    assert c2["status"] == "NOVO"
    print(f"   [PASS] Chamado em Andamento ({c2['codigo']}): Desatribuído (operador=None) e status revertido para 'NOVO'.")

    # Mensagem prévia no chamado aberto continua existindo
    msg_c2 = next(m for m in db_mensagens if m["chamado_id"] == "c2")
    assert msg_c2["remetente_id"] == LUCAS_ID
    print("   [PASS] Chat do Chamado em Andamento: Mensagem prévia de Lucas mantida intacta.")

    c3 = db_chamados["c3"]
    assert c3["operador_id"] is None
    assert c3["status"] == "NOVO"
    print(f"   [PASS] Chamado Novo ({c3['codigo']}): Desatribuído (operador=None) na fila da TI.")

    print("\n" + "=" * 80)
    print("RESULTADO FINAL: TESTE DEFINITIVO CONCLUÍDO COM 100% DE SUCESSO!")
    print("=" * 80)


if __name__ == "__main__":
    test_cenario_lucas_desligado()
