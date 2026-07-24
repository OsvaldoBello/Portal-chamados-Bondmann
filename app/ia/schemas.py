"""Schemas Pydantic da saída estruturada dos passes de triagem (F1).

Contrato do JSON que o modelo devolve — **uma chamada com saída estruturada
por passe**, validada aqui (Seção 2.1 do plano IA). JSON que não valida conta
como inválido (1 retry; depois ``acao='ERRO'`` silencioso).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class SaidaTriagem(BaseModel):
    """Saída do passe único (TI) / Passe A (Químico, F4).

    Campos extras do modelo são ignorados (tolerância a divagação); campos
    obrigatórios ausentes invalidam o JSON.
    """

    model_config = {"extra": "ignore"}

    # O chamado tem informação suficiente para o atendente agir?
    informacoes_suficientes: bool
    # Confiança geral da análise — perguntas ao usuário (F2) só saem com ALTA.
    confianca: Literal["ALTA", "MEDIA", "BAIXA"] = "MEDIA"
    # Pré-análise técnica para a nota interna (2–6 frases, pt-BR).
    pre_analise: str = Field(min_length=1)
    # Sugestões APENAS quando divergem do chamado (a IA nunca altera — Regra #3).
    categoria_sugerida: str | None = None
    prioridade_sugerida: Literal["BAIXA", "MEDIA", "ALTA", "URGENTE"] | None = None
    # Perguntas que fariam a triagem avançar (máx. 3). Em modo sombra ficam
    # registradas na nota interna; na F2 viram mensagem pública.
    perguntas: list[str] = Field(default_factory=list, max_length=3)
    # Termos para a busca de chamados semelhantes (F3 — FTS português).
    termos_busca: list[str] = Field(default_factory=list, max_length=8)


class SaidaPasseB(BaseModel):
    """Saída do Passe B do Químico (F4) — pré-análise técnica com a base sigilosa.

    A ``pre_analise`` vira EXCLUSIVAMENTE nota interna (invariante 8.2). Em
    ``ia_triagens.resultado`` só entram os METADADOS deste schema — nunca o
    texto da pré-análise (decisão da Seção 4.1: um único lugar sensível).
    """

    model_config = {"extra": "ignore"}

    # Pré-análise técnica (metodologia 6M, higiene epistêmica) — nota interna.
    pre_analise: str = Field(min_length=1)
    confianca: Literal["ALTA", "MEDIA", "BAIXA"] = "MEDIA"
    # O caso exige avaliação do químico responsável (playbook "quando escalar")?
    escalar_para_quimico: bool = False
    # Produto da base que o modelo reconheceu no chamado (auditoria).
    produto_reconhecido: str | None = None
    # Dados que o atendente deve confirmar antes de concluir (máx. 6).
    dados_faltantes: list[str] = Field(default_factory=list, max_length=6)
