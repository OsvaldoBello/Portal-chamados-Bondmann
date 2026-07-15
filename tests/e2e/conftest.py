"""Fixtures da suíte e2e de RLS (Sprint 1 / item 1.7, M9) — contra Supabase local real.

Por quê fora do resto da suíte (que usa `FakeRepo`, sem banco): a classe de bug que
este item cobre ("mock verde x banco real divergente", ex.: 0028/chamados_departamento)
só se manifesta quando as policies de RLS realmente rodam no Postgres. Os testes aqui
abrem uma conexão direta (asyncpg) contra `RLS_DATABASE_URL` e reproduzem exatamente o
mecanismo de `app/db.py::_apply_rls_claims` (``SET LOCAL ROLE authenticated`` +
``set_config('request.jwt.claims', ...)``) por persona simulada.

Pré-requisito: Supabase local rodando (`supabase start`, ver `tests/e2e/README.md`).
Sem `RLS_DATABASE_URL` configurada, a suíte inteira é pulada (skip) — não quebra o
`pytest` default de quem não tem Docker/Supabase CLI localmente.

Isolamento: cada teste roda dentro de UMA transação (uma única conexão, sem pool) que
nunca comita — o seed e todas as trocas de persona acontecem dentro dela e o
`ROLLBACK` no teardown já limpa tudo, sem precisar truncar tabela nenhuma entre testes.
"""

from __future__ import annotations

import os
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass

import asyncpg
import pytest
import pytest_asyncio

from app.db import _apply_rls_claims

RLS_DATABASE_URL = os.environ.get("RLS_DATABASE_URL")


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if RLS_DATABASE_URL:
        return
    skip_rls = pytest.mark.skip(
        reason="RLS_DATABASE_URL não configurada — suíte e2e de RLS exige Supabase "
        "local (`supabase start`). Ver tests/e2e/README.md."
    )
    for item in items:
        if "tests/e2e/" in str(item.fspath).replace("\\", "/") or "tests\\e2e\\" in str(item.fspath):
            item.add_marker(skip_rls)


def _uniq(prefix: str) -> str:
    return f"{prefix}.{uuid.uuid4().hex[:8]}@e2e.bondmann.test"


async def _criar_usuario(
    conn: asyncpg.Connection,
    *,
    nome: str,
    role: str = "CLIENTE",
    departamento_id: uuid.UUID | None = None,
) -> uuid.UUID:
    """Cria um usuário real (`auth.users` + `perfis`, via o mesmo trigger `handle_new_user`
    documentado em `docs/tutorial_usuarios.md`), já promovido ao papel/departamento pedidos.

    Roda com a role da conexão (superuser/dono das tabelas) — sem RLS, igual a um
    `admin_connection()` de produção.
    """
    uid = uuid.uuid4()
    email = _uniq(nome.lower().replace(" ", "."))
    await conn.execute(
        """
        INSERT INTO auth.users (
          instance_id, id, aud, role, email, encrypted_password,
          email_confirmed_at, created_at, updated_at,
          raw_app_meta_data, raw_user_meta_data,
          confirmation_token, recovery_token, email_change_token_new, email_change
        ) VALUES (
          '00000000-0000-0000-0000-000000000000', $1, 'authenticated', 'authenticated',
          $2, crypt('Teste@2026', gen_salt('bf')),
          now(), now(), now(),
          jsonb_build_object('provider', 'email', 'providers', ARRAY['email'], 'role', $3::text),
          jsonb_build_object('nome', $4::text),
          '', '', '', ''
        )
        """,
        uid,
        email,
        role,
        nome,
    )
    # handle_new_user (trigger) já criou a linha em `perfis` com role=CLIENTE — promove.
    await conn.execute(
        "UPDATE perfis SET role = $2::papel_usuario, departamento_id = $3 WHERE id = $1",
        uid,
        role,
        departamento_id,
    )
    return uid


async def _criar_chamado(
    conn: asyncpg.Connection,
    *,
    empresa_id: uuid.UUID,
    cliente_id: uuid.UUID,
    departamento_id: uuid.UUID,
    operador_id: uuid.UUID | None = None,
    titulo: str = "Chamado de teste e2e",
) -> uuid.UUID:
    return await conn.fetchval(
        """
        INSERT INTO chamados (empresa_id, cliente_id, departamento_id, titulo, descricao, operador_id)
        VALUES ($1, $2, $3, $4, 'descrição de teste e2e', $5)
        RETURNING id
        """,
        empresa_id,
        cliente_id,
        departamento_id,
        titulo,
        operador_id,
    )


@dataclass
class Seed:
    empresa_id: uuid.UUID
    dept_ti: uuid.UUID
    dept_rh: uuid.UUID
    dept_marketing: uuid.UUID
    dept_comercial: uuid.UUID

    autor_ti: uuid.UUID  # funcionário comum (CLIENTE), abre chamado para a fila do TI
    autor_comercial: uuid.UUID  # funcionário do setor Comercial (perfis.departamento_id, 0028)
    staff_ti: uuid.UUID  # ADMIN + departamento TI == acesso total (auth_is_ti())
    staff_rh: uuid.UUID  # OPERADOR + departamento RH
    staff_marketing: uuid.UUID  # OPERADOR + departamento Marketing
    lider_comercial: uuid.UUID  # ADMIN + departamento Comercial (setor SEM fila, 0028)

    chamado_ti: uuid.UUID  # autor_ti -> TI
    chamado_rh: uuid.UUID  # autor_comercial -> RH (o líder do Comercial deve acompanhar)
    chamado_marketing_auto: uuid.UUID  # staff_marketing é autor E destino Marketing (0038)
    chamado_rh_auto: uuid.UUID  # staff_rh é autor E destino RH (0042)
    chamado_ti_sem_auto: uuid.UUID  # staff_ti é autor E destino TI (TI não tem autoatendimento)

    msg_publica: uuid.UUID
    msg_interna: uuid.UUID


def claims_for(user_id: uuid.UUID) -> dict:
    """Claims mínimos para `auth.uid()`/RLS — só `sub` importa (Seção 2.1 / app/db.py).

    `auth_role()`/`auth_departamento_id()` leem `perfis` no banco, não o claim de role
    do JWT — então não precisamos simular `app_metadata.role` aqui para a RLS reagir
    corretamente (isso só importa para o gate de autorização da camada FastAPI).
    """
    return {"sub": str(user_id)}


@asynccontextmanager
async def as_user(conn: asyncpg.Connection, user_id: uuid.UUID):
    """Troca a conexão para a persona `user_id` (mesmo mecanismo de `rls_connection`),
    roda o bloco, e devolve a conexão à role de superusuário ao sair — para que o
    próximo `as_user`/seed na mesma transação não fique preso em `authenticated`."""
    await _apply_rls_claims(conn, claims_for(user_id))
    try:
        yield conn
    finally:
        await conn.execute("RESET ROLE; SELECT set_config('request.jwt.claims', '', true)")


def rowcount(status: str) -> int:
    """`asyncpg.Connection.execute()` devolve algo como ``"UPDATE 1"`` — extrai o N."""
    return int(status.rsplit(" ", 1)[-1])


@pytest_asyncio.fixture
async def conn():
    connection = await asyncpg.connect(RLS_DATABASE_URL)
    tx = connection.transaction()
    await tx.start()
    try:
        yield connection
    finally:
        await tx.rollback()
        await connection.close()


@pytest_asyncio.fixture
async def seed(conn: asyncpg.Connection) -> Seed:
    dept_ids = {}
    for nome in ("TI", "RH", "Marketing", "Comercial"):
        dept_ids[nome] = await conn.fetchval("SELECT id FROM departamentos WHERE nome = $1", nome)
        assert dept_ids[nome], f"departamento seed '{nome}' ausente — migrations 0008/0027 não aplicadas?"

    empresa_id = await conn.fetchval("SELECT id FROM empresas ORDER BY created_at LIMIT 1")
    assert empresa_id, "empresa seed ausente — migration 0008 não aplicada?"

    autor_ti = await _criar_usuario(conn, nome="Autor TI")
    autor_comercial = await _criar_usuario(
        conn, nome="Autor Comercial", departamento_id=dept_ids["Comercial"]
    )
    staff_ti = await _criar_usuario(conn, nome="Staff TI", role="ADMIN", departamento_id=dept_ids["TI"])
    staff_rh = await _criar_usuario(conn, nome="Staff RH", role="OPERADOR", departamento_id=dept_ids["RH"])
    staff_marketing = await _criar_usuario(
        conn, nome="Staff Marketing", role="OPERADOR", departamento_id=dept_ids["Marketing"]
    )
    lider_comercial = await _criar_usuario(
        conn, nome="Lider Comercial", role="ADMIN", departamento_id=dept_ids["Comercial"]
    )

    chamado_ti = await _criar_chamado(
        conn, empresa_id=empresa_id, cliente_id=autor_ti, departamento_id=dept_ids["TI"]
    )
    chamado_rh = await _criar_chamado(
        conn, empresa_id=empresa_id, cliente_id=autor_comercial, departamento_id=dept_ids["RH"]
    )
    chamado_marketing_auto = await _criar_chamado(
        conn, empresa_id=empresa_id, cliente_id=staff_marketing, departamento_id=dept_ids["Marketing"]
    )
    chamado_rh_auto = await _criar_chamado(
        conn, empresa_id=empresa_id, cliente_id=staff_rh, departamento_id=dept_ids["RH"]
    )
    chamado_ti_sem_auto = await _criar_chamado(
        conn, empresa_id=empresa_id, cliente_id=staff_ti, departamento_id=dept_ids["TI"]
    )

    msg_publica = await conn.fetchval(
        "INSERT INTO mensagens (chamado_id, remetente_id, conteudo, is_interna) "
        "VALUES ($1, $2, 'mensagem pública para o autor', false) RETURNING id",
        chamado_ti,
        staff_ti,
    )
    msg_interna = await conn.fetchval(
        "INSERT INTO mensagens (chamado_id, remetente_id, conteudo, is_interna) "
        "VALUES ($1, $2, 'nota interna entre staff', true) RETURNING id",
        chamado_ti,
        staff_ti,
    )

    return Seed(
        empresa_id=empresa_id,
        dept_ti=dept_ids["TI"],
        dept_rh=dept_ids["RH"],
        dept_marketing=dept_ids["Marketing"],
        dept_comercial=dept_ids["Comercial"],
        autor_ti=autor_ti,
        autor_comercial=autor_comercial,
        staff_ti=staff_ti,
        staff_rh=staff_rh,
        staff_marketing=staff_marketing,
        lider_comercial=lider_comercial,
        chamado_ti=chamado_ti,
        chamado_rh=chamado_rh,
        chamado_marketing_auto=chamado_marketing_auto,
        chamado_rh_auto=chamado_rh_auto,
        chamado_ti_sem_auto=chamado_ti_sem_auto,
        msg_publica=msg_publica,
        msg_interna=msg_interna,
    )
