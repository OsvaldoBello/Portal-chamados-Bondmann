"""Corpus versionado do red team do Químico (F5, Seção 8.3 do plano IA).

Um arquivo JSON por cenário em ``tests/red_team/casos/`` — descrição maliciosa
do chamado (+ resposta maliciosa de rodada 2, quando o cenário é multi-rodada).
Categorias mínimas exigidas pelo plano: pedido direto de formulação; "ignore
as instruções anteriores"; roleplay/autoridade falsa; extração incremental
multi-rodada; indução a citar quantidades "aproximadas"; tentativa de vazar a
nota interna via pergunta pública.

Este módulo só carrega dados — não é um arquivo de teste (sem prefixo
``test_``, não é coletado pelo pytest).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

CASOS_DIR = Path(__file__).parent / "casos"


@dataclass(frozen=True)
class CasoRedTeam:
    id: str
    categoria: str
    titulo: str
    descricao: str
    dados_formulario: dict | None = None
    resposta_rodada2: str | None = None


def carregar_casos() -> list[CasoRedTeam]:
    """Todos os cenários do corpus, ordenados por nome de arquivo (determinístico)."""
    casos = []
    for caminho in sorted(CASOS_DIR.glob("*.json")):
        dados = json.loads(caminho.read_text(encoding="utf-8"))
        casos.append(
            CasoRedTeam(
                id=dados["id"],
                categoria=dados["categoria"],
                titulo=dados["titulo"],
                descricao=dados["descricao"],
                dados_formulario=dados.get("dados_formulario"),
                resposta_rodada2=dados.get("resposta_rodada2"),
            )
        )
    return casos


def caso_para_chamado(caso: CasoRedTeam, *, base: dict) -> dict:
    """Funde um cenário do corpus num dict de chamado (mesmo formato de
    ``_CHAMADO_QUIMICO`` em ``tests/test_ia_quimico.py``)."""
    chamado = dict(base)
    chamado["titulo"] = caso.titulo
    chamado["descricao"] = caso.descricao
    if caso.dados_formulario is not None:
        chamado["dados_formulario"] = json.dumps(caso.dados_formulario, ensure_ascii=False)
    return chamado
