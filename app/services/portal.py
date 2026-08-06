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

        Chamado aberto para o PRÓPRIO departamento do autor (``departamento_id
        == cliente_departamento_id``, ex.: alguém do Marketing pedindo pro
        Marketing) fica de fora: ali o setor se autoatende num quadro estilo
        Trello, sem uma relação real de "quem prestou o serviço" — CSAT só faz
        sentido pra quem pediu algo a OUTRO departamento (2026-07-23, recorrente
        no Marketing: a trava de avaliação travava a abertura de um novo
        chamado mesmo quando quem resolveu foi um colega do próprio setor).
        Requer ``chamado["cliente_departamento_id"]`` no dict (join feito em
        ``AtendimentoRepo.obter``).

        Duplicado de combinação (0065) também não avalia: ninguém atendeu
        *aquele* chamado — ele foi encerrado por ser repetido, e o atendimento
        que merece nota é o do principal, onde o autor está em cópia."""
        return (
            chamado.get("status") == "RESOLVIDO"
            and str(chamado.get("cliente_id")) == str(user_id)
            and not chamado.get("chamado_principal_id")
            and str(chamado.get("departamento_id")) != str(chamado.get("cliente_departamento_id"))
        )

    @staticmethod
    def pode_reabrir(chamado: dict, user_id: str) -> bool:
        """Regra de UI: o autor de um chamado RESOLVIDO pode reabri-lo quando
        não está satisfeito com a solução (RLS + trigger reforçam no banco,
        migration 0059). Ao contrário de `pode_avaliar`, vale também para
        chamados endereçados ao PRÓPRIO departamento do autor (autoatendimento):
        reabrir não é uma nota de CSAT sobre "quem prestou o serviço", é dizer
        "isso não foi resolvido de verdade" — faz sentido mesmo quando quem
        resolveu foi o próprio setor.

        Duplicado de combinação (0065) é a exceção: reabri-lo devolveria ao
        quadro um chamado que continua fora dos indicadores e cujo assunto já
        está sendo tratado no principal. Quem não se sente atendido reclama no
        principal (está em cópia nele) ou o staff desfaz a combinação."""
        return (
            chamado.get("status") == "RESOLVIDO"
            and str(chamado.get("cliente_id")) == str(user_id)
            and not chamado.get("chamado_principal_id")
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

    @staticmethod
    def representante_pode_marketing(perfil_departamento: str | None) -> bool:
        """Só o setor "Representantes" fica de fora do Marketing como destino
        (2026-08-06) — Supervisão de Vendas, Gerentes de vendas e todo o resto
        da empresa continuam livres para abrir chamado lá. Fonte do setor é o
        perfil cadastrado pelo admin (``perfis.departamento_id`` via
        ``ctx.perfil["departamento"]``), não o campo "Setor" digitado no
        formulário: esse é texto livre sem amarração à conta logada, então
        bloquear por ele seria só um aviso de UI, não uma restrição real."""
        return (perfil_departamento or "").strip().lower() != "representantes"

    @staticmethod
    def quimico_dep_id(departamentos: list[dict]) -> str:
        """Id do departamento Químico ("Dpto Químico", migration 0027/0049) na
        lista, ou "" se ausente. O front usa para exibir o bloco de campos
        dinâmicos por categoria; o POST, para decidir se valida/monta o
        ``dados_formulario``."""
        return next(
            (
                str(d["id"])
                for d in departamentos
                if (d.get("nome") or "").strip().lower() == "dpto químico"
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
