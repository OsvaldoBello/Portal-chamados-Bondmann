"""Serviço de regras de negócio do Admin — vínculo usuário↔departamento e
dual-write de papel (Sprint 2 / item 2.2, M2, fase admin).

Concentra: (a) a validação de departamento usada tanto para vincular
CATEGORIAS quanto para vincular PAPEL/SETOR de um usuário, antes duas funções
quase-idênticas em `app/routes/admin.py` (`_depto_valido`/
`_depto_perfil_valido`) — mesma checagem base (existe + ativo), uma delas com
a exigência extra "OPERADOR só em setor com fila"; (b) a orquestração de
dual-write de papel (grava em `perfis` → espelha em `app_metadata.role` via
Admin API → relê os dois lados → decide a mensagem), hoje 100% na rota
`mudar_papel` — nenhuma parte estava no `AdminRepo` (item 1.5, que só expõe a
leitura/escrita crua de `perfis`).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

log = logging.getLogger("app.services.admin")


@dataclass(frozen=True)
class ResultadoPapel:
    sucesso: bool
    mensagem: str


class AdminService:
    """Regras de negócio da gestão de usuários/catálogos (Fase 5 — admin)."""

    @staticmethod
    def departamento_valido(
        departamentos: list[dict], dep_id: str, *, exigir_fila: bool
    ) -> str | None:
        """Retorna o id do departamento se existir, estiver ativo e (quando
        ``exigir_fila``) receber chamado; senão ``None``.

        Unifica as duas validações que existiam na rota: categorias sempre
        exigem fila — só fazem sentido num setor com atendimento (0027) — e o
        vínculo de papel só exige fila para OPERADOR (é quem atende a fila;
        os demais papéis podem ter setor de origem sem fila, 0028).
        """
        dep_id = (dep_id or "").strip()
        if not dep_id:
            return None
        for d in departamentos:
            if str(d["id"]) != dep_id or not d.get("ativo"):
                continue
            if exigir_fila and not d.get("recebe_chamados"):
                return None
            return dep_id
        return None

    @staticmethod
    async def promover_papel(
        *,
        repo: Any,
        claims: dict,
        user_id: str,
        papel: str,
        departamento_id: str,
        client: Any,
    ) -> ResultadoPapel:
        """Grava o papel em ``perfis``, espelha em ``app_metadata.role`` (JWT)
        e relê os dois lados para confirmar.

        Sprint 1 / item 1.5 (M12): a etapa de espelhamento podia falhar em
        silêncio antes desta releitura — a divergência só aparecia num
        incidente de permissão depois.
        """
        await repo.atualizar_papel(claims, user_id, role=papel, departamento_id=departamento_id)

        if client is not None:
            try:
                await client.auth.admin.update_user_by_id(user_id, {"app_metadata": {"role": papel}})
            except Exception as exc:  # noqa: BLE001 — perfis já atualizado; confirmado abaixo
                log.error(
                    "Dual-write de papel: falha ao atualizar app_metadata.role (user_id=%s alvo=%s): %s",
                    user_id, papel, exc,
                )
        else:
            log.error(
                "Dual-write de papel: service_role não configurada — app_metadata.role não "
                "pôde ser sincronizado (user_id=%s alvo=%s).",
                user_id, papel,
            )

        papel_no_banco = await repo.obter_papel(claims, user_id)
        papel_no_jwt = None
        if client is not None:
            try:
                res = await client.auth.admin.get_user_by_id(user_id)
                u = getattr(res, "user", res)
                papel_no_jwt = (getattr(u, "app_metadata", None) or {}).get("role")
            except Exception as exc:  # noqa: BLE001 — não bloqueia a resposta, só perde a confirmação
                log.warning(
                    "Não foi possível reler app_metadata.role para confirmar (user_id=%s): %s",
                    user_id, exc,
                )

        if papel_no_banco != papel:
            log.error(
                "Divergência de papel após promoção: perfis.role=%r ≠ alvo=%r (user_id=%s).",
                papel_no_banco, papel, user_id,
            )
            return ResultadoPapel(
                sucesso=False, mensagem="Falha ao gravar o papel no banco. Tente novamente."
            )
        if client is not None and papel_no_jwt is not None and papel_no_jwt != papel:
            log.error(
                "Divergência de papel após promoção: app_metadata.role=%r ≠ alvo=%r (user_id=%s).",
                papel_no_jwt, papel, user_id,
            )
            return ResultadoPapel(
                sucesso=True,
                mensagem=(
                    "Papel atualizado no banco, mas o JWT (app_metadata) ficou com um valor "
                    "diferente — peça para o usuário deslogar e logar de novo; se persistir, "
                    "verifique manualmente."
                ),
            )
        return ResultadoPapel(
            sucesso=True, mensagem="Papel atualizado. A mudança vale no próximo login do usuário."
        )
