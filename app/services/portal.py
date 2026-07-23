"""Serviço de regras de negócio do Portal — abertura de chamado e avaliação
(Sprint 2 / item 2.2, M2, fase portal).

Concentra: (a) `pode_avaliar` (autor + RESOLVIDO), já isolada como função pura
antes desta extração, só movida; (b) a identificação do departamento
"Marketing" por nome e a regra do fluxo por demanda (prioridade forçada +
prazo mínimo de 48h / "sem prazo"), que antes vivia duplicada entre
`_render_form` e `criar_chamado` em `app/routes/portal.py` — duas cópias da
mesma fórmula "achar o Marketing pelo nome", cada uma sobre uma lista de
departamentos com filtro ligeiramente diferente, sob risco real de
dessincronia (mesma classe de duplicação que motivou o `AtendimentoService`
na fase workspace deste item).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

_TZ_BR = ZoneInfo("America/Sao_Paulo")
_ENTREGA_MIN_DIAS = 2


@dataclass(frozen=True)
class RegrasMarketing:
    is_marketing: bool
    prioridade: str
    data_entrega: date | None
    sem_prazo: bool
    erro: str | None = None


class PortalService:
    """Regras de negócio da abertura/avaliação de chamado (Fase 3 — portal)."""

    @staticmethod
    def pode_avaliar(chamado: dict, user_id: str) -> bool:
        """Regra de UI: autor + RESOLVIDO podem avaliar (RLS reforça no banco).

        Autoatendimento (o próprio autor também atendeu, ``operador_id ==
        cliente_id``) fica de fora: não faz sentido pedir CSAT de quem resolveu
        a própria demanda — CSAT mede a experiência de quem foi atendido por
        outra pessoa/setor."""
        return (
            chamado.get("status") == "RESOLVIDO"
            and str(chamado.get("cliente_id")) == str(user_id)
            and str(chamado.get("operador_id")) != str(chamado.get("cliente_id"))
        )

    @staticmethod
    def data_entrega_min() -> date:
        """Menor data de entrega permitida (hoje + 48h, no fuso de Brasília)."""
        return datetime.now(_TZ_BR).date() + timedelta(days=_ENTREGA_MIN_DIAS)

    @staticmethod
    def marketing_dep_id(departamentos: list[dict]) -> str:
        """Id do departamento "Marketing" na lista informada, ou "" se ausente.

        Fórmula única de "achar o Marketing pelo nome" — usar sempre esta,
        nunca reimplementar a busca inline (ver docstring do módulo)."""
        return next(
            (
                str(d["id"])
                for d in departamentos
                if (d.get("nome") or "").strip().lower() == "marketing"
            ),
            "",
        )

    @classmethod
    def regras_marketing(
        cls,
        *,
        departamento_id: str,
        setores_ativos: list[dict],
        prioridade: str,
        sem_prazo_marcado: bool,
        data_entrega: str,
    ) -> RegrasMarketing:
        """Resolve o fluxo por demanda do Marketing: prioridade forçada e prazo
        mínimo de 48h (ou "sem prazo determinado"). Fora do Marketing, devolve
        a prioridade recebida sem alteração."""
        marketing_id = cls.marketing_dep_id(setores_ativos)
        is_marketing = bool(marketing_id) and departamento_id == marketing_id
        if not is_marketing:
            return RegrasMarketing(
                is_marketing=False, prioridade=prioridade, data_entrega=None, sem_prazo=False
            )

        # Prioridade não é usada no fluxo por demanda do Marketing.
        if sem_prazo_marcado:
            # Demanda sem urgência nem prazo determinado — feita quando sobrar
            # tempo (0040). BAIXA: não entra na escalada automática de
            # prioridade do Marketing (0031), que só olha data_entrega.
            return RegrasMarketing(
                is_marketing=True, prioridade="BAIXA", data_entrega=None, sem_prazo=True
            )

        if not data_entrega:
            return RegrasMarketing(
                is_marketing=True, prioridade="MEDIA", data_entrega=None, sem_prazo=False,
                erro=(
                    "Informe a data de entrega desejada (mínimo de 48h) ou marque "
                    "\"Sem data limite\"."
                ),
            )
        try:
            escolhida = date.fromisoformat(data_entrega)
        except ValueError:
            return RegrasMarketing(
                is_marketing=True, prioridade="MEDIA", data_entrega=None, sem_prazo=False,
                erro="Data de entrega inválida.",
            )
        minimo = cls.data_entrega_min()
        if escolhida < minimo:
            return RegrasMarketing(
                is_marketing=True, prioridade="MEDIA", data_entrega=None, sem_prazo=False,
                erro=(
                    "A data de entrega deve ser a partir de "
                    f"{minimo.strftime('%d/%m/%Y')} — mínimo de 48h para início do desenvolvimento."
                ),
            )
        return RegrasMarketing(
            is_marketing=True, prioridade="MEDIA", data_entrega=escolhida, sem_prazo=False
        )
