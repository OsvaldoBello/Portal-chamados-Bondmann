"""Regras de negócio e domínio para demandas do setor de Marketing.

Governa a detecção de atraso de demandas e a obrigatoriedade do registro de
causas de atraso antes da conclusão do atendimento (Fase 4 / Sprint 2).
"""

from __future__ import annotations

from datetime import UTC, datetime

# As 4 causas de atraso oficiais do Marketing (pedido do usuário 2026-10-07).
CAUSAS_ATRASO_MARKETING: tuple[str, ...] = (
    "SEM CAUSA REGISTRADA",
    "AGUARDANDO DEFINIÇÃO INTERNA",
    "DEPENDÊNCIA DE EXECUÇÃO INTERNA",
    "DEPENDÊNCIA DE TERCEIROS",
)


STATUS_ATIVOS_MARKETING = ("NOVO", "A_FAZER", "EM_ATENDIMENTO", "AGUARDANDO_TERCEIROS")


def demanda_marketing_atrasada(chamado: dict, agora: datetime | None = None) -> bool:
    """Verifica se uma demanda do Marketing ultrapassou o prazo de resolução acordado.

    Uma demanda de Marketing só é considerada atrasada quando:
    1. Pertence ao setor de Marketing (`departamento == 'Marketing'`).
    2. Está em um status ativo com contagem de prazo (`NOVO`, `A_FAZER`, `EM_ATENDIMENTO`, `AGUARDANDO_TERCEIROS`).
       Chamados com prazo pausado (`AGUARDANDO`) ou já concluídos/aguardando avaliação (`RESOLVIDO`)
       não contam como atrasados.
    3. Não é uma demanda `sem_prazo` (ou seja, possui `limite_resolucao` definido).
    4. O instante de checagem (`agora`) ultrapassou `limite_resolucao` (que reflete
       a `data_entrega` às 18:00 no horário de Brasília).
    """
    departamento = str(chamado.get("departamento") or "").strip()
    if departamento != "Marketing":
        return False

    status = chamado.get("status")
    if status and status not in STATUS_ATIVOS_MARKETING:
        return False

    if bool(chamado.get("sem_prazo")):
        return False

    limite = chamado.get("limite_resolucao")
    if limite is None:
        return False

    termino = chamado.get("resolvido_em")
    if termino is None:
        if agora is None:
            agora = datetime.now(limite.tzinfo or UTC)
        termino = agora

    if limite.tzinfo is not None and termino.tzinfo is None:
        termino = termino.replace(tzinfo=UTC)
    elif limite.tzinfo is None and termino.tzinfo is not None:
        limite = limite.replace(tzinfo=UTC)

    return termino > limite


def validar_conclusao_marketing(
    chamado: dict, causa_atraso: str | None = None, agora: datetime | None = None
) -> str | None:
    """Valida se uma demanda de Marketing atrasada possui causa de atraso válida.

    Retorna uma mensagem de erro descritiva se a conclusão estiver bloqueada,
    ou `None` se a validação passar (demanda no prazo, sem prazo, ou com causa válida).
    """
    if not demanda_marketing_atrasada(chamado, agora=agora):
        return None

    # Se uma causa foi enviada nesta requisição (ex: no POST /encerrar), avalia ela;
    # caso contrário, avalia a causa já persistida no chamado.
    causa_efetiva = (
        causa_atraso if causa_atraso is not None else chamado.get("causa_atraso")
    )
    causa_limpa = (causa_efetiva or "").strip()

    if not causa_limpa:
        return "Esta demanda está atrasada. Para finalizá-la, é obrigatório informar a causa do atraso."

    if causa_limpa not in CAUSAS_ATRASO_MARKETING:
        return (
            f"Causa de atraso inválida. Selecione uma das opções permitidas: "
            f"{', '.join(CAUSAS_ATRASO_MARKETING)}."
        )

    return None


def dias_atraso_marketing(chamado: dict, agora: datetime | None = None) -> int:
    """Calcula a quantidade de dias de atraso de uma demanda de Marketing.

    Retorna 0 se a demanda não estiver atrasada ou for `sem_prazo`.
    Se atrasada, retorna a diferença em dias entre a data de resolução (ou `agora`)
    e o `limite_resolucao`, garantindo pelo menos 1 dia se houver atraso.
    """
    departamento = str(chamado.get("departamento") or "Marketing").strip()
    if departamento != "Marketing":
        return 0

    status = chamado.get("status")
    if status and status not in STATUS_ATIVOS_MARKETING:
        return 0

    if bool(chamado.get("sem_prazo")):
        return 0

    limite = chamado.get("limite_resolucao")
    if limite is None:
        return 0

    termino = chamado.get("resolvido_em")
    if termino is None:
        if agora is None:
            agora = datetime.now(limite.tzinfo or UTC)
        termino = agora

    if limite.tzinfo is not None and termino.tzinfo is None:
        termino = termino.replace(tzinfo=UTC)
    elif limite.tzinfo is None and termino.tzinfo is not None:
        limite = limite.replace(tzinfo=UTC)

    diff_segundos = (termino - limite).total_seconds()
    if diff_segundos <= 0:
        return 0

    dias = diff_segundos / 86400.0
    return max(1, int(round(dias)))
