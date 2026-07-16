"""Serviço de permissões de atendimento — workspace (Sprint 2 / item 2.2, M2).

Concentra num único lugar as regras de "quem pode atender/reivindicar/excluir
um chamado", hoje espalhadas entre `app/routes/workspace.py`
(`_carregar_atendimento`) e `kanban.html` (o Jinja recomputava `dept_bate` por
conta própria, com a mesma fórmula copiada à mão). Essa duplicação é
exatamente o tipo de coisa que causou o bug corrigido na migration `0028`
(bypass de TI que enxergava chamados fora do próprio setor como se pudesse
atendê-los) — a RLS sempre bloqueou a escrita, mas nada garantia que as duas
cópias da regra de UI ficassem em sincronia.

`autoatendimento` (`departamentos.autoatendimento`, migrations 0038/0042) é a
única exceção à segregação de autoria (0029): nos setores Marketing/RH o
próprio autor pode ser responsável pela demanda; nos demais, nunca.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PermissoesAtendimento:
    dept_bate: bool
    eh_autor: bool
    eh_autoatendimento: bool
    bloqueado_por_autoria: bool
    pode_reivindicar: bool
    pode_atender: bool


class AtendimentoService:
    """Regras de acesso do staff a um chamado (Fase 4 — workspace)."""

    @staticmethod
    def dept_bate(chamado: dict, perfil: dict) -> bool:
        """Setor de destino do chamado bate com o setor do staff logado.

        Único critério usado tanto na tela de atendimento quanto no gate de
        UI do Kanban (arrastar/excluir cartão) — antes duplicado à mão nos
        dois lugares.
        """
        return bool(perfil.get("departamento_id")) and str(
            chamado.get("departamento_id") or ""
        ) == str(perfil.get("departamento_id") or "")

    @classmethod
    def permissoes(cls, chamado: dict, perfil: dict, user_id: str) -> PermissoesAtendimento:
        """Permissões completas da tela de atendimento de um chamado."""
        eh_autor = str(chamado.get("cliente_id") or "") == str(user_id)
        eh_autoatendimento = bool(chamado.get("autoatendimento"))
        dept_bate = cls.dept_bate(chamado, perfil)
        ja_assumido = chamado.get("operador_id") is not None
        bloqueado_por_autoria = eh_autor and not eh_autoatendimento
        pode_reivindicar = dept_bate and not bloqueado_por_autoria and not ja_assumido
        pode_atender = dept_bate and not bloqueado_por_autoria and ja_assumido
        return PermissoesAtendimento(
            dept_bate=dept_bate,
            eh_autor=eh_autor,
            eh_autoatendimento=eh_autoatendimento,
            bloqueado_por_autoria=bloqueado_por_autoria,
            pode_reivindicar=pode_reivindicar,
            pode_atender=pode_atender,
        )
