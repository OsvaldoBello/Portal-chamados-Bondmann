"""Repositório de chamados — acesso de domínio sob RLS (Seções 3.1 / 5.1).

Cada método abre uma transação curta via :func:`rls_connection`, injetando os
claims do usuário (``SET LOCAL ROLE authenticated`` + ``request.jwt.claims``).
Toda autorização/isolamento multi-tenant é imposta pelo RLS no banco — as
queries aqui não recriam regra de negócio, apenas leem/escrevem o que o papel
do usuário tem permissão de ver.

O repositório é resolvido por dependência (:func:`get_chamados_repo`) para que
as rotas possam ser testadas com um fake, sem banco vivo.

`ChamadosRepo` é uma **fachada** (Sprint 2 / item 2.1, M1): a implementação de
cada domínio foi extraída para `app/repositories/catalogo.py`,
`mensagens.py`, `fila.py` e `atendimento.py`. A fachada delega para essas
classes e mantém as quatro operações de perfil/self-service (`perfil`,
`atualizar_avatar`, `listar`, `stats`) diretamente, já que não se encaixam
com folga em nenhum dos quatro domínios. Isso preserva o mesmo import
(`from app.repositories.chamados import ChamadosRepo, get_chamados_repo, ...`)
e a mesma superfície de métodos para todas as rotas e testes existentes —
nenhum caller precisa mudar.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any

from app.db import rls_connection
from app.repositories.atendimento import AtendimentoRepo
from app.repositories.catalogo import (
    CACHE_CATEGORIAS,
    CACHE_DEPARTAMENTOS,
    CACHE_SUBCATEGORIAS,
    CATALOGO_TTL,
    CatalogoRepo,
)
from app.repositories.fila import FilaRepo
from app.repositories.mensagens import MensagensRepo

__all__ = [
    "ChamadosRepo",
    "get_chamados_repo",
    "validar_nota",
    "validar_comentario_avaliacao",
    "validar_telefone_contato",
    "PRIORIDADES",
    "CACHE_CATEGORIAS",
    "CACHE_DEPARTAMENTOS",
    "CACHE_SUBCATEGORIAS",
    "CATALOGO_TTL",
]

PRIORIDADES = ("BAIXA", "MEDIA", "ALTA", "URGENTE")
NOTA_MIN, NOTA_MAX = 1, 5
# Nota a partir da qual o comentário deixa de ser opcional (Seção 5.1 —
# pedido do usuário 2026-07-24): 4 estrelas ou menos exige que o autor
# descreva o motivo e o que pode melhorar, com um mínimo de substância.
NOTA_COMENTARIO_OBRIGATORIO = 4
COMENTARIO_MIN_CHARS = 50
TELEFONE_MIN_DIGITOS = 8


class ChamadosRepo:
    """Operações de leitura/escrita de chamados em nome do usuário autenticado."""

    def __init__(self) -> None:
        self._catalogo = CatalogoRepo()
        self._mensagens = MensagensRepo()
        self._fila = FilaRepo()
        self._atendimento = AtendimentoRepo()

    # -- Perfil / self-service (não delegado — ver docstring do módulo) -----

    async def perfil(self, claims: dict) -> dict[str, Any] | None:
        async with rls_connection(claims) as conn:
            row = await conn.fetchrow(
                """SELECT p.id, p.nome, p.role, p.empresa_id, p.departamento_id,
                          p.avatar_path, p.updated_at AS avatar_atualizado_em,
                          d.nome AS departamento,
                          COALESCE(d.nome = 'TI', false) AS is_ti,
                          COALESCE(d.recebe_chamados, false) AS recebe_chamados
                     FROM perfis p
                     LEFT JOIN departamentos d ON d.id = p.departamento_id
                    WHERE p.id = $1::uuid""",
                claims["sub"],
            )
            return dict(row) if row else None

    async def atualizar_avatar(self, claims: dict, *, avatar_path: str | None) -> None:
        """Grava o path do avatar do PRÓPRIO usuário (Fase 7). RLS nova
        (`perfis_update_self`, migration 0033) só deixa autenticado atualizar o
        próprio perfil, e o trigger `enforce_perfil_self_so_avatar` restringe essa
        escrita à coluna `avatar_path` — o path em si é sempre
        `{auth.uid()}/avatar.<ext>` (`app/avatar_storage.py`), então não há como
        um usuário apontar para o avatar de outro."""
        async with rls_connection(claims) as conn:
            await conn.execute(
                "UPDATE perfis SET avatar_path = $2 WHERE id = $1::uuid",
                claims["sub"],
                avatar_path,
            )

    async def listar(self, claims: dict, *, limite: int = 100) -> list[dict[str, Any]]:
        """"Meus chamados": os que o usuário ABRIU (portal do solicitante).

        Filtra por `cliente_id = auth.uid()` para que também o staff (que via RLS
        enxerga a fila do seu setor) veja aqui apenas os próprios. A fila de
        atendimento por departamento é o Workspace (Fase 4)."""
        async with rls_connection(claims) as conn:
            rows = await conn.fetch(
                """
                SELECT c.id, c.codigo, c.titulo, c.status, c.prioridade,
                       c.created_at, c.limite_resolucao, c.avaliacao_nota,
                       cat.nome AS categoria, dep.nome AS departamento
                  FROM chamados c
                  LEFT JOIN categorias cat ON cat.id = c.categoria_id
                  LEFT JOIN departamentos dep ON dep.id = c.departamento_id
                 WHERE c.cliente_id = $1::uuid
                 ORDER BY c.created_at DESC
                 LIMIT $2
                """,
                claims["sub"],
                limite,
            )
            return [dict(r) for r in rows]

    async def stats(self, claims: dict) -> dict[str, int]:
        async with rls_connection(claims) as conn:
            rows = await conn.fetch(
                "SELECT status, count(*) AS n FROM chamados WHERE cliente_id = $1::uuid GROUP BY status",
                claims["sub"],
            )
        por_status = {r["status"]: r["n"] for r in rows}
        return {
            "total": sum(por_status.values()),
            "novo": por_status.get("NOVO", 0),
            "em_atendimento": por_status.get("EM_ATENDIMENTO", 0),
            "aguardando": por_status.get("AGUARDANDO", 0),
            "resolvido": por_status.get("RESOLVIDO", 0),
        }

    # -- CatalogoRepo ---------------------------------------------------------

    async def categorias_ativas(
        self, claims: dict, departamento_id: str | None = None
    ) -> list[dict[str, Any]]:
        return await self._catalogo.categorias_ativas(claims, departamento_id)

    async def categoria_valida(
        self, claims: dict, *, categoria_id: str, departamento_id: str
    ) -> bool:
        return await self._catalogo.categoria_valida(
            claims, categoria_id=categoria_id, departamento_id=departamento_id
        )

    async def nome_categoria(self, claims: dict, categoria_id: str) -> str | None:
        return await self._catalogo.nome_categoria(claims, categoria_id)

    async def departamentos_ativos(self, claims: dict) -> list[dict[str, Any]]:
        return await self._catalogo.departamentos_ativos(claims)

    async def departamentos_destino_ativos(self, claims: dict) -> list[dict[str, Any]]:
        return await self._catalogo.departamentos_destino_ativos(claims)

    async def subcategorias_ativas(
        self, claims: dict, categoria_id: str
    ) -> list[dict[str, Any]]:
        return await self._catalogo.subcategorias_ativas(claims, categoria_id)

    async def subcategoria_valida(
        self, claims: dict, *, categoria_id: str, subcategoria_id: str
    ) -> bool:
        return await self._catalogo.subcategoria_valida(
            claims, categoria_id=categoria_id, subcategoria_id=subcategoria_id
        )

    # -- MensagensRepo ---------------------------------------------------------

    async def mensagens(self, claims: dict, chamado_id: str) -> list[dict[str, Any]]:
        return await self._mensagens.mensagens(claims, chamado_id)

    async def mensagens_assinatura(self, claims: dict, chamado_id: str):
        return await self._mensagens.mensagens_assinatura(claims, chamado_id)

    async def adicionar_mensagem(
        self,
        claims: dict,
        chamado_id: str,
        *,
        remetente_id: str,
        conteudo: str,
        anexos: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        return await self._mensagens.adicionar_mensagem(
            claims, chamado_id, remetente_id=remetente_id, conteudo=conteudo, anexos=anexos
        )

    async def responder_staff(
        self,
        claims: dict,
        chamado_id: str,
        *,
        conteudo: str,
        is_interna: bool,
        anexos: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any] | None:
        return await self._mensagens.responder_staff(
            claims, chamado_id, conteudo=conteudo, is_interna=is_interna, anexos=anexos
        )

    async def notificacoes(self, claims: dict, *, limite: int = 6) -> list[dict[str, Any]]:
        return await self._mensagens.notificacoes(claims, limite=limite)

    async def marcar_notificacao_vista(self, claims: dict, chamado_id: str) -> None:
        return await self._mensagens.marcar_notificacao_vista(claims, chamado_id)

    async def usuarios_para_copia(
        self, claims: dict, *, excluir_id: str | None = None
    ) -> list[dict[str, Any]]:
        return await self._mensagens.usuarios_para_copia(claims, excluir_id=excluir_id)

    async def observadores(self, claims: dict, chamado_id: str) -> list[dict[str, Any]]:
        return await self._mensagens.observadores(claims, chamado_id)

    async def adicionar_observador(
        self, claims: dict, chamado_id: str, perfil_id: str
    ) -> None:
        return await self._mensagens.adicionar_observador(claims, chamado_id, perfil_id)

    async def remover_observador(
        self, claims: dict, chamado_id: str, perfil_id: str
    ) -> None:
        return await self._mensagens.remover_observador(claims, chamado_id, perfil_id)

    # -- FilaRepo ---------------------------------------------------------

    async def fila(
        self,
        claims: dict,
        *,
        departamento_id: str | None,
        status: str | None = None,
        categoria_id: str | None = None,
        prioridade: str | None = None,
        operador_id: str | None = None,
        setor: str | None = None,
        data_de: date | None = None,
        data_ate: date | None = None,
        busca: str | None = None,
        limite: int = 200,
    ) -> list[dict[str, Any]]:
        return await self._fila.fila(
            claims,
            departamento_id=departamento_id,
            status=status,
            categoria_id=categoria_id,
            prioridade=prioridade,
            operador_id=operador_id,
            setor=setor,
            data_de=data_de,
            data_ate=data_ate,
            busca=busca,
            limite=limite,
        )

    async def setores_ativos(self, claims: dict, departamento_id: str | None = None) -> list[str]:
        return await self._fila.setores_ativos(claims, departamento_id)

    async def chamados_departamento(
        self,
        claims: dict,
        *,
        departamento_id: str | None,
        status: str | None = None,
        categoria_id: str | None = None,
        prioridade: str | None = None,
        limite: int = 200,
    ) -> list[dict[str, Any]]:
        return await self._fila.chamados_departamento(
            claims,
            departamento_id=departamento_id,
            status=status,
            categoria_id=categoria_id,
            prioridade=prioridade,
            limite=limite,
        )

    async def fila_assinatura(
        self, claims: dict, *, departamento_id: str | None, status: str | None = None
    ):
        return await self._fila.fila_assinatura(claims, departamento_id=departamento_id, status=status)

    async def fila_stats(self, claims: dict, *, departamento_id: str | None = None) -> dict[str, int]:
        return await self._fila.fila_stats(claims, departamento_id=departamento_id)

    async def operadores(
        self, claims: dict, *, departamento_id: str | None = None, excluir_id: str | None = None
    ) -> list[dict[str, Any]]:
        return await self._fila.operadores(
            claims, departamento_id=departamento_id, excluir_id=excluir_id
        )

    # -- AtendimentoRepo ---------------------------------------------------------

    async def obter(self, claims: dict, chamado_id: str) -> dict[str, Any] | None:
        return await self._atendimento.obter(claims, chamado_id)

    async def criar(
        self,
        claims: dict,
        *,
        empresa_id: str,
        cliente_id: str,
        categoria_id: str | None,
        subcategoria_id: str | None,
        departamento_id: str,
        titulo: str,
        descricao: str,
        prioridade: str,
        setor: str,
        telefone_contato: str,
        data_entrega: date | None = None,
        volume: int = 1,
        origem_demanda: str = "Solicitação",
        sem_prazo: bool = False,
        dados_formulario: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return await self._atendimento.criar(
            claims,
            empresa_id=empresa_id,
            cliente_id=cliente_id,
            categoria_id=categoria_id,
            subcategoria_id=subcategoria_id,
            departamento_id=departamento_id,
            titulo=titulo,
            descricao=descricao,
            prioridade=prioridade,
            setor=setor,
            telefone_contato=telefone_contato,
            data_entrega=data_entrega,
            volume=volume,
            origem_demanda=origem_demanda,
            sem_prazo=sem_prazo,
            dados_formulario=dados_formulario,
        )

    async def avaliar(
        self, claims: dict, chamado_id: str, *, nota: int, comentario: str | None
    ) -> dict[str, Any] | None:
        return await self._atendimento.avaliar(claims, chamado_id, nota=nota, comentario=comentario)

    async def reabrir(self, claims: dict, chamado_id: str) -> dict[str, Any] | None:
        return await self._atendimento.reabrir(claims, chamado_id)

    async def ia_triagem_nota(self, claims: dict, chamado_id: str) -> dict[str, Any] | None:
        return await self._atendimento.ia_triagem_nota(claims, chamado_id)

    async def avaliar_ia_triagem(
        self, claims: dict, chamado_id: str, *, triagem_id: int, nota: int, avaliador_id: str
    ) -> bool:
        return await self._atendimento.avaliar_ia_triagem(
            claims, chamado_id, triagem_id=triagem_id, nota=nota, avaliador_id=avaliador_id
        )

    async def iniciar_atendimento(
        self, claims: dict, chamado_id: str, *, operador_id: str, novo_status: str = "EM_ATENDIMENTO"
    ) -> dict[str, Any] | None:
        return await self._atendimento.iniciar_atendimento(
            claims, chamado_id, operador_id=operador_id, novo_status=novo_status
        )

    async def transferir(
        self, claims: dict, chamado_id: str, *, departamento_id: str
    ) -> dict[str, Any] | None:
        return await self._atendimento.transferir(claims, chamado_id, departamento_id=departamento_id)

    async def avaliacao_pendente(self, claims: dict) -> dict[str, Any] | None:
        return await self._atendimento.avaliacao_pendente(claims)

    async def alterar_status(
        self, claims: dict, chamado_id: str, novo_status: str
    ) -> dict[str, Any] | None:
        return await self._atendimento.alterar_status(claims, chamado_id, novo_status)

    async def alterar_prioridade(
        self, claims: dict, chamado_id: str, nova_prioridade: str
    ) -> dict[str, Any] | None:
        return await self._atendimento.alterar_prioridade(claims, chamado_id, nova_prioridade)

    async def atribuir(
        self, claims: dict, chamado_id: str, operador_id: str | None
    ) -> dict[str, Any] | None:
        return await self._atendimento.atribuir(claims, chamado_id, operador_id)

    async def alterar_categoria(
        self, claims: dict, chamado_id: str, *,
        categoria_id: str | None, subcategoria_id: str | None,
    ) -> dict[str, Any] | None:
        return await self._atendimento.alterar_categoria(
            claims, chamado_id, categoria_id=categoria_id, subcategoria_id=subcategoria_id
        )

    async def excluir(self, claims: dict, chamado_id: str) -> bool:
        return await self._atendimento.excluir(claims, chamado_id)

    async def salvar_marketing_meta(
        self, claims: dict, chamado_id: str, *, volume: int, origem_demanda: str, causa_atraso: str | None
    ) -> dict[str, Any] | None:
        return await self._atendimento.salvar_marketing_meta(
            claims, chamado_id, volume=volume, origem_demanda=origem_demanda, causa_atraso=causa_atraso
        )


_repo = ChamadosRepo()


def get_chamados_repo() -> ChamadosRepo:
    """Dependência FastAPI; sobreposta nos testes por um fake."""
    return _repo


def validar_nota(raw: str | int | None) -> int:
    """Valida e normaliza a nota de avaliação (1–5). Levanta ``ValueError``."""
    try:
        nota = int(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        raise ValueError("Nota inválida: informe um número de 1 a 5.")
    if not (NOTA_MIN <= nota <= NOTA_MAX):
        raise ValueError("Nota fora do intervalo: use de 1 a 5 estrelas.")
    return nota


def validar_comentario_avaliacao(nota: int, comentario: str) -> str | None:
    """Exige comentário com pelo menos ``COMENTARIO_MIN_CHARS`` caracteres para
    notas de ``NOTA_COMENTARIO_OBRIGATORIO`` estrelas ou menos — o autor deve
    dizer por que essa nota e o que pode melhorar. Acima disso, o comentário
    continua opcional. Levanta ``ValueError``; devolve o comentário limpo (ou
    ``None`` se vazio e a nota não exige)."""
    comentario_limpo = comentario.strip()
    if nota <= NOTA_COMENTARIO_OBRIGATORIO and len(comentario_limpo) < COMENTARIO_MIN_CHARS:
        raise ValueError(
            "Para notas de 4 estrelas ou menos, descreva em pelo menos "
            f"{COMENTARIO_MIN_CHARS} caracteres o motivo da nota e o que pode melhorar."
        )
    return comentario_limpo or None


def validar_telefone_contato(raw: str) -> str:
    """Valida o telefone de contato exigido na abertura do chamado (mínimo de
    ``TELEFONE_MIN_DIGITOS`` dígitos, ignorando formatação — DDD/traço/parênteses
    ficam livres). Levanta ``ValueError``; devolve o texto já sem espaços nas
    pontas."""
    valor = raw.strip()
    digitos = re.sub(r"\D", "", valor)
    if len(digitos) < TELEFONE_MIN_DIGITOS:
        raise ValueError("Informe um número de contato válido (telefone ou celular).")
    return valor
