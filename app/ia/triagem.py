"""Motor de triagem de chamados por IA (F1 sombra + F2 perguntas/re-triagem +
F4 dois passes do Químico).

Fluxo (Seção 2.3 do plano IA): chamado criado num departamento com triagem
ativa → background task → **uma chamada com saída estruturada** (JSON validado
por :class:`app.ia.schemas.SaidaTriagem`; 1 retry em JSON inválido) → decisão:

- ``NOTA_INTERNA`` — pré-análise assinada pelo perfil "Assistente IA". Sai
  DIRETO quando a informação é suficiente (ou em modo sombra, ou na última
  rodada com as lacunas sinalizadas, ou com atendimento já iniciado).
- ``PERGUNTAS`` (F2, fora do modo sombra) — mensagem PÚBLICA ao autor +
  e-mail (Reply-To inbound). A nota interna NÃO sai nesta rodada (decisão do
  usuário 2026-07-23: o ciclo de perguntas vem primeiro; a nota fecha o ciclo
  quando a resposta chegar — rodada N+1 — ou no teto de rodadas, com lacunas).

A máquina de rodadas é **inteiramente derivada do banco** (``ia_triagens``),
o que torna o motor idempotente por construção:

- rodada N+1 só executa se a última triagem foi ``PERGUNTAS`` **e** existe
  mensagem pública do AUTOR posterior a ela (task duplicada não re-tria);
- rodada N+1 só volta a perguntar o que o autor deixou EM BRANCO na rodada
  anterior (:func:`pendencias_do_ciclo`) — se ele respondeu tudo (inclusive
  com "não sei"), o ciclo fecha com a nota interna;
- rodada > ``IA_TRIAGEM_MAX_RODADAS`` é impossível (teto + UNIQUE do banco);
- guarda de atendimento (ajuste 2026-07-23): atendimento iniciado NÃO suprime
  a nota interna da rodada 1 (ela existe para quem atende) — força a ação a
  ``NOTA_INTERNA`` e veta re-triagem; a guarda é reavaliada com estado fresco
  após a chamada de modelo. Só chamado ``RESOLVIDO`` fica fora da triagem.

Garantias permanentes (Regras de Ouro): kill switch sem efeito colateral;
falha silenciosa ponta a ponta; a IA nunca conclui atendimento nem altera
prioridade/status (essas seguem só como sugestão de texto na nota);
``is_interna=true`` fixado em código na persistência da nota (invariante 8.2 —
o canal público tem função própria, usada só em PERGUNTAS).

**Reclassificação automática (F8, 2026-08-04):** a única alteração de estado do
chamado que o motor faz é trocar ``categoria_id``/``subcategoria_id`` quando a
classificação escolhida na abertura está evidentemente errada — pedido do
gestor, ver ADR 0008 e Seção 2.5 do plano IA. É estrutural, não promessa de
prompt: :func:`resolver_reclassificacao` (pura) só devolve destino que exista no
**catálogo do departamento do próprio chamado**, e :func:`_aplicar_reclassificacao`
ainda exige chamado intocado (ninguém atendendo, nenhuma reclassificação
anterior, sem formulário dinâmico vinculado à categoria) e faz a escrita como
compare-and-swap contra a categoria analisada. Nada disso roda sem o
departamento estar em ``IA_TRIAGEM_RECLASSIFICACAO_DEPARTAMENTOS`` (default
vazio = feature desligada).

**Dpto Químico (F4, Seção 3):** o Passe A acima é idêntico ao do TI, só que
com o roteiro de perguntas de investigação no contexto (SEM dado de produto —
:func:`app.ia.contexto_quimico.playbook_perguntas`). Quando decide
``NOTA_INTERNA``, um **Passe B** roda em seguida (só para o Químico): contexto
sigiloso recuperado seletivamente (:func:`app.ia.contexto_quimico.montar_contexto_passe_b`,
via conexão dedicada do role ``ia_worker`` — C7) + prompt de pré-análise técnica
6M (``prompts/quimico_passe_b.md``); a saída **substitui** a nota do Passe A
(degrada para a do A se o B falhar) e é gravada **exclusivamente** como nota
interna. O ciclo de perguntas (``PERGUNTAS``) nunca chega a rodar o Passe B —
a base sigilosa não participa do canal que fala com o autor.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import unicodedata
from dataclasses import dataclass
from datetime import timedelta
from difflib import SequenceMatcher
from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from app.config import Settings, get_settings
from app.db import admin_connection
from app.domain.formularios_quimico import rotular
from app.ia import anexos_contexto, cliente, contexto_quimico
from app.ia.catalogo_prompt import linha_catalogo, nome_categoria
from app.ia.chamada_estruturada import chamar_modelo_estruturado
from app.ia.schemas import SaidaPasseB, SaidaTriagem
from app.notification import notificar_nova_mensagem_email
from app.repositories import ia_busca
from app.storage import ensure_storage

log = logging.getLogger("app.ia_triagem")

PERFIL_IA_NOME = "Assistente IA"
_PASSE_UNICO = "UNICO"
_PASSE_A = "A"
_PASSE_B = "B"
_MAX_TOKENS_SAIDA = 900

# Mesmo casamento normalizado usado por `PortalService.quimico_dep_id`
# (aqui é o NOME do departamento, não o id — a triagem só recebe nomes).
_DEPARTAMENTO_QUIMICO_NORM = "dpto químico"


def _eh_quimico(departamento_nome: str | None) -> bool:
    return (departamento_nome or "").strip().lower() == _DEPARTAMENTO_QUIMICO_NORM


# Ranking do limiar de confiança para perguntas públicas
# (`IA_TRIAGEM_PERGUNTAS_CONFIANCA_MINIMA`, Seção 2.4 do plano IA).
_CONFIANCA_ORDEM = {"BAIXA": 0, "MEDIA": 1, "ALTA": 2}

# Cache em memória do UUID do perfil de serviço (Seção 4.2 — lookup por nome,
# sem env extra, sem hardcode). O perfil nunca muda em runtime.
_perfil_ia_id: str | None = None


def deve_triar(departamento_nome: str | None, settings: Settings) -> bool:
    """Os hooks (abertura e resposta do autor) só agendam a task quando a
    triagem está ligada para o departamento (o motor revalida tudo ao rodar)."""
    return (
        settings.ia_triagem_ativa
        and bool(settings.ia_triagem_api_key)
        and (departamento_nome or "") in settings.ia_triagem_departamentos_lista
    )


@lru_cache(maxsize=8)
def _prompt(nome: str) -> str:
    """Prompt de sistema versionado em ``app/ia/prompts/<nome>.md`` (Regra #8).

    O cabeçalho de documentação do arquivo (antes do primeiro ``---``) não é
    enviado ao modelo."""
    texto = (Path(__file__).parent / "prompts" / f"{nome}.md").read_text(encoding="utf-8")
    return texto.split("\n---\n", 1)[1].strip()


def _linhas_chamado(
    chamado: dict[str, Any], anexos: anexos_contexto.ResultadoAnexos | None = None
) -> list[str]:
    """Fatos do chamado — comuns ao passe único, ao Passe A e ao Passe B."""
    linhas = [
        "## Chamado",
        f"Departamento: {chamado.get('departamento') or '—'}",
        f"Categoria escolhida: {chamado.get('categoria') or '—'}",
        f"Subcategoria escolhida: {chamado.get('subcategoria') or '—'}",
        f"Prioridade declarada: {chamado.get('prioridade') or '—'}",
        f"Assunto: {chamado.get('titulo') or '—'}",
        "",
        "Descrição:",
        str(chamado.get("descricao") or ""),
    ]
    pares = rotular(chamado.get("categoria"), chamado.get("dados_formulario") or {})
    if pares:
        linhas += ["", "Campos do formulário:"]
        linhas += [f"- {rotulo}: {valor}" for rotulo, valor in pares]
    if anexos and anexos.texto_documentos:
        linhas += [
            "",
            "## Texto extraído de documentos anexados (PDF/Word/Excel/PowerPoint — pode estar incompleto)",
            anexos.texto_documentos,
        ]
    return linhas


def _conteudo_usuario(linhas: list[str], anexos: anexos_contexto.ResultadoAnexos | None) -> Any:
    """Content do turno do usuário: string simples (formato de sempre) quando
    não há imagem anexada, ou lista de blocos texto+``image_url`` (formato
    multimodal OpenAI) quando há — payload idêntico ao anterior sem imagem,
    base do teste de regressão do kill switch de anexos."""
    texto = "\n".join(linhas)
    if not anexos or not anexos.blocos_imagem:
        return texto
    return [{"type": "text", "text": texto}, *anexos.blocos_imagem]


_nome_categoria = nome_categoria
_linha_catalogo = linha_catalogo


def _linha_catalogo_do_banco(linha: dict[str, Any]) -> dict[str, Any]:
    """Normaliza a linha do catálogo vinda do banco (``json_agg`` chega como
    ``str`` no asyncpg; catálogo sem subcategoria chega vazio)."""
    subs = linha.get("subcategorias") or []
    return {
        "id": linha.get("id"),
        "nome": linha.get("nome"),
        "subcategorias": json.loads(subs) if isinstance(subs, str) else subs,
    }


def montar_mensagens(
    chamado: dict[str, Any],
    categorias: list[Any],
    conversa: list[dict[str, str]] | None = None,
    playbook: list[dict[str, Any]] | None = None,
    anexos: anexos_contexto.ResultadoAnexos | None = None,
) -> list[dict[str, Any]]:
    """Mensagens system+user do passe único (TI) / Passe A (Químico, F4).

    ``conversa`` (rodadas > 1): lista de ``{"papel": ..., "conteudo": ...}`` com
    a troca pública até aqui — perguntas da IA e respostas do autor.
    ``playbook`` (só Químico): roteiro de perguntas de investigação por
    cenário (:func:`app.ia.contexto_quimico.playbook_perguntas`) — SEM
    qualquer dado de produto/base sigilosa (Seção 3.1 do plano IA).
    ``anexos`` (F7): imagens/PDFs do próprio chamado — não é dado sigiloso da
    Bondmann (Regra de Ouro #4), então participa também do canal público."""
    linhas = _linhas_chamado(chamado, anexos)
    if playbook:
        linhas += [
            "",
            "## Roteiro de perguntas de investigação (base interna — sem dado de produto)",
        ]
        for item in playbook:
            cenario = item.get("cenario") or "—"
            detalhes = "; ".join(
                f"{chave}: {valor}"
                for chave, valor in item.items()
                if chave != "cenario" and valor
            )
            linhas.append(f"- {cenario}" + (f" — {detalhes}" if detalhes else ""))
    if conversa:
        linhas += ["", "## Conversa pública até agora (rodada de re-triagem)"]
        linhas += [f"[{m['papel']}] {m['conteudo']}" for m in conversa]
        linhas += [
            "",
            "Considere as respostas do autor acima; não repita perguntas já respondidas.",
        ]
    if categorias:
        linhas += ["", "## Catálogo de categorias do departamento"]
        linhas += [_linha_catalogo(item) for item in categorias]
    prompt_nome = "quimico_passe_a" if _eh_quimico(chamado.get("departamento")) else "ti"
    return [
        {"role": "system", "content": _prompt(prompt_nome)},
        {"role": "user", "content": _conteudo_usuario(linhas, anexos)},
    ]


def montar_mensagens_passe_b(
    chamado: dict[str, Any],
    contexto: contexto_quimico.ContextoQuimico | None,
    anexos: anexos_contexto.ResultadoAnexos | None = None,
) -> list[dict[str, Any]]:
    """Mensagens system+user do Passe B do Químico (F4) — canal interno.

    ``contexto`` é a recuperação seletiva da base sigilosa (produto citado +
    ficha + playbooks, SEM quantidades — C7); ``None``/vazio degrada
    graciosamente (o modelo recebe só o chamado, mesmo padrão do passe único
    sem contexto extra). ``anexos`` (F7): mesmo objeto já baixado/processado
    para o Passe A — reaproveitado aqui sem novo download (Regra de Ouro #9)."""
    linhas = _linhas_chamado(chamado, anexos)
    texto_contexto = contexto_quimico.formatar_contexto(contexto) if contexto else ""
    if texto_contexto:
        linhas += ["", texto_contexto]
    return [
        {"role": "system", "content": _prompt("quimico_passe_b")},
        {"role": "user", "content": _conteudo_usuario(linhas, anexos)},
    ]


def _normalizar_texto(texto: str) -> str:
    """Forma canônica para comparar texto do modelo com texto nosso (acentuação,
    pontuação e caixa fora) — o modelo raramente devolve byte a byte igual.

    Usada para casar perguntas da rodada anterior (:func:`pendencias_do_ciclo`)
    e nomes de categoria/subcategoria contra o catálogo do departamento
    (:func:`resolver_reclassificacao`)."""
    sem_acento = unicodedata.normalize("NFKD", texto)
    sem_acento = "".join(c for c in sem_acento if not unicodedata.combining(c))
    return " ".join("".join(c if c.isalnum() else " " for c in sem_acento).split()).casefold()


def pendencias_do_ciclo(
    saida: SaidaTriagem, perguntas_anteriores: list[str] | None
) -> list[str]:
    """Perguntas da rodada anterior que o autor deixou EM BRANCO (função pura).

    Gate estrutural da rodada extra (``IA_TRIAGEM_MAX_RODADAS`` 2→3,
    2026-07-31): ela existe para cobrar o que o autor não respondeu, não para
    abrir uma bateria nova de perguntas. Cada item de
    ``saida.perguntas_nao_respondidas`` só vale se casar com uma pergunta
    REALMENTE feita na rodada anterior — assim o modelo não consegue inventar
    pendência nem reciclar o slot com outro assunto (BOND-2026-00653: o autor
    respondeu as 3 perguntas e recebeu outras 3 no mesmo minuto).

    Devolve o **texto original da rodada anterior**, na ordem original: o autor
    reconhece a pergunta que ficou faltando em vez de ler uma reformulação.
    """
    if not perguntas_anteriores or not saida.perguntas_nao_respondidas:
        return []
    alvos = [_normalizar_texto(p) for p in saida.perguntas_nao_respondidas]
    pendentes = []
    for anterior in perguntas_anteriores:
        chave = _normalizar_texto(anterior)
        if not chave:
            continue
        # Igualdade normalizada ou reformulação leve (o modelo às vezes corta a
        # pergunta ou troca uma palavra ao copiar); assunto novo não passa.
        if any(
            alvo == chave or SequenceMatcher(None, alvo, chave).ratio() >= 0.85
            for alvo in alvos
            if alvo
        ):
            pendentes.append(anterior)
    return pendentes


def decidir_acao(
    saida: SaidaTriagem,
    rodada: int,
    settings: Settings,
    atendimento_iniciado: bool = False,
    departamento: str | None = None,
    pendencias: list[str] | None = None,
) -> str:
    """Decide entre nota interna e perguntas públicas (Seção 2.3, função pura).

    Informação suficiente ⇒ nota DIRETO. Insuficiente ⇒ o ciclo de perguntas
    vem primeiro e a nota fica para o fim do ciclo (decisão do usuário
    2026-07-23). ``PERGUNTAS`` exige TODAS: ninguém atendendo ainda (com
    atendimento humano em curso, interpelar o autor seria ruído); fora do modo
    sombra — avaliado POR DEPARTAMENTO (F2, 2026-07-24:
    ``Settings.ia_triagem_em_sombra`` — o TI liberado pergunta enquanto o
    Químico segue em sombra até o red team); informação insuficiente; há
    perguntas; confiança no mínimo `IA_TRIAGEM_PERGUNTAS_CONFIANCA_MINIMA`
    (decisão do usuário 2026-07-24: default BAIXA — todas perguntam; era ALTA
    fixa, mitigação 10.1, e reapertar é só env); e ainda há rodada disponível
    (na última, a nota interna sai com as lacunas sinalizadas).

    ``pendencias`` (:func:`pendencias_do_ciclo`) fecha o ciclo nas rodadas de
    re-triagem: sem pergunta anterior deixada em branco, a rodada extra vira
    NOTA_INTERNA — o autor já respondeu o que sabia e insistir só atrasa o
    atendimento (BOND-2026-00653)."""
    if atendimento_iniciado:
        return "NOTA_INTERNA"
    if settings.ia_triagem_em_sombra(departamento):
        return "NOTA_INTERNA"
    if saida.informacoes_suficientes or not saida.perguntas:
        return "NOTA_INTERNA"
    if rodada > 1 and not pendencias:
        return "NOTA_INTERNA"
    minima = settings.ia_triagem_perguntas_confianca_minima.strip().upper()
    # Env com valor desconhecido degrada para o mais conservador (só ALTA).
    if _CONFIANCA_ORDEM.get(saida.confianca, 0) < _CONFIANCA_ORDEM.get(
        minima, _CONFIANCA_ORDEM["ALTA"]
    ):
        return "NOTA_INTERNA"
    if rodada >= settings.ia_triagem_max_rodadas:
        return "NOTA_INTERNA"
    return "PERGUNTAS"


@dataclass(frozen=True)
class Reclassificacao:
    """Troca de categoria/subcategoria já resolvida contra o catálogo real.

    Só existe quando TODAS as guardas puras passaram — quem constrói é
    :func:`resolver_reclassificacao`, e os ids vêm do catálogo do departamento
    do próprio chamado (o modelo devolve nome, nunca id)."""

    categoria_id: str
    categoria_nome: str
    subcategoria_id: str | None
    subcategoria_nome: str | None
    de_categoria: str | None
    de_subcategoria: str | None
    motivo: str


def _indice_catalogo(categorias: list[Any]) -> dict[str, dict[str, Any]]:
    """Catálogo indexado por nome normalizado (só entradas com id — o formato
    ``list[str]``, usado por testes antigos e pelo red team, não reclassifica
    nada por construção: sem id não há destino)."""
    indice: dict[str, dict[str, Any]] = {}
    for item in categorias:
        if not isinstance(item, dict) or not item.get("id"):
            continue
        chave = _normalizar_texto(_nome_categoria(item))
        if chave:
            indice[chave] = item
    return indice


def _achar_subcategoria(categoria: dict[str, Any], nome: str | None) -> dict[str, Any] | None:
    if not nome:
        return None
    alvo = _normalizar_texto(nome)
    if not alvo:
        return None
    for sub in categoria.get("subcategorias") or []:
        if isinstance(sub, dict) and sub.get("id") and _normalizar_texto(str(sub.get("nome") or "")) == alvo:
            return sub
    return None


def resolver_reclassificacao(
    saida: SaidaTriagem,
    chamado: dict[str, Any],
    categorias: list[Any],
    settings: Settings,
) -> Reclassificacao | None:
    """Destino da reclassificação automática, ou ``None`` para não mexer (F8).

    Função pura — todo o julgamento fica aqui, testável sem banco. A IA passou
    a poder corrigir a classificação (pedido do gestor 2026-08-04, ADR 0008),
    mas só sob condições que não dependem da boa vontade do modelo:

    1. departamento liberado em ``IA_TRIAGEM_RECLASSIFICACAO_DEPARTAMENTOS``
       (default vazio ⇒ ninguém — kill switch por construção);
    2. o modelo declarou ``categoria_divergente`` (não basta ter devolvido uma
       ``categoria_sugerida``: sugerir continua sendo o caminho da dúvida);
    3. confiança ≥ ``IA_TRIAGEM_RECLASSIFICACAO_CONFIANCA_MINIMA`` (default
       ALTA; env inválida degrada para ALTA);
    4. justificativa não vazia — é o que o atendente lê na nota e no histórico;
    5. o destino EXISTE no catálogo ativo do departamento do próprio chamado
       (casamento por nome normalizado): nome inventado não vira escrita, e não
       há como escapar para categoria de outro setor;
    6. o resultado difere do par (categoria, subcategoria) atual.

    Subcategoria: quando a categoria muda, a subcategoria antiga fica órfã —
    vira a sugerida, se ela existir na categoria nova, ou ``None`` (mesma regra
    da rota do staff, ``workspace.alterar_categoria``). Quando a categoria não
    muda, uma subcategoria sugerida válida ainda pode ser corrigida sozinha.
    """
    if not settings.ia_triagem_reclassifica(chamado.get("departamento")):
        return None
    if not saida.categoria_divergente:
        return None
    minima = settings.ia_triagem_reclassificacao_confianca_minima.strip().upper()
    if _CONFIANCA_ORDEM.get(saida.confianca, 0) < _CONFIANCA_ORDEM.get(
        minima, _CONFIANCA_ORDEM["ALTA"]
    ):
        return None
    motivo = (saida.categoria_justificativa or "").strip()
    if not motivo:
        return None

    indice = _indice_catalogo(categorias)
    if not indice:
        return None
    atual_nome = chamado.get("categoria")
    if saida.categoria_sugerida:
        destino = indice.get(_normalizar_texto(saida.categoria_sugerida))
    else:
        # Sem categoria nova: só a subcategoria pode estar errada. A categoria
        # atual precisa existir no catálogo para ancorar a busca da sub.
        destino = indice.get(_normalizar_texto(atual_nome or ""))
    if destino is None:
        return None  # nome fora do catálogo ⇒ não se inventa destino

    categoria_mudou = str(destino["id"]) != str(chamado.get("categoria_id") or "")
    sub = _achar_subcategoria(destino, saida.subcategoria_sugerida)
    if sub is not None:
        sub_id: str | None = str(sub["id"])
        sub_nome: str | None = str(sub.get("nome") or "")
    elif categoria_mudou:
        sub_id, sub_nome = None, None  # órfã da categoria antiga: limpa
    else:
        sub_id = str(chamado.get("subcategoria_id") or "") or None
        sub_nome = chamado.get("subcategoria")

    if not categoria_mudou and sub_id == (str(chamado.get("subcategoria_id") or "") or None):
        return None  # nada a fazer
    return Reclassificacao(
        categoria_id=str(destino["id"]),
        categoria_nome=_nome_categoria(destino),
        subcategoria_id=sub_id,
        subcategoria_nome=sub_nome,
        de_categoria=atual_nome,
        de_subcategoria=chamado.get("subcategoria"),
        motivo=motivo,
    )


async def _aplicar_reclassificacao(
    conn: Any,
    chamado: dict[str, Any],
    reclassificacao: Reclassificacao,
    perfil_id: str,
    rodada: int,
    confianca: str,
) -> bool:
    """Escreve a reclassificação no chamado + histórico (F8). ``False`` = não
    aplicou (alguma guarda de banco/estado barrou) e a nota volta a apenas
    sugerir.

    Guardas que só existem aqui (as puras estão em
    :func:`resolver_reclassificacao`):

    - **formulário dinâmico:** ``dados_formulario`` preenchido significa que os
      campos foram respondidos contra o schema da categoria atual
      (``app/domain/formularios_quimico.py`` indexa o layout pelo NOME da
      categoria) — trocar a categoria órfanaria os rótulos. Chamado com
      formulário não é reclassificado, e é isso que mantém o Dpto Químico fora
      da feature mesmo que alguém o liste na env;
    - **classificação virgem:** já houve ``CATEGORIA_ALTERADA`` (staff) ou
      ``IA_RECLASSIFICACAO`` antes ⇒ a IA não desfaz decisão de humano nem
      briga consigo mesma entre rodadas;
    - **compare-and-swap:** o ``UPDATE`` casa a categoria que foi analisada; se
      alguém reclassificou durante a chamada de modelo, nenhuma linha é tocada.
    """
    if chamado.get("dados_formulario"):
        return False
    ja_classificado = await conn.fetchval(
        """
        SELECT 1 FROM historico_chamados
         WHERE chamado_id = $1::uuid
           AND acao IN ('CATEGORIA_ALTERADA', 'IA_RECLASSIFICACAO')
         LIMIT 1
        """,
        chamado["id"],
    )
    if ja_classificado:
        return False
    atualizado = await conn.fetchval(
        """
        UPDATE chamados
           SET categoria_id = $2::uuid, subcategoria_id = $3::uuid
         WHERE id = $1::uuid
           AND categoria_id IS NOT DISTINCT FROM $4::uuid
        RETURNING id::text
        """,
        chamado["id"],
        reclassificacao.categoria_id,
        reclassificacao.subcategoria_id,
        chamado.get("categoria_id"),
    )
    if atualizado is None:
        return False
    await conn.execute(
        """
        INSERT INTO historico_chamados (chamado_id, ator_id, acao, detalhes)
        VALUES ($1::uuid, $2::uuid, 'IA_RECLASSIFICACAO', $3::jsonb)
        """,
        chamado["id"],
        perfil_id,
        json.dumps(
            {
                "rodada": rodada,
                "confianca": confianca,
                "de": {
                    "categoria": reclassificacao.de_categoria,
                    "subcategoria": reclassificacao.de_subcategoria,
                    "categoria_id": chamado.get("categoria_id"),
                    "subcategoria_id": chamado.get("subcategoria_id"),
                },
                "para": {
                    "categoria": reclassificacao.categoria_nome,
                    "subcategoria": reclassificacao.subcategoria_nome,
                    "categoria_id": reclassificacao.categoria_id,
                    "subcategoria_id": reclassificacao.subcategoria_id,
                },
                "motivo": reclassificacao.motivo,
            },
            ensure_ascii=False,
        ),
    )
    # Estado em memória segue o banco: a nota (e o Passe B do Químico) já falam
    # da classificação nova.
    chamado["categoria_id"] = reclassificacao.categoria_id
    chamado["categoria"] = reclassificacao.categoria_nome
    chamado["subcategoria_id"] = reclassificacao.subcategoria_id
    chamado["subcategoria"] = reclassificacao.subcategoria_nome
    return True


def _linhas_reclassificacao(reclassificacao: Reclassificacao | None) -> list[str]:
    """Bloco da nota interna que registra o que a IA trocou (F8) — sempre
    explícito: quem atende precisa ver que a classificação não é mais a que o
    autor escolheu, e por quê."""
    if reclassificacao is None:
        return []
    linha = (
        "Categoria reclassificada automaticamente pela triagem: "
        f"{reclassificacao.de_categoria or '—'} → {reclassificacao.categoria_nome}"
    )
    if reclassificacao.subcategoria_nome or reclassificacao.de_subcategoria:
        linha += (
            f" (subcategoria: {reclassificacao.de_subcategoria or '—'}"
            f" → {reclassificacao.subcategoria_nome or '—'})"
        )
    return ["", linha, f"Motivo da reclassificação: {reclassificacao.motivo}"]


def _resumir_resolucao(texto: str | None, max_chars: int = 280) -> str:
    """Resolução do chamado semelhante numa linha (espaços normalizados,
    truncada) — a nota cita a solução, não reproduz a conversa inteira."""
    limpo = " ".join((texto or "").split())
    if len(limpo) > max_chars:
        limpo = limpo[: max_chars - 1].rstrip() + "…"
    return limpo


def montar_nota(
    saida: SaidaTriagem,
    chamado: dict[str, Any],
    semelhantes: list[dict[str, Any]] | None = None,
    reclassificacao: Reclassificacao | None = None,
) -> str:
    """Texto da nota interna a partir da saída estruturada (função pura).

    ``semelhantes`` (F3): chamados resolvidos citados como referência (código +
    resolução registrada). Lista vazia/None omite a seção — degradação graciosa
    (DoD F3: seção omitida, nunca inventada).

    ``reclassificacao`` (F8): quando a troca de categoria/subcategoria foi
    APLICADA, a nota registra de→para e o motivo, e a linha "Categoria
    sugerida" deixa de fazer sentido (já é a categoria do chamado). Sem
    reclassificação aplicada, a sugestão continua saindo como sempre."""
    partes = ["Triagem automática — pré-análise da IA", "", saida.pre_analise.strip()]
    partes += _linhas_reclassificacao(reclassificacao)

    sugestoes = []
    if (
        reclassificacao is None
        and saida.categoria_sugerida
        and saida.categoria_sugerida != (chamado.get("categoria") or "")
    ):
        sugestoes.append(
            f"Categoria sugerida: {saida.categoria_sugerida}"
            f" (escolhida: {chamado.get('categoria') or '—'})"
        )
    if saida.prioridade_sugerida and saida.prioridade_sugerida != (
        chamado.get("prioridade") or ""
    ):
        sugestoes.append(
            f"Prioridade sugerida: {saida.prioridade_sugerida}"
            f" (declarada: {chamado.get('prioridade') or '—'})"
        )
    if sugestoes:
        partes += ["", *sugestoes]

    if semelhantes:
        partes += ["", "Casos semelhantes já resolvidos:"]
        for s in semelhantes:
            linha = f"- {s.get('codigo') or '?'} — {s.get('titulo') or ''}".rstrip(" —")
            resolucao = _resumir_resolucao(s.get("resolucao"))
            if resolucao:
                linha += f" · resolução registrada: {resolucao}"
            partes.append(linha)

    if not saida.informacoes_suficientes and saida.perguntas:
        partes += ["", "Informações faltantes — perguntas que a IA faria ao autor:"]
        partes += [f"{i}. {p}" for i, p in enumerate(saida.perguntas, start=1)]

    partes += ["", f"Confiança: {saida.confianca} · Gerado por IA — valide antes de agir."]
    return "\n".join(partes)


def montar_nota_quimico(
    saida: SaidaPasseB,
    chamado: dict[str, Any],
    semelhantes: list[dict[str, Any]] | None = None,
    reclassificacao: Reclassificacao | None = None,
) -> str:
    """Texto da nota interna do Passe B do Químico (F4, função pura).

    Só chamada quando o Passe B teve sucesso — a falha degrada para
    :func:`montar_nota` com a saída do Passe A (fallback, Regra de Ouro #5).

    ``reclassificacao`` (F8) só chega preenchida se o Químico for liberado na
    env E o chamado não tiver formulário dinâmico — hoje nenhuma das duas
    condições vale em produção (o prompt do Passe A sequer conhece os campos),
    mas a nota registra a troca do mesmo jeito se um dia valer."""
    partes = [
        "Triagem automática — pré-análise técnica da IA (Químico)",
        "",
        saida.pre_analise.strip(),
    ]
    partes += _linhas_reclassificacao(reclassificacao)
    if saida.escalar_para_quimico:
        partes += ["", "⚠️ ESCALAR para o químico responsável."]
    if saida.produto_reconhecido:
        partes += ["", f"Produto reconhecido: {saida.produto_reconhecido}"]
    if semelhantes:
        partes += ["", "Casos semelhantes já resolvidos:"]
        for s in semelhantes:
            linha = f"- {s.get('codigo') or '?'} — {s.get('titulo') or ''}".rstrip(" —")
            resolucao = _resumir_resolucao(s.get("resolucao"))
            if resolucao:
                linha += f" · resolução registrada: {resolucao}"
            partes.append(linha)
    if saida.dados_faltantes:
        partes += ["", "Dados a confirmar com o atendente:"]
        partes += [f"{i}. {d}" for i, d in enumerate(saida.dados_faltantes, start=1)]
    partes += ["", f"Confiança: {saida.confianca} · Gerado por IA — valide antes de agir."]
    return "\n".join(partes)


def montar_pergunta_publica(
    saida: SaidaTriagem,
    chamado: dict[str, Any],
    perguntas: list[str] | None = None,
    rodada: int = 1,
) -> str:
    """Mensagem pública ao autor com as perguntas da triagem (função pura).

    ``perguntas``/``rodada``: na re-triagem o motor passa só as pendências
    (:func:`pendencias_do_ciclo`) e a abertura muda — o autor acabou de
    responder, então a mensagem agradece e cobra o que ficou em branco, em vez
    de repetir a saudação inicial como se fosse o primeiro contato."""
    itens = perguntas if perguntas is not None else saida.perguntas
    codigo = chamado.get("codigo") or ""
    ref = f" (chamado {codigo})" if codigo else ""
    if rodada > 1:
        abertura = (
            f"Obrigado pela resposta!{ref} Para o atendente seguir, ficou faltando apenas:"
        )
    else:
        abertura = (
            f"Olá! Sou o assistente virtual do suporte{ref}."
            " Para agilizar seu atendimento, poderia responder:"
        )
    partes = [abertura, ""]
    partes += [f"{i}. {p}" for i, p in enumerate(itens, start=1)]
    plural = len(itens) > 1
    partes += [
        "",
        (
            "Por favor, responda "
            + ("todas as perguntas acima numa única mensagem" if plural else "a pergunta acima")
            + " (aqui no chamado ou a este e-mail) — assim o atendente já recebe tudo de uma vez. "
            "Um atendente dará sequência assim que possível."
        ),
    ]
    return "\n".join(partes)


async def _salvar_nota_interna(conn: Any, chamado_id: str, remetente_id: str, conteudo: str) -> None:
    """Persiste a pré-análise SEMPRE como nota interna.

    Invariante da Seção 8.2: ``is_interna`` NÃO é parâmetro — é ``true`` fixado
    no SQL. Esta função é o único destino da nota; o canal público é
    :func:`_enviar_pergunta_publica`, usado exclusivamente na ação PERGUNTAS.
    """
    await conn.execute(
        "INSERT INTO mensagens (chamado_id, remetente_id, conteudo, is_interna, anexos) "
        "VALUES ($1::uuid, $2::uuid, $3, true, '[]'::jsonb)",
        chamado_id,
        remetente_id,
        conteudo,
    )


async def _enviar_pergunta_publica(
    conn: Any, chamado_id: str, remetente_id: str, conteudo: str
) -> None:
    """Mensagem pública ao autor (ação PERGUNTAS, F2). ``is_interna=false``
    fixado — este é o único caminho do motor que escreve no canal público."""
    await conn.execute(
        "INSERT INTO mensagens (chamado_id, remetente_id, conteudo, is_interna, anexos) "
        "VALUES ($1::uuid, $2::uuid, $3, false, '[]'::jsonb)",
        chamado_id,
        remetente_id,
        conteudo,
    )


async def _registrar_historico(
    conn: Any,
    chamado_id: str,
    ator_id: str,
    rodada: int,
    passe: str,
    acao: str,
    settings: Settings,
    modelo: str | None = None,
    em_sombra: bool | None = None,
) -> None:
    await conn.execute(
        """
        INSERT INTO historico_chamados (chamado_id, ator_id, acao, detalhes)
        VALUES ($1::uuid, $2::uuid, 'IA_TRIAGEM', $3::jsonb)
        """,
        chamado_id,
        ator_id,
        json.dumps(
            {
                "rodada": rodada,
                "passe": passe,
                "acao": acao,
                "modelo": modelo or settings.ia_triagem_model,
                # Sombra EFETIVA do departamento (F2 por departamento) — cai
                # para a global quando o chamador não informa.
                "modo_sombra": (
                    settings.ia_triagem_modo_sombra if em_sombra is None else em_sombra
                ),
            }
        ),
    )


async def _obter_perfil_ia_id(conn: Any) -> str | None:
    global _perfil_ia_id
    if _perfil_ia_id is None:
        _perfil_ia_id = await conn.fetchval(
            "SELECT id::text FROM perfis WHERE nome = $1 LIMIT 1", PERFIL_IA_NOME
        )
    return _perfil_ia_id


def _soma_tokens(acumulado: int | None, novo: int | None) -> int | None:
    if novo is None:
        return acumulado
    return (acumulado or 0) + novo


async def _chamar_modelo[SaidaT: BaseModel](
    mensagens: list[dict[str, Any]],
    settings: Settings,
    *,
    model: str | None = None,
    schema: type[SaidaT] = SaidaTriagem,  # type: ignore[assignment]
) -> tuple[SaidaT | None, str | None, int | None, int | None]:
    """Uma chamada estruturada + 1 retry em JSON inválido (Seção 2.1).

    Devolve ``(saida, erro, tokens_entrada, tokens_saida)`` — tokens somados
    entre tentativas (custo real). Falha de provedor não tem retry: vira erro
    silencioso direto (a triagem nunca segura a abertura). ``model``/``schema``
    plugáveis para o Passe B do Químico (F4): mesmo motor, contrato JSON
    diferente (:class:`app.ia.schemas.SaidaPasseB`) e modelo opcionalmente mais
    forte (``IA_TRIAGEM_MODEL_PASSE_B``). Wrapper fino sobre
    :func:`app.ia.chamada_estruturada.chamar_modelo_estruturado`, injetando os
    settings de triagem — extraído para ser reaproveitado por outros fluxos
    (ex.: intake de chamado via WhatsApp) sem duplicar a lógica de retry."""
    return await chamar_modelo_estruturado(
        mensagens,
        model=model or settings.ia_triagem_model,
        api_key=settings.ia_triagem_api_key,
        base_url=settings.ia_triagem_base_url,
        timeout_s=settings.ia_triagem_timeout_s,
        max_tokens=_MAX_TOKENS_SAIDA,
        schema=schema,
    )


# Referências fortes às tasks disparadas (asyncio só guarda referência fraca:
# sem isso, o GC pode matar a triagem no meio — recomendação da própria doc).
_tasks_ativas: set[asyncio.Task] = set()


def agendar_triagem(chamado_id: str) -> None:
    """Dispara a triagem AGORA, em paralelo à resposta (``asyncio.create_task``).

    Por que não ``BackgroundTasks`` (otimização de latência, 2026-07-23): o
    Starlette só roda a task depois que a resposta atravessa TODA a cadeia de
    middlewares e é consumida — qualquer coisa presa nesse caminho (middleware,
    proxy, cliente lento) segura a triagem junto. Em produção as 2 primeiras
    triagens da sombra saíram 8 e 28 min após a abertura com o modelo levando
    ~4 s (meta p95 < 2 min, Seção 9). Com ``create_task`` a triagem começa no
    instante do agendamento, independente do ciclo da resposta; o motor
    revalida tudo no banco, então rodar "cedo demais" é seguro por construção.
    """
    task = asyncio.create_task(executar_triagem(chamado_id, agendada_em=time.time()))
    _tasks_ativas.add(task)
    task.add_done_callback(_tasks_ativas.discard)


async def executar_triagem(chamado_id: str, agendada_em: float | None = None) -> None:
    """Entrada da background task — tolerante a falha ponta a ponta (Regra #5).

    Chamada tanto na abertura (rodada 1) quanto na resposta do autor
    (re-triagem, F2): a rodada certa é derivada do banco.

    ``agendada_em`` (``time.time()`` de quem agendou): instrumentação do gap
    agendamento→execução — em produção (2026-07-23) as duas primeiras triagens
    reais saíram 8 e 28 min após a abertura com `duracao_ms` de ~4 s, ou seja,
    o tempo se perde ANTES da task rodar (meta p95 < 2 min, Seção 9). O log
    permite atribuir o atraso à infra (deploy/loop de eventos) com dado real."""
    if agendada_em is not None:
        espera_s = time.time() - agendada_em
        nivel = log.warning if espera_s > 60 else log.info
        nivel(
            "[IA TRIAGEM] Task do chamado %s iniciou %.1fs após o agendamento.",
            chamado_id,
            espera_s,
        )
    try:
        await _executar(chamado_id)
    except Exception as exc:  # noqa: BLE001 — task de fundo nunca derruba nada
        log.warning("[IA TRIAGEM] Erro inesperado no chamado %s: %s", chamado_id, exc)


async def _executar(chamado_id: str) -> None:
    settings = get_settings()
    if not settings.ia_triagem_ativa or not settings.ia_triagem_api_key:
        return  # kill switch: nenhum efeito colateral (Seção 8.2, teste 5)

    # 1) Contexto + máquina de rodadas — conexão curta; a chamada HTTP fica FORA.
    t_inicio_db = time.monotonic()
    async with admin_connection() as conn:
        espera_pool_s = time.monotonic() - t_inicio_db
        if espera_pool_s > 5:
            # Pool saturado/Supavisor lento é candidato a explicar gaps grandes.
            log.warning(
                "[IA TRIAGEM] Chamado %s esperou %.1fs por conexão do pool.",
                chamado_id,
                espera_pool_s,
            )
        chamado = await conn.fetchrow(
            """
            SELECT c.id::text AS id, c.codigo, c.titulo, c.descricao,
                   c.status::text AS status, c.cliente_id, c.operador_id,
                   c.prioridade::text AS prioridade, c.dados_formulario,
                   c.departamento_id, cat.nome AS categoria, d.nome AS departamento,
                   c.categoria_id::text AS categoria_id,
                   c.subcategoria_id::text AS subcategoria_id,
                   sub.nome AS subcategoria
            FROM chamados c
            JOIN departamentos d ON d.id = c.departamento_id
            LEFT JOIN categorias cat ON cat.id = c.categoria_id
            LEFT JOIN subcategorias sub ON sub.id = c.subcategoria_id
            WHERE c.id = $1::uuid
            """,
            chamado_id,
        )
        if chamado is None:
            return
        chamado = dict(chamado)
        if chamado["departamento"] not in settings.ia_triagem_departamentos_lista:
            return
        # Guarda de atendimento (Seção 2.3, ajuste 2026-07-23): atendente atuando
        # NÃO suprime a pré-análise da rodada 1 — a nota interna é para quem
        # atende. O que fica vetado é interpelar o autor (PERGUNTAS, decidido em
        # `decidir_acao`) e re-triagem. Chamado encerrado não é triado.
        if chamado["status"] == "RESOLVIDO":
            return
        atendimento_iniciado = (
            chamado["status"] != "NOVO" or chamado["operador_id"] is not None
        )

        # Rodada derivada do banco (idempotência por construção).
        info = await conn.fetchrow(
            """
            SELECT COALESCE(MAX(t.rodada), 0) AS ultima_rodada,
                   (SELECT t2.acao FROM ia_triagens t2 WHERE t2.chamado_id = $1::uuid
                     ORDER BY t2.rodada DESC, t2.id DESC LIMIT 1) AS ultima_acao,
                   (SELECT t2.created_at FROM ia_triagens t2 WHERE t2.chamado_id = $1::uuid
                     ORDER BY t2.rodada DESC, t2.id DESC LIMIT 1) AS ultima_em,
                   (SELECT t2.resultado->'perguntas' FROM ia_triagens t2
                     WHERE t2.chamado_id = $1::uuid
                     ORDER BY t2.rodada DESC, t2.id DESC LIMIT 1) AS ultimas_perguntas
              FROM ia_triagens t WHERE t.chamado_id = $1::uuid
            """,
            chamado_id,
        )
        rodada = int(info["ultima_rodada"]) + 1
        if rodada > settings.ia_triagem_max_rodadas:
            return  # teto: a rodada N+1 além do máximo é impossível (Regra #3)
        conversa: list[dict[str, str]] = []
        # Perguntas efetivamente feitas na rodada anterior — base do gate de
        # pendências da re-triagem (`pendencias_do_ciclo`).
        perguntas_anteriores: list[str] = []
        if rodada > 1:
            # Re-triagem só enquanto nenhum atendente atua (Seção 2.3).
            if atendimento_iniciado:
                return
            # Re-triagem só faz sentido se a IA perguntou E o autor respondeu
            # DEPOIS da pergunta (task duplicada/replay não re-tria).
            if info["ultima_acao"] != "PERGUNTAS":
                return
            respondeu = await conn.fetchval(
                """
                SELECT 1 FROM mensagens
                 WHERE chamado_id = $1::uuid AND remetente_id = $2
                   AND is_interna = false AND created_at > $3
                 LIMIT 1
                """,
                chamado_id,
                chamado["cliente_id"],
                info["ultima_em"],
            )
            if not respondeu:
                return
            bruto_perguntas = info["ultimas_perguntas"]
            if isinstance(bruto_perguntas, str):  # jsonb chega como str no asyncpg
                bruto_perguntas = json.loads(bruto_perguntas)
            perguntas_anteriores = [str(p) for p in (bruto_perguntas or [])]
            conversa = [
                {
                    "papel": "Autor" if r["remetente_id"] == chamado["cliente_id"] else "Equipe",
                    "conteudo": r["conteudo"],
                }
                for r in await conn.fetch(
                    """
                    SELECT m.conteudo, m.remetente_id FROM mensagens m
                     WHERE m.chamado_id = $1::uuid AND m.is_interna = false
                       AND m.conteudo <> ''
                     ORDER BY m.created_at
                    """,
                    chamado_id,
                )
            ]
        # Catálogo do departamento com as subcategorias ativas de cada categoria
        # (F8): é o contexto do modelo E o conjunto fechado de destinos que a
        # reclassificação pode escolher — nome fora daqui não vira escrita.
        categorias = [
            _linha_catalogo_do_banco(dict(r))
            for r in await conn.fetch(
                """
                SELECT cat.id::text AS id, cat.nome,
                       COALESCE(
                         json_agg(
                           json_build_object('id', sub.id::text, 'nome', sub.nome)
                           ORDER BY sub.nome
                         ) FILTER (WHERE sub.id IS NOT NULL),
                         '[]'::json
                       ) AS subcategorias
                  FROM categorias cat
                  LEFT JOIN subcategorias sub
                         ON sub.categoria_id = cat.id AND sub.ativo
                 WHERE cat.departamento_id = $1 AND cat.ativo
                 GROUP BY cat.id, cat.nome
                 ORDER BY cat.nome
                """,
                chamado["departamento_id"],
            )
        ]

        # Anexos (F7): metadados só, gated pelo kill switch (zero query extra
        # quando desligado). Cobre a mensagem de abertura (conteudo="", por
        # isso FORA do filtro `conteudo <> ''` usado acima para `conversa`) e
        # qualquer anexo enviado em respostas seguintes.
        anexos_brutos: list[dict[str, Any]] = []
        if settings.ia_triagem_anexos_ativo:
            for r in await conn.fetch(
                """
                SELECT m.anexos FROM mensagens m
                 WHERE m.chamado_id = $1::uuid AND m.is_interna = false
                   AND m.anexos <> '[]'::jsonb
                 ORDER BY m.created_at
                """,
                chamado_id,
            ):
                bruto = r["anexos"]
                anexos_brutos += json.loads(bruto) if isinstance(bruto, str) else bruto

    if isinstance(chamado.get("dados_formulario"), str):  # jsonb chega como str no asyncpg
        chamado["dados_formulario"] = json.loads(chamado["dados_formulario"])

    eh_quimico = _eh_quimico(chamado.get("departamento"))
    playbook: list[dict[str, Any]] = []
    if eh_quimico:
        # Passe A do Químico: só o roteiro de perguntas de investigação, SEM
        # dado de produto — `playbook_perguntas` é a única função deste módulo
        # que este caminho pode chamar (Seção 3.1 do plano IA).
        playbook = await contexto_quimico.playbook_perguntas(settings)

    # 1b) Download/processamento de anexos (F7) — HTTP fica FORA da conexão,
    # mesma disciplina do resto do arquivo. Roda uma única vez e é
    # reaproveitado no Passe A e no Passe B do Químico (sem download nem
    # chamada de modelo duplicada — Regra de Ouro #9). Nunca lança: falha
    # aqui nunca derruba a triagem (Regra de Ouro #5).
    resultado_anexos = anexos_contexto.ResultadoAnexos()
    if anexos_brutos:
        storage = await ensure_storage()
        if storage is not None and settings.supabase_service_role_key:
            try:
                resultado_anexos = await anexos_contexto.montar_contexto_anexos(
                    storage, settings.supabase_service_role_key, anexos_brutos,
                    settings, eh_quimico=eh_quimico,
                )
            except Exception as exc:  # noqa: BLE001 — anexos nunca derrubam a triagem
                log.warning(
                    "[IA TRIAGEM] Contexto de anexos falhou no chamado %s: %s", chamado_id, exc
                )

    # 2) Passe A / passe único — uma chamada estruturada (+1 retry de JSON,
    # Regra #9). O Passe B do Químico (item 5) só roda depois, se este decidir
    # NOTA_INTERNA — cada passe continua sendo uma única chamada estruturada.
    inicio = time.monotonic()
    saida, erro, tokens_in, tokens_out = await _chamar_modelo(
        montar_mensagens(chamado, categorias, conversa or None, playbook or None, resultado_anexos),
        settings,
        schema=SaidaTriagem,
    )
    duracao_ms = int((time.monotonic() - inicio) * 1000)

    # 3) Persistência do passe A / único: auditoria + mensagem (nota OU
    # pergunta) + histórico. O Passe B do Químico tem persistência própria —
    # roda DEPOIS, fora desta conexão (mesma disciplina "HTTP fica fora").
    passe_a_nome = _PASSE_A if eh_quimico else _PASSE_UNICO
    email_pergunta: str | None = None
    semelhantes: list[dict[str, Any]] = []
    acao = "ERRO"
    perfil_id: str | None = None
    async with admin_connection() as conn:
        perfil_id = await _obter_perfil_ia_id(conn)
        if saida is not None and perfil_id is None:
            saida, erro = None, "perfil_assistente_ia_ausente"
        # Reavalia a guarda com estado FRESCO (Seção 2.3): se o atendente assumiu
        # durante a chamada de modelo, a pergunta pública vira nota interna.
        estado = await conn.fetchrow(
            "SELECT status::text AS status, operador_id FROM chamados WHERE id = $1::uuid",
            chamado_id,
        )
        if estado is not None:
            atendimento_iniciado = (
                estado["status"] != "NOVO" or estado["operador_id"] is not None
            )
        em_sombra = settings.ia_triagem_em_sombra(chamado["departamento"])
        # Re-triagem: a rodada extra só cobra o que o autor deixou em branco
        # (gate estrutural — ver `pendencias_do_ciclo`).
        pendencias = (
            pendencias_do_ciclo(saida, perguntas_anteriores) if saida is not None else []
        )
        acao = (
            decidir_acao(
                saida,
                rodada,
                settings,
                atendimento_iniciado,
                chamado["departamento"],
                pendencias,
            )
            if saida is not None
            else "ERRO"
        )
        # Busca de semelhantes (F3): só quando a ação é nota interna (a pergunta
        # pública não cita casos) e o modelo devolveu termos. Falha da busca é
        # silenciosa — a nota sai sem a seção, nunca deixa de sair (Regra #5).
        if saida is not None and acao == "NOTA_INTERNA" and saida.termos_busca:
            try:
                semelhantes = await ia_busca.buscar_semelhantes(
                    conn,
                    departamento_id=chamado["departamento_id"],
                    chamado_id=chamado_id,
                    termos=saida.termos_busca,
                )
            except Exception as exc:  # noqa: BLE001 — busca nunca derruba a triagem
                log.warning(
                    "[IA TRIAGEM] Busca de semelhantes falhou no chamado %s: %s",
                    chamado_id,
                    exc,
                )
        resultado = saida.model_dump() if saida is not None else {"erro": erro or "desconhecido"}
        if rodada > 1 and saida is not None:
            # Auditável: o que a rodada extra cobrou de fato (subconjunto das
            # perguntas da rodada anterior), separado do que o modelo pediu.
            resultado["perguntas_cobradas"] = pendencias
        if semelhantes:
            # Auditável em `ia_triagens.resultado`: QUAIS casos foram citados.
            resultado["semelhantes_codigos"] = [s.get("codigo") for s in semelhantes]
        triagem_id = await conn.fetchval(
            """
            INSERT INTO ia_triagens
              (chamado_id, rodada, passe, acao, resultado, modelo,
               tokens_entrada, tokens_saida, custo_usd, duracao_ms)
            VALUES ($1::uuid, $2, $3, $4, $5::jsonb, $6, $7, $8, $9, $10)
            ON CONFLICT (chamado_id, rodada, passe) DO NOTHING
            RETURNING id
            """,
            chamado_id,
            rodada,
            passe_a_nome,
            acao,
            json.dumps(resultado, ensure_ascii=False),
            settings.ia_triagem_model,
            tokens_in,
            tokens_out,
            cliente.custo_usd(settings.ia_triagem_model, tokens_in, tokens_out),
            duracao_ms,
        )
        if triagem_id is None:
            # Corrida com outra execução (retry duplicado): quem chegou antes já
            # gravou a mensagem — não duplica (UNIQUE + verificação de rodada).
            return
        if saida is None:
            log.warning("[IA TRIAGEM] Chamado %s sem triagem útil: %s", chamado_id, erro)
            return
        assert perfil_id is not None  # garantido pelo guard acima (saida None ⇒ já saiu)

        # 3b) Reclassificação automática (F8): a única escrita do motor no
        # próprio chamado. Roda DEPOIS do INSERT em `ia_triagens` de propósito —
        # a execução que perdeu a corrida do UNIQUE já saiu acima, então a troca
        # acontece uma vez só. Vale para as duas ações: uma categoria errada
        # atrapalha o roteamento tanto no ciclo de perguntas quanto na nota.
        # `resultado` (o que o modelo PROPÔS) já está gravado; o que foi
        # APLICADO fica em `historico_chamados` (evento `IA_RECLASSIFICACAO`),
        # que é a auditoria de estado do chamado.
        reclassificacao = resolver_reclassificacao(saida, chamado, categorias, settings)
        if reclassificacao is not None and not atendimento_iniciado:
            aplicada = await _aplicar_reclassificacao(
                conn, chamado, reclassificacao, perfil_id, rodada, saida.confianca
            )
            if not aplicada:
                reclassificacao = None  # a nota volta a apenas sugerir
        else:
            reclassificacao = None

        if acao == "PERGUNTAS":
            # Ciclo de perguntas primeiro (decisão do usuário 2026-07-23): a
            # nota interna fica para o fim do ciclo — rodada N+1 (resposta do
            # autor) ou teto de rodadas, com as lacunas sinalizadas. O Passe B
            # do Químico nunca roda nesta rodada — a base sigilosa não
            # participa do canal que fala com o autor (Seção 3.1).
            email_pergunta = montar_pergunta_publica(
                saida, chamado, pendencias or None, rodada
            )
            await _enviar_pergunta_publica(conn, chamado_id, perfil_id, email_pergunta)
            await _registrar_historico(
                conn, chamado_id, perfil_id, rodada, passe_a_nome, acao, settings,
                em_sombra=em_sombra,
            )
        elif not eh_quimico:
            await _salvar_nota_interna(
                conn,
                chamado_id,
                perfil_id,
                montar_nota(saida, chamado, semelhantes, reclassificacao),
            )
            await _registrar_historico(
                conn, chamado_id, perfil_id, rodada, passe_a_nome, acao, settings,
                em_sombra=em_sombra,
            )
        # eh_quimico e acao == NOTA_INTERNA: nota/histórico ficam para depois
        # do Passe B (item 5) — a pré-análise técnica SUBSTITUI a do Passe A
        # na nota final (C2), então não grava duas vezes.

    if not (eh_quimico and acao == "NOTA_INTERNA"):
        # 4) E-mail da pergunta (fora da transação — a mensagem já está visível).
        #    Remetente = perfil IA (≠ autor) ⇒ o destinatário resolvido é o AUTOR,
        #    com Reply-To inbound (a resposta por e-mail reagenda a triagem).
        if email_pergunta is not None:
            try:
                await notificar_nova_mensagem_email(chamado, str(perfil_id), email_pergunta)
            except Exception as exc:  # noqa: BLE001 — e-mail nunca derruba a triagem
                log.warning("[IA TRIAGEM] Falha ao notificar pergunta por e-mail: %s", exc)
        log.info(
            "[IA TRIAGEM] Chamado %s triado (rodada %s, %s ms, anexos processados=%s ignorados=%s).",
            chamado_id, rodada, duracao_ms,
            resultado_anexos.processados, resultado_anexos.ignorados,
        )
        return

    # 5) Passe B do Químico (F4) — recuperação seletiva da base sigilosa
    # (produto citado, SEM quantidades — C7) + pré-análise técnica (metodologia
    # 6M). Só roda quando o A decidiu NOTA_INTERNA (nunca no ciclo de perguntas).
    dados_formulario = chamado.get("dados_formulario") or {}
    produto_form = dados_formulario.get("produto") if isinstance(dados_formulario, dict) else None
    texto_livre = f"{chamado.get('titulo') or ''} {chamado.get('descricao') or ''}"
    contexto_b = await contexto_quimico.montar_contexto_passe_b(settings, produto_form, texto_livre)
    modelo_passe_b = settings.ia_triagem_model_passe_b or settings.ia_triagem_model
    inicio_b = time.monotonic()
    saida_b, erro_b, tokens_in_b, tokens_out_b = await _chamar_modelo(
        montar_mensagens_passe_b(chamado, contexto_b, resultado_anexos),
        settings,
        model=modelo_passe_b,
        schema=SaidaPasseB,
    )
    duracao_ms_b = int((time.monotonic() - inicio_b) * 1000)

    # 6) Persistência do Passe B: sucesso → nota interna com a pré-análise
    # técnica (SUBSTITUI a do Passe A); falha → degrada para a nota do Passe A
    # (Regra #5 — o Químico nunca fica sem nota nenhuma).
    if saida_b is not None:
        resultado_b: dict[str, Any] = saida_b.model_dump(exclude={"pre_analise"})
        acao_b = "NOTA_INTERNA"
        nota = montar_nota_quimico(saida_b, chamado, semelhantes, reclassificacao)
    else:
        resultado_b = {"erro": erro_b or "desconhecido"}
        acao_b = "ERRO"
        nota = montar_nota(saida, chamado, semelhantes, reclassificacao)
    async with admin_connection() as conn:
        triagem_id_b = await conn.fetchval(
            """
            INSERT INTO ia_triagens
              (chamado_id, rodada, passe, acao, resultado, modelo,
               tokens_entrada, tokens_saida, custo_usd, duracao_ms)
            VALUES ($1::uuid, $2, $3, $4, $5::jsonb, $6, $7, $8, $9, $10)
            ON CONFLICT (chamado_id, rodada, passe) DO NOTHING
            RETURNING id
            """,
            chamado_id,
            rodada,
            _PASSE_B,
            acao_b,
            json.dumps(resultado_b, ensure_ascii=False),
            modelo_passe_b,
            tokens_in_b,
            tokens_out_b,
            cliente.custo_usd(modelo_passe_b, tokens_in_b, tokens_out_b),
            duracao_ms_b,
        )
        if triagem_id_b is not None:
            # Sem corrida com outra execução: grava a nota (SEMPRE interna,
            # is_interna=true fixado em código — Seção 8.2) e o histórico.
            await _salvar_nota_interna(conn, chamado_id, perfil_id, nota)
            await _registrar_historico(
                conn, chamado_id, perfil_id, rodada, _PASSE_B, acao_b, settings,
                modelo=modelo_passe_b, em_sombra=em_sombra,
            )

    log.info(
        "[IA TRIAGEM] Chamado %s triado (rodada %s, %s ms + %s ms Químico, "
        "anexos processados=%s ignorados=%s).",
        chamado_id,
        rodada,
        duracao_ms,
        duracao_ms_b,
        resultado_anexos.processados,
        resultado_anexos.ignorados,
    )


# ------------------------------------------------------------------
# Reconciliação (rede de segurança do agendamento em memória)
# ------------------------------------------------------------------


# Idade mínima do chamado (ou da resposta do autor) para a varredura considerá-lo
# órfão. Existe só para não competir com o `create_task` recém-agendado que ainda
# está no ar — o caminho normal termina em ~4 s (mediana real de produção, 19
# triagens entre 2026-07-23 e 2026-07-29), então 1 min já é 15× a duração
# esperada. Era 3 min até 2026-07-29: somado ao intervalo de varredura, punha o
# pior caso de recuperação em ~8 min, longe da meta p95 < 2 min (Seção 9) — foi
# o que se viu no BOND-2026-00629 (nota interna 4min40s após a abertura, com o
# modelo levando 3,8 s). Encurtar não arrisca duplicar nada: o motor revalida
# tudo no banco e o UNIQUE `(chamado_id, rodada, passe)` + `ON CONFLICT DO
# NOTHING` fazem a segunda execução sair sem gravar nem duplicar mensagem.
_MARGEM_ORFAO = timedelta(minutes=1)


async def _chamados_orfaos(conn: Any, settings: Settings) -> list[str]:
    """IDs de chamados com uma rodada de triagem "presa" (nunca rodou).

    ``agendar_triagem`` dispara via ``asyncio.create_task`` — rápido (latência
    p95 < 2 min), mas NÃO durável: um restart/redeploy do processo entre o
    agendamento e o primeiro INSERT em ``ia_triagens`` apaga a tarefa sem
    deixar rastro algum (caso real: BOND-2026-00593, 2026-07-23 — chamado de
    TI elegível com ZERO linhas em ``ia_triagens``/``historico_chamados``,
    enquanto o chamado imediatamente anterior e o seguinte foram triados
    normalmente em poucos segundos). Duas formas de "órfão", espelhando as
    mesmas guardas de :func:`_executar`:

    1. rodada 1 nunca rodou (chamado criado há mais de :data:`_MARGEM_ORFAO`,
       nenhuma linha em ``ia_triagens``);
    2. a IA perguntou (rodada N), o autor respondeu há mais de
       :data:`_MARGEM_ORFAO`, e a rodada N+1 nunca rodou (mesma guarda de
       re-triagem: ``NOVO`` + sem operador + dentro do teto de rodadas).

    A margem evita corrida com o ``create_task`` recém-agendado de um chamado
    que acabou de abrir ou responder.
    """
    departamentos = settings.ia_triagem_departamentos_lista
    if not departamentos:
        return []
    linhas = await conn.fetch(
        """
        SELECT c.id::text AS id
          FROM chamados c
          JOIN departamentos d ON d.id = c.departamento_id
         WHERE d.nome = ANY($1::text[])
           AND c.status <> 'RESOLVIDO'
           AND c.created_at < now() - $3::interval
           AND NOT EXISTS (SELECT 1 FROM ia_triagens t WHERE t.chamado_id = c.id)
        UNION
        SELECT c.id::text AS id
          FROM chamados c
          JOIN departamentos d ON d.id = c.departamento_id
          JOIN LATERAL (
                 SELECT t.rodada, t.acao, t.created_at
                   FROM ia_triagens t
                  WHERE t.chamado_id = c.id
                  ORDER BY t.rodada DESC, t.id DESC
                  LIMIT 1
               ) ultima ON true
         WHERE d.nome = ANY($1::text[])
           AND c.status = 'NOVO' AND c.operador_id IS NULL
           AND ultima.acao = 'PERGUNTAS'
           AND ultima.rodada < $2
           AND EXISTS (
                 SELECT 1 FROM mensagens m
                  WHERE m.chamado_id = c.id AND m.remetente_id = c.cliente_id
                    AND m.is_interna = false AND m.created_at > ultima.created_at
                    AND m.created_at < now() - $3::interval
               )
        """,
        departamentos,
        settings.ia_triagem_max_rodadas,
        _MARGEM_ORFAO,
    )
    return [r["id"] for r in linhas]


async def reconciliar_triagens_perdidas() -> int:
    """Varredura periódica: reexecuta a triagem dos chamados órfãos.

    Segura por construção — ``executar_triagem`` reavalida tudo no banco e é
    idempotente (Seção 2.3); processar de novo um chamado que já foi
    resolvido por outra via (corrida com o agendamento original) é um no-op
    barato (uma consulta, sem chamar o modelo). Nunca lança — mesma tolerância
    a falha do motor (Regra #5). Devolve quantos chamados foram reprocessados
    (só para log/observação)."""
    settings = get_settings()
    if not settings.ia_triagem_ativa or not settings.ia_triagem_api_key:
        return 0
    async with admin_connection() as conn:
        ids = await _chamados_orfaos(conn, settings)
    for chamado_id in ids:
        await executar_triagem(chamado_id)
    if ids:
        log.warning(
            "[IA TRIAGEM] Reconciliação reexecutou %d chamado(s) órfão(s): %s",
            len(ids),
            ", ".join(ids),
        )
    return len(ids)


async def _loop_reconciliacao(intervalo_s: float) -> None:
    """Roda :func:`reconciliar_triagens_perdidas` a cada ``intervalo_s``, até
    a task ser cancelada no shutdown do app (Regra #5: nunca derruba nada)."""
    while True:
        await asyncio.sleep(intervalo_s)
        try:
            await reconciliar_triagens_perdidas()
        except Exception as exc:  # noqa: BLE001 — loop de fundo nunca morre por erro de uma volta
            log.warning("[IA TRIAGEM] Ciclo de reconciliação falhou: %s", exc)


def iniciar_reconciliacao(settings: Settings) -> asyncio.Task | None:
    """Inicia o loop de reconciliação (chamado pelo lifespan do app).

    ``None`` se a triagem estiver desligada ou o intervalo for ``<= 0`` — a
    rede de segurança segue o mesmo kill switch geral (Regra de Ouro #5): sem
    ``IA_TRIAGEM_ATIVA``, nem a varredura roda."""
    if not settings.ia_triagem_ativa or settings.ia_triagem_reconciliacao_intervalo_s <= 0:
        return None
    return asyncio.create_task(_loop_reconciliacao(settings.ia_triagem_reconciliacao_intervalo_s))
