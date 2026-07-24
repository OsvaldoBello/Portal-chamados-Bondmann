"""Contexto do agente Químico (F4): recuperação seletiva na base ``base_quimico_*``.

Seções 3.1/3.3/C7 do plano IA — proteção ESTRUTURAL, não promessa de prompt:

- Toda leitura da base usa uma conexão DEDICADA com o role Postgres
  ``ia_worker`` (``IA_WORKER_DATABASE_URL``), que NÃO tem GRANT/policy em
  ``base_quimico_formulacoes`` — as quantidades são inacessíveis por permissão
  de banco, não por disciplina de código.
- **Passe A** recebe apenas o playbook de perguntas (tipo
  ``PERGUNTA_INVESTIGACAO`` — roteiros genéricos por cenário, sem qualquer dado
  de produto). :func:`playbook_perguntas` é a ÚNICA função deste módulo que o
  caminho do Passe A pode consumir.
- **Passe B** recebe a recuperação seletiva: só as linhas + ficha do(s)
  produto(s) citado(s) (~3–8 mil tokens), nunca a base inteira — o que também é
  a economia de tokens da F4 (vs. anexar planilha/PDF completos por chamada).

Falha aqui é sempre silenciosa (Regra de Ouro #5): sem env, sem tabela ou com
erro de rede, o chamador recebe vazio/None e a triagem degrada graciosamente
(o Químico é triado só com os dados do chamado).
"""

from __future__ import annotations

import json
import logging
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any

import asyncpg

from app.config import Settings

log = logging.getLogger("app.ia_contexto_quimico")

# Recuperação seletiva: teto de produtos citados e de tamanho de ficha no
# contexto (controle de custo/token — Seção 6 do plano IA).
_MAX_PRODUTOS = 3
_MAX_FICHA_CHARS = 6_000
_TIMEOUT_CONEXAO_S = 10


def _normalizar(texto: str) -> str:
    """Caixa alta sem acento — casamento robusto de nomes de produto."""
    nfkd = unicodedata.normalize("NFKD", texto)
    return "".join(c for c in nfkd if not unicodedata.combining(c)).upper()


def _alnum(texto: str) -> str:
    """Só letras/dígitos, caixa alta, sem acento — tolera divergência de grafia
    entre o select do formulário e a planilha ("WAY 45 - B" == "WAY45 - B")."""
    return re.sub(r"[^A-Z0-9]", "", _normalizar(texto))


def _padrao_flexivel(nome: str) -> re.Pattern[str]:
    """Regex do nome tolerante a espaços/pontos/hífens internos, com limite de
    palavra nas pontas ("26" não casa com "26000"; "OX" não casa com "TOXICO")."""
    partes = [re.escape(c) for c in _alnum(nome)]
    return re.compile(r"(?<![A-Z0-9])" + r"[\s.\-–—]*".join(partes) + r"(?![A-Z0-9])")


@dataclass(frozen=True)
class ContextoQuimico:
    """Contexto sigiloso montado para o Passe B (SEM quantidades — C7)."""

    produtos: list[dict[str, Any]] = field(default_factory=list)
    fichas: list[str] = field(default_factory=list)
    diagnosticos: list[dict[str, Any]] = field(default_factory=list)
    regras_sigilo: list[dict[str, Any]] = field(default_factory=list)

    @property
    def vazio(self) -> bool:
        return not (self.produtos or self.fichas or self.diagnosticos or self.regras_sigilo)


def identificar_produtos(
    nomes_conhecidos: list[str], produto_form: str | None, texto_livre: str
) -> list[str]:
    """Nomes de produto citados no chamado (função pura, testável).

    Prioridade: o campo "Produto" do formulário dinâmico (select de opções
    fixas — casamento por comparação ALFANUMÉRICA, tolerante à divergência de
    grafia formulário × planilha, ex.: "WAY 45 - B" × "WAY45 - B"); fallback:
    varredura do texto livre com regex flexível a espaços/pontos internos e
    limite de palavra, nomes mais longos primeiro (evita "26" capturar
    "ADITIVO 1090" ou casar com "26000")."""
    por_alnum: dict[str, str] = {}
    for n in nomes_conhecidos:
        por_alnum.setdefault(_alnum(n), n)
    if produto_form:
        alvo = por_alnum.get(_alnum(produto_form))
        if alvo:
            return [alvo]
    achados: list[str] = []
    norma_texto = _normalizar(texto_livre or "")
    for nome in sorted(nomes_conhecidos, key=len, reverse=True):
        if _padrao_flexivel(nome).search(norma_texto):
            achados.append(nome)
        if len(achados) >= _MAX_PRODUTOS:
            break
    return achados


def _dados_jsonb(valor: Any) -> dict[str, Any]:
    """jsonb chega como str no asyncpg — normaliza para dict."""
    if isinstance(valor, str):
        try:
            return json.loads(valor)
        except ValueError:
            return {}
    return dict(valor or {})


async def _conectar(settings: Settings) -> asyncpg.Connection | None:
    """Conexão do role ``ia_worker`` — ou ``None`` se a env não está configurada."""
    if not settings.ia_worker_database_url:
        return None
    return await asyncpg.connect(
        settings.ia_worker_database_url,
        statement_cache_size=0,  # exigência do Supavisor em transaction mode
        timeout=_TIMEOUT_CONEXAO_S,
    )


async def playbook_perguntas(settings: Settings) -> list[dict[str, Any]]:
    """Roteiro de perguntas de investigação para o **Passe A**.

    Só linhas ``tipo='PERGUNTA_INVESTIGACAO'`` — cenário + pergunta + próximos
    passos, SEM qualquer dado de produto/ficha/formulação (invariante 8.2:
    o payload do Passe A não contém nada de ``base_quimico_produtos``/fichas).
    """
    conn = None
    try:
        conn = await _conectar(settings)
        if conn is None:
            return []
        rows = await conn.fetch(
            "SELECT sintoma, dados FROM base_quimico_playbooks "
            "WHERE tipo = 'PERGUNTA_INVESTIGACAO' ORDER BY id"
        )
        return [{"cenario": r["sintoma"], **_dados_jsonb(r["dados"])} for r in rows]
    except Exception as exc:  # noqa: BLE001 — contexto nunca derruba a triagem
        log.warning("[IA QUIMICO] Playbook de perguntas indisponível: %s", exc)
        return []
    finally:
        if conn is not None:
            await conn.close()


async def montar_contexto_passe_b(
    settings: Settings, produto_form: str | None, texto_livre: str
) -> ContextoQuimico | None:
    """Contexto sigiloso do **Passe B**: recuperação seletiva por produto.

    ``None`` = base indisponível (sem env / erro) — o chamador cai no fallback
    (nota do Passe A). Contexto vazio ≠ None: base acessível, produto não
    identificado (o Passe B ainda recebe diagnósticos e regras de conduta).
    """
    conn = None
    try:
        conn = await _conectar(settings)
        if conn is None:
            return None
        nomes = [
            r["nome"] for r in await conn.fetch("SELECT DISTINCT nome FROM base_quimico_produtos")
        ]
        alvos = identificar_produtos(nomes, produto_form, texto_livre)
        produtos: list[dict[str, Any]] = []
        fichas: list[str] = []
        if alvos:
            rows = await conn.fetch(
                """
                SELECT chave_produto, nome, segmento, aplicacao, familia_tecnica,
                       tipo_uso, componentes, orientacao
                  FROM base_quimico_produtos WHERE nome = ANY($1::text[])
                 ORDER BY chave_produto LIMIT $2
                """,
                alvos,
                _MAX_PRODUTOS,
            )
            produtos = [dict(r) for r in rows]
            for p in produtos:
                if isinstance(p.get("componentes"), str):
                    p["componentes"] = json.loads(p["componentes"])
            fichas_rows = await conn.fetch(
                "SELECT DISTINCT conteudo FROM base_quimico_fichas WHERE chave_produto = ANY($1::text[])",
                [p["chave_produto"] for p in produtos],
            )
            fichas = [r["conteudo"][:_MAX_FICHA_CHARS] for r in fichas_rows]
        extras = await conn.fetch(
            "SELECT tipo, sintoma, dados FROM base_quimico_playbooks "
            "WHERE tipo IN ('DIAGNOSTICO', 'REGRA_SIGILO') ORDER BY id"
        )
        diagnosticos = [
            {"sintoma": r["sintoma"], **_dados_jsonb(r["dados"])}
            for r in extras
            if r["tipo"] == "DIAGNOSTICO"
        ]
        regras = [
            {"tipo_informacao": r["sintoma"], **_dados_jsonb(r["dados"])}
            for r in extras
            if r["tipo"] == "REGRA_SIGILO"
        ]
        return ContextoQuimico(
            produtos=produtos, fichas=fichas, diagnosticos=diagnosticos, regras_sigilo=regras
        )
    except Exception as exc:  # noqa: BLE001 — contexto nunca derruba a triagem
        log.warning("[IA QUIMICO] Contexto do Passe B indisponível: %s", exc)
        return None
    finally:
        if conn is not None:
            await conn.close()


def formatar_contexto(ctx: ContextoQuimico) -> str:
    """Contexto → texto da mensagem ``user`` do Passe B (função pura).

    Só campos liberados ao worker: catálogo sem proporções, ficha técnica,
    diagnósticos por sintoma e regras de conduta/sigilo. Quantidades jamais
    chegam aqui (o role ``ia_worker`` não consegue lê-las — C7)."""
    linhas: list[str] = []
    if ctx.produtos:
        linhas.append("## Base interna — produto(s) citado(s)")
        for p in ctx.produtos:
            linhas += [
                f"### {p.get('nome')} ({p.get('chave_produto')})",
                f"Segmento: {p.get('segmento') or '—'} · Família técnica: {p.get('familia_tecnica') or '—'}",
                f"Aplicação: {p.get('aplicacao') or '—'}",
                f"Tipo de uso: {p.get('tipo_uso') or '—'}",
                f"Componentes (nomes, SEM proporções): {', '.join(p.get('componentes') or []) or '—'}",
                f"Orientação de resposta: {p.get('orientacao') or '—'}",
                "",
            ]
    if ctx.fichas:
        linhas.append("## Ficha(s) técnica(s) do(s) produto(s)")
        for ficha in ctx.fichas:
            linhas += [ficha, ""]
    if ctx.diagnosticos:
        linhas.append("## Diagnóstico de ocorrências (playbook interno)")
        for d in ctx.diagnosticos:
            linhas.append(f"- Sintoma: {d.get('sintoma')}")
            for rotulo, chave in (
                ("Possíveis causas", "Possíveis causas técnicas"),
                ("Dados mínimos a coletar", "Dados mínimos a coletar"),
                ("Parâmetros a medir", "Parâmetros a medir"),
                ("Hipóteses por componente/família", "Hipóteses por componente/família"),
                ("Orientação inicial", "Orientação inicial da IA"),
                ("Quando escalar", "Quando escalar para químico"),
            ):
                if d.get(chave):
                    linhas.append(f"  - {rotulo}: {d[chave]}")
    if ctx.regras_sigilo:
        linhas.append("")
        linhas.append("## Regras de conduta e sigilo (obrigatórias)")
        for r in ctx.regras_sigilo:
            linhas.append(
                f"- {r.get('tipo_informacao')}: pode responder? {r.get('Pode responder?') or '—'}."
                f" Como: {r.get('Como a IA deve responder') or '—'}"
                f" Não revelar: {r.get('O que não deve revelar') or '—'}"
            )
    return "\n".join(linhas).strip()
