"""Busca FTS de chamados semelhantes (migration 0052 — F3 da frente de IA) contra Postgres real.

DoD F3 (plano_md_mestre_IA.md, Seção 7): com corpus semeado, a busca acha o
semelhante certo, não vaza chamado de outro departamento (filtro obrigatório
no SQL — a conexão aqui é a de superusuário, equivalente ao admin_connection()
do motor, então NENHUMA RLS ajuda: o teste prova a defesa em profundidade) e
só considera chamados RESOLVIDO.
"""

from __future__ import annotations

import uuid

import asyncpg
import pytest

from app.repositories.ia_busca import buscar_semelhantes
from tests.e2e.conftest import Seed

pytestmark = pytest.mark.rls


async def _chamado_resolvido(
    conn: asyncpg.Connection,
    seed: Seed,
    *,
    departamento_id: uuid.UUID,
    titulo: str,
    descricao: str,
    resolucao: str | None = None,
    staff_id: uuid.UUID | None = None,
    status: str = "RESOLVIDO",
) -> uuid.UUID:
    cid = await conn.fetchval(
        f"""
        INSERT INTO chamados (empresa_id, cliente_id, departamento_id, titulo, descricao,
                              status, operador_id, resolvido_em)
        VALUES ($1, $2, $3, $4, $5, '{status}',
                $6, CASE WHEN '{status}' = 'RESOLVIDO' THEN now() END)
        RETURNING id
        """,
        seed.empresa_id,
        seed.autor_ti,
        departamento_id,
        titulo,
        descricao,
        staff_id,
    )
    if resolucao:
        # Resolução registrada = última mensagem pública de STAFF (Seção 4.4).
        await conn.execute(
            "INSERT INTO mensagens (chamado_id, remetente_id, conteudo, is_interna) "
            "VALUES ($1, $2, $3, false)",
            cid,
            staff_id or seed.staff_ti,
            resolucao,
        )
    return cid


async def test_acha_o_semelhante_certo_com_resolucao(conn: asyncpg.Connection, seed: Seed):
    certo = await _chamado_resolvido(
        conn,
        seed,
        departamento_id=seed.dept_ti,
        titulo="Erro ao emitir NF-e após atualização",
        descricao="Falha do certificado digital A1 na emissão da nota fiscal.",
        resolucao="Reinstalado o certificado A1 e reconfigurado o emissor.",
        staff_id=seed.staff_ti,
    )
    await _chamado_resolvido(
        conn,
        seed,
        departamento_id=seed.dept_ti,
        titulo="Impressora atolando papel",
        descricao="A impressora da recepção atola papel toda manhã.",
        resolucao="Substituído o rolete de tração.",
        staff_id=seed.staff_ti,
    )

    resultados = await buscar_semelhantes(
        conn,
        departamento_id=seed.dept_ti,
        chamado_id=str(seed.chamado_ti),
        termos=["certificado digital", "NF-e"],
    )
    assert [r["id"] for r in resultados] == [str(certo)]
    assert "Reinstalado o certificado A1" in resultados[0]["resolucao"]
    assert resultados[0]["codigo"]  # código BOND gerado pelo trigger


async def test_acha_pela_resolucao_quando_titulo_e_descricao_nao_casam(
    conn: asyncpg.Connection, seed: Seed
):
    """A resolução registrada entra na busca: um chamado cujo sintoma (título +
    descrição) não menciona o termo, mas cuja SOLUÇÃO aplicada sim, é encontrado
    e citado. Cobre a inclusão da resolução no ``tsvector`` (Seção 5)."""
    achado = await _chamado_resolvido(
        conn,
        seed,
        departamento_id=seed.dept_ti,
        titulo="Sistema de notas travando",
        descricao="O ERP fecha sozinho ao gerar documento fiscal.",
        resolucao="Reinstalado o certificado digital A1 e o emissor voltou a operar.",
        staff_id=seed.staff_ti,
    )
    resultados = await buscar_semelhantes(
        conn,
        departamento_id=seed.dept_ti,
        chamado_id=str(seed.chamado_ti),
        termos=["certificado digital A1"],
    )
    assert [r["id"] for r in resultados] == [str(achado)]
    assert "certificado digital A1" in resultados[0]["resolucao"]


async def test_nao_vaza_chamado_de_outro_departamento(conn: asyncpg.Connection, seed: Seed):
    """O mesmo vocabulário em outro departamento fica fora — o filtro por
    `departamento_id` no SQL é a única barreira (conexão administrativa)."""
    await _chamado_resolvido(
        conn,
        seed,
        departamento_id=seed.dept_rh,
        titulo="Erro no certificado digital do eSocial",
        descricao="Certificado digital expirado no envio do eSocial.",
        resolucao="Renovado o certificado junto à certificadora.",
        staff_id=seed.staff_rh,
    )
    resultados = await buscar_semelhantes(
        conn,
        departamento_id=seed.dept_ti,
        chamado_id=str(seed.chamado_ti),
        termos=["certificado digital"],
    )
    assert resultados == []


async def test_chamado_nao_resolvido_e_o_proprio_ficam_fora(
    conn: asyncpg.Connection, seed: Seed
):
    await _chamado_resolvido(
        conn,
        seed,
        departamento_id=seed.dept_ti,
        titulo="Computador não liga sem vídeo",
        descricao="Máquina não dá vídeo desde ontem.",
        status="NOVO",
    )
    novo = await _chamado_resolvido(
        conn,
        seed,
        departamento_id=seed.dept_ti,
        titulo="Computador não liga",
        descricao="Sem vídeo ao ligar.",
        status="NOVO",
    )
    resultados = await buscar_semelhantes(
        conn,
        departamento_id=seed.dept_ti,
        chamado_id=str(novo),
        termos=["não liga", "sem vídeo"],
    )
    assert resultados == []  # nada RESOLVIDO no corpus ⇒ nada citado


async def test_semelhante_sem_mensagem_de_staff_vem_sem_resolucao(
    conn: asyncpg.Connection, seed: Seed
):
    """Chamado resolvido sem mensagem pública de staff (ex.: resolvido por
    telefone) ainda é citável — `resolucao` vem NULL e a nota cita só o código."""
    cid = await _chamado_resolvido(
        conn,
        seed,
        departamento_id=seed.dept_ti,
        titulo="VPN não conecta no cliente novo",
        descricao="Erro de autenticação na VPN corporativa.",
        staff_id=seed.staff_ti,
    )
    resultados = await buscar_semelhantes(
        conn,
        departamento_id=seed.dept_ti,
        chamado_id=str(seed.chamado_ti),
        termos=["VPN"],
    )
    assert [r["id"] for r in resultados] == [str(cid)]
    assert resultados[0]["resolucao"] is None
