"""Simulador adversarial do intake de chamados via WhatsApp (uso manual).

Roda conversas COMPLETAS contra o modelo REAL (mesma `processar_conversa` da
produção), mas com banco em memória e envio de WhatsApp desligado — nada toca
produção. O "representante" do outro lado é outra chamada ao modelo, com
persona de pessoa leiga em tecnologia e vocabulário (erra nome, escreve tudo
minúsculo, responde vago, muda de ideia no meio).

Cada rodada é checada contra INVARIANTES (ver `_checar_invariantes`) — a
saída lista toda violação com a conversa/rodada em que apareceu, para virar
teste de regressão em `tests/test_whatsapp_intake*.py` depois.

Uso:
    python scripts/simular_conversas_whatsapp.py --cenarios 20 --max-rodadas 14
    python scripts/simular_conversas_whatsapp.py --so-cenario 3 --verbose

CUSTO: cada rodada faz 2+ chamadas reais ao modelo (bot + persona). ~20
cenários de ~10 rodadas custou ~US$ 3-5 em gpt-5.4-mini nos testes iniciais.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import sys
from contextlib import ExitStack, asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

from dotenv import load_dotenv
from pydantic import BaseModel

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

load_dotenv()

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

os.environ.setdefault("SESSION_SECRET", "simulacao-nao-producao-secret-key")
os.environ.setdefault("CSRF_SECRET", "simulacao-nao-producao-csrf-key")

from app.config import Settings  # noqa: E402
from app.domain.formularios_quimico import (  # noqa: E402
    CAMPOS_POR_CATEGORIA,
    campos_da_categoria,
    validar_payload,
)
from app.ia import whatsapp_intake  # noqa: E402
from app.ia.chamada_estruturada import chamar_modelo_estruturado  # noqa: E402

CAT_OCORRENCIA = "Registro de Ocorrência"
CAT_VISITA = "Solicitação de Visita Técnica"
CAT_ANALISE = "Solicitação de Análise Laboratorial"
CAT_DESENVOLVIMENTO = "Solicitação de Desenvolvimento"

_PERFIL = {
    "id": "perfil-uuid-simulado",
    "nome": "Representante Teste",
    "empresa_id": "empresa-uuid-simulada",
    "departamento_id": "departamento-uuid-simulado",
    "departamento_nome": "Comercial",
}
_TELEFONE = "5551999990000"
_SETORES = ["TI", "Comercial", "Produção", "RH", "Marketing", "Dpto Químico", "Logística"]
_CATALOGO_QUIMICO = [
    {
        "id": "dep-quimico-uuid",
        "nome": "Dpto Químico",
        "categorias": [
            {"id": f"cat-{i}", "nome": nome, "subcategorias": []}
            for i, nome in enumerate(CAMPOS_POR_CATEGORIA)
        ],
    }
]


class RespostaPersona(BaseModel):
    """Saída da persona que simula o representante."""

    model_config = {"extra": "ignore"}
    mensagem: str


@dataclass
class Cenario:
    nome: str
    # O que a pessoa REALMENTE quer (verdade de referência para conferir o
    # chamado no fim) — em linguagem natural, como ela pensaria.
    intencao: str
    # Como ela se comunica: a fonte do "adversarial" realista.
    estilo: str
    categoria_esperada: str | None = None
    # Espera-se chamado criado ao fim? None = tanto faz (cenário exploratório).
    espera_chamado: bool | None = None


@dataclass
class Violacao:
    cenario: str
    rodada: int
    regra: str
    detalhe: str


@dataclass
class ConversaSim:
    """Estado da conversa, no lugar das tabelas do Postgres."""

    conversa_id: str = "conversa-simulada-uuid"
    rodada: int = 0
    status: str = "COLETANDO"
    mensagens: list[dict[str, Any]] = field(default_factory=list)
    auditorias: list[dict[str, Any]] = field(default_factory=list)
    chamado_id: str | None = None
    enviadas: list[str] = field(default_factory=list)
    chamado_criado: dict[str, Any] | None = None

    @property
    def ultimo_resultado(self) -> dict[str, Any] | None:
        return self.auditorias[-1]["resultado"] if self.auditorias else None


class SimConn:
    """Conexão fake multi-rodada — fiel aos SQLs que `processar_conversa` usa."""

    def __init__(self, sim: ConversaSim):
        self.sim = sim

    async def fetchval(self, sql: str, *args):
        if "INSERT INTO ia_whatsapp_intake" in sql:
            self.sim.auditorias.append(
                {
                    "rodada": args[1],
                    "acao": args[2],
                    "resultado": json.loads(args[3]),
                    "modelo": args[4],
                    "tokens_in": args[5],
                    "tokens_out": args[6],
                    "custo": args[7],
                }
            )
            return len(self.sim.auditorias)
        if "INSERT INTO whatsapp_conversas" in sql:
            return self.sim.conversa_id
        if "SELECT id::text FROM whatsapp_conversas" in sql:
            return self.sim.conversa_id
        if "INSERT INTO whatsapp_mensagens_recebidas" in sql:
            return 1
        raise AssertionError(f"fetchval inesperado: {sql[:120]}")

    async def fetchrow(self, sql: str, *args):
        if "UPDATE whatsapp_conversas" in sql and "PROCESSANDO" in sql:
            if self.sim.status != "COLETANDO":
                return None
            self.sim.status = "PROCESSANDO"
            return {
                "id": self.sim.conversa_id,
                "perfil_id": _PERFIL["id"],
                "telefone": _TELEFONE,
                "rodada": self.sim.rodada,
                "mensagens_acumuladas": json.dumps(self.sim.mensagens, ensure_ascii=False),
            }
        if "FROM perfis p" in sql:
            return dict(_PERFIL)
        if "FROM whatsapp_conversas wc" in sql:
            return None
        if "SELECT resultado FROM ia_whatsapp_intake" in sql:
            if not self.sim.auditorias:
                return None
            return {
                "resultado": json.dumps(self.sim.ultimo_resultado, ensure_ascii=False)
            }
        raise AssertionError(f"fetchrow inesperado: {sql[:120]}")

    async def fetch(self, sql: str, *args):
        return []

    async def execute(self, sql: str, *args):
        if "UPDATE whatsapp_conversas" in sql and "mensagens_acumuladas" in sql:
            self.sim.status = args[1]
            self.sim.rodada = args[2]
            if args[3]:
                self.sim.chamado_id = args[3]
            self.sim.mensagens.extend(json.loads(args[4]))
            return
        if "UPDATE whatsapp_conversas SET status" in sql:
            self.sim.status = args[1] if len(args) > 1 else "FALHOU"
            return


def _settings() -> Settings:
    return Settings(
        session_secret="simulacao-nao-producao-secret-key",
        csrf_secret="simulacao-nao-producao-csrf-key",
        whatsapp_intake_ativo=True,
        whatsapp_intake_departamentos="Dpto Químico",
        whatsapp_app_secret="",
        whatsapp_intake_max_rodadas=30,
    )


@asynccontextmanager
async def _ambiente_simulado(sim: ConversaSim):
    """Mesmo patching dos testes, MENOS `chamar_modelo_estruturado` — aqui o
    modelo é o REAL, que é justamente o que se quer exercitar."""
    conn = SimConn(sim)

    @asynccontextmanager
    async def _fake_admin():
        yield conn

    async def _responder(telefone: str, texto: str) -> None:
        sim.enviadas.append(texto)

    async def _criar(**kwargs):
        sim.chamado_criado = kwargs
        return {"id": "chamado-simulado-uuid", "codigo": "BOND-2026-SIMUL"}

    repo = AsyncMock()
    repo.criar = AsyncMock(side_effect=_criar)
    repo.operadores = AsyncMock(return_value=[])
    repo.adicionar_mensagem = AsyncMock()

    with ExitStack() as stack:
        for p in (
            patch.object(whatsapp_intake, "admin_connection", _fake_admin),
            patch.object(whatsapp_intake, "get_settings", return_value=_settings()),
            patch.object(
                whatsapp_intake, "resolver_perfil_por_telefone", AsyncMock(return_value=_PERFIL)
            ),
            patch.object(whatsapp_intake, "_perfil_por_id", AsyncMock(return_value=_PERFIL)),
            patch.object(
                whatsapp_intake, "_montar_catalogo", AsyncMock(return_value=_CATALOGO_QUIMICO)
            ),
            patch.object(whatsapp_intake, "_setores_validos", AsyncMock(return_value=_SETORES)),
            patch.object(whatsapp_intake, "_responder", AsyncMock(side_effect=_responder)),
            patch.object(whatsapp_intake, "_responder_documento", AsyncMock()),
            patch.object(whatsapp_intake, "_imagem_da_conversa", AsyncMock(return_value=None)),
            patch.object(whatsapp_intake, "_anexar_midia_da_conversa", AsyncMock()),
            patch.object(whatsapp_intake, "_pos_criacao", AsyncMock()),
            patch("app.repositories.chamados.ChamadosRepo", return_value=repo),
        ):
            stack.enter_context(p)
        yield


_PROMPT_PERSONA = """\
Você está SIMULANDO um representante comercial da Bondmann Química conversando \
por WhatsApp com um bot que abre chamados. Você NÃO é o bot — você é o cliente \
dele, e responde como uma pessoa real responderia.

PERFIL DESTA PESSOA:
{intencao}

COMO ELA ESCREVE (siga à risca, é o ponto do teste):
{estilo}

REGRAS DA SIMULAÇÃO:
- Responda APENAS com a próxima mensagem que essa pessoa mandaria. Uma mensagem \
curta de WhatsApp, nada de narração ou explicação.
- Ela é LEIGA: não sabe os nomes técnicos exatos, não sabe que existe uma lista \
fechada de opções, não entende jargão de sistema.
- Se o bot pedir algo que ela não sabe de cabeça, ela responde do jeito dela \
(aproximado, incompleto, ou pergunta de volta) — nunca inventa um código exato \
de sistema que ela não teria.
- Se o bot apresentar uma lista numerada, ela PODE responder pelo número.
- Se o bot repetir a mesma coisa, ela fica confusa ou impaciente, como uma \
pessoa real ficaria.
- Nunca saia do personagem, nunca comente que isso é um teste.

FORMATO DA RESPOSTA: responda em json, com exatamente a chave "mensagem" \
contendo o texto que a pessoa mandaria. Exemplo: {{"mensagem": "oi, bom dia"}}
"""


async def _resposta_persona(cenario: Cenario, historico: list[dict[str, Any]]) -> str:
    st = _settings()
    linhas = []
    for m in historico[-12:]:
        quem = "BOT" if m.get("papel") != "usuario" else "VOCÊ"
        linhas.append(f"[{quem}] {m.get('conteudo')}")
    mensagens = [
        {
            "role": "system",
            "content": _PROMPT_PERSONA.format(
                intencao=cenario.intencao, estilo=cenario.estilo
            ),
        },
        {
            "role": "user",
            "content": (
                "Conversa até agora:\n" + "\n".join(linhas)
                + "\n\nQual é a sua próxima mensagem?"
            ),
        },
    ]
    saida, erro, _ti, _to = await chamar_modelo_estruturado(
        mensagens,
        model=st.whatsapp_intake_model,
        api_key=st.ia_triagem_api_key,
        base_url=st.ia_triagem_base_url,
        timeout_s=st.whatsapp_intake_timeout_s,
        max_tokens=300,
        schema=RespostaPersona,
    )
    if saida is None:
        raise RuntimeError(f"persona sem saída: {erro}")
    return saida.mensagem.strip()


def _checar_invariantes(
    cenario: Cenario, sim: ConversaSim, rodada: int, violacoes: list[Violacao]
) -> None:
    """As regras que o bot NUNCA pode quebrar, checadas a cada rodada."""

    def viola(regra: str, detalhe: str) -> None:
        violacoes.append(Violacao(cenario.nome, rodada, regra, detalhe))

    enviadas = sim.enviadas
    if not enviadas:
        return
    ultima = enviadas[-1]

    # 1. Nunca manda a MESMA mensagem duas vezes na mesma conversa.
    if enviadas.count(ultima) > 1:
        viola("mensagem-repetida", f"mandou 2x: {ultima[:120]!r}")

    # 2. Nunca despeja lista gigante no chat.
    if len(ultima) > 700:
        viola("mensagem-gigante", f"{len(ultima)} chars: {ultima[:120]!r}")

    # 3. Nunca ecoa a última mensagem do usuário como se fosse pergunta.
    ultimo_usuario = next(
        (m["conteudo"] for m in reversed(sim.mensagens) if m.get("papel") == "usuario"), ""
    )
    if ultimo_usuario and ultima.strip().casefold() == str(ultimo_usuario).strip().casefold():
        viola("eco-do-usuario", f"ecoou {ultima[:80]!r}")

    if not sim.auditorias:
        return
    ult = sim.auditorias[-1]
    res = ult["resultado"]
    campos = res.get("campos_formulario") or {}
    categoria = str(res.get("categoria") or "")

    # 4. Nunca grava chave que não é campo real da categoria.
    if categoria and campos:
        validos = {c.name for c in campos_da_categoria(categoria)}
        if validos:
            for chave in campos:
                if chave not in validos:
                    viola("campo-inventado", f"{chave!r} não existe em {categoria!r}")

    # 5. Nunca grava valor de select fora da lista real.
    if categoria:
        for c in campos_da_categoria(categoria):
            valor = campos.get(c.name)
            if not c.opcoes or valor in (None, "", []):
                continue
            if c.tipo == "select" and isinstance(valor, str) and valor not in c.opcoes:
                viola("select-invalido", f"{c.name}={valor!r} fora da lista")
            if c.tipo == "checkbox_multi" and isinstance(valor, list):
                for v in valor:
                    if v not in c.opcoes:
                        viola("checkbox-invalido", f"{c.name} contém {v!r} fora da lista")

    # 6. Dado já capturado nunca regride para vazio.
    if len(sim.auditorias) >= 2:
        antes = sim.auditorias[-2]["resultado"].get("campos_formulario") or {}
        for chave, valor in antes.items():
            if valor in (None, "", []):
                continue
            if campos.get(chave) in (None, "", []):
                viola("dado-perdido", f"{chave}={valor!r} sumiu na rodada seguinte")
        for chave in ("setor", "departamento", "categoria"):
            se_antes = sim.auditorias[-2]["resultado"].get(chave)
            if se_antes and not res.get(chave):
                viola("dado-perdido", f"{chave}={se_antes!r} sumiu na rodada seguinte")

    # 7. Auditoria tem que refletir a mensagem realmente enviada.
    if ult["acao"] == "PERGUNTA":
        registradas = res.get("perguntas") or []
        if registradas and ultima not in registradas and len(registradas) == 1:
            viola(
                "auditoria-divergente",
                f"gravou {str(registradas[0])[:60]!r}, enviou {ultima[:60]!r}",
            )

    # 8. Chamado criado só com formulário válido pela MESMA validação do Portal.
    if sim.chamado_criado is not None:
        dados = sim.chamado_criado.get("dados_formulario")
        if categoria and campos_da_categoria(categoria) and dados is not None:
            ok, erro_val, _limpo = validar_payload(categoria, dados)
            if not ok or erro_val:
                viola("chamado-invalido", f"criado com formulário inválido: {erro_val}")
        if not (sim.chamado_criado.get("setor") or res.get("setor")):
            viola("chamado-sem-setor", "chamado criado sem setor do solicitante")


async def _rodar_cenario(
    cenario: Cenario, max_rodadas: int, verbose: bool
) -> tuple[ConversaSim, list[Violacao]]:
    sim = ConversaSim()
    violacoes: list[Violacao] = []

    async with _ambiente_simulado(sim):
        # Primeira mensagem: sempre um "oi" espontâneo da pessoa.
        primeira = await _resposta_persona(cenario, [])
        sim.mensagens.append({"papel": "usuario", "conteudo": primeira})
        if verbose:
            print(f"    [USUÁRIO] {primeira}")

        for rodada in range(1, max_rodadas + 1):
            sim.status = "COLETANDO"
            antes = len(sim.enviadas)
            await whatsapp_intake.processar_conversa(sim.conversa_id)
            if len(sim.enviadas) == antes:
                if verbose:
                    print("    [BOT] (sem resposta — conversa encerrada)")
                break
            if verbose:
                print(f"    [BOT] {sim.enviadas[-1][:300]}")
            _checar_invariantes(cenario, sim, rodada, violacoes)

            if sim.chamado_criado is not None or sim.status in ("CONCLUIDA", "FALHOU"):
                break

            resposta = await _resposta_persona(cenario, sim.mensagens)
            sim.mensagens.append({"papel": "usuario", "conteudo": resposta})
            if verbose:
                print(f"    [USUÁRIO] {resposta}")

    # Checagens de fim de conversa.
    if cenario.espera_chamado is True and sim.chamado_criado is None:
        violacoes.append(
            Violacao(cenario.nome, sim.rodada, "chamado-nao-criado",
                     "conversa terminou sem abrir chamado, mas deveria abrir")
        )
    if cenario.espera_chamado is False and sim.chamado_criado is not None:
        violacoes.append(
            Violacao(cenario.nome, sim.rodada, "chamado-indevido",
                     "abriu chamado numa conversa que não deveria abrir")
        )
    if (
        cenario.categoria_esperada
        and sim.chamado_criado is not None
        and sim.auditorias
        and str(sim.ultimo_resultado.get("categoria") or "") != cenario.categoria_esperada
    ):
        violacoes.append(
            Violacao(cenario.nome, sim.rodada, "categoria-errada",
                     f"esperava {cenario.categoria_esperada!r}, "
                     f"veio {sim.ultimo_resultado.get('categoria')!r}")
        )
    return sim, violacoes


def _cenarios() -> list[Cenario]:
    """Cada cenário é um jeito diferente de uma pessoa leiga errar ou acertar."""
    return [
        Cenario(
            "01-caminho-feliz-lento",
            "Você é do setor de Produção. Um cliente reclamou que o produto DEGRAX 25 "
            "manchou uma peça de alumínio. Você quer registrar essa ocorrência. Cliente: "
            "Metalúrgica Silva, cidade Canoas, contato João, cargo gerente de manutenção, "
            "telefone 51999998888, email joao@metalsilva.com.br, lote 1234567890123.",
            "Escreve tudo minúsculo, sem acento, frases curtas. Responde uma coisa por vez, "
            "sempre certo mas devagar. Nunca dá dois dados na mesma mensagem.",
            CAT_OCORRENCIA, True,
        ),
        Cenario(
            "02-nome-supervisor-errado",
            "Você é do Comercial e quer registrar uma ocorrência do produto BRIL na região "
            "de Caxias do Sul. Cliente: Vidros Bento, cidade Bento Gonçalves.",
            "Quando pedirem supervisor, você chuta nomes que NÃO existem: 'roberto', "
            "'seu carlinhos', 'a menina do financeiro'. Só depois de 3 tentativas você diz "
            "'nao lembro, tem uma lista?'. Escreve errado e usa gíria.",
            CAT_OCORRENCIA, None,
        ),
        Cenario(
            "03-so-diz-que-quer-chamado",
            "Você quer abrir um chamado pro pessoal da química mas não explica o que é. "
            "Você é do setor de Logística. Só depois de MUITA insistência você conta que "
            "é uma análise de laboratório que você precisa.",
            "Responde curtíssimo: 'preciso de um chamado', 'pro quimico', 'e isso'. "
            "Evasivo, nunca dá detalhe de primeira.",
            CAT_ANALISE, None,
        ),
        Cenario(
            "04-confunde-setor-com-destino",
            "Você é do setor de TI mas fala de um jeito que confunde: você diz que quer "
            "abrir chamado 'para o setor do departamento químico'. Se perguntarem qual é o "
            "SEU setor, você responde TI.",
            "Usa a palavra 'setor' pra falar do DESTINO, não de você. Escreve sem acento, "
            "com erro de digitação.",
            None, None,
        ),
        Cenario(
            "05-muda-de-ideia-no-meio",
            "Você começa querendo uma visita técnica, mas na terceira ou quarta resposta "
            "muda de ideia e diz que na verdade quer registrar uma ocorrência de produto. "
            "Você é do Comercial.",
            "No meio da conversa você diz 'na verdade nao', 'esquece', 'quero outra coisa'. "
            "Escreve informal.",
            None, None,
        ),
        Cenario(
            "06-responde-por-numero",
            "Você é do setor de Produção e quer registrar uma ocorrência. Sempre que o bot "
            "mostrar uma lista numerada, você responde SÓ o número.",
            "Respostas de uma palavra ou só um número. Muito seco. 'sim', 'nao', '2'.",
            CAT_OCORRENCIA, None,
        ),
        Cenario(
            "07-tudo-de-uma-vez",
            "Você é do Comercial e despeja TUDO na primeira mensagem: quer registrar "
            "ocorrência, produto BRIL, cliente Alumínios Garden em Canoas, região Canoas, "
            "contato Maria, cargo compradora, fone 5133334444, email maria@garden.com.br.",
            "Manda um textão corrido, sem pontuação, tudo grudado numa mensagem só. "
            "Depois responde monossílabos.",
            CAT_OCORRENCIA, None,
        ),
        Cenario(
            "08-pergunta-de-volta",
            "Você é do RH (setor errado pra esse fluxo, mas é o seu setor) e quer entender "
            "o que o bot faz antes de pedir qualquer coisa. Depois pede uma análise "
            "laboratorial.",
            "Pergunta de volta o tempo todo: 'como assim?', 'que que é isso?', 'pra que "
            "voce quer isso?'. Desconfiado, leigo.",
            None, None,
        ),
        Cenario(
            "09-desenvolvimento-de-produto",
            "Você é do Comercial e quer pedir o desenvolvimento de um produto novo: um "
            "desengraxante que não agrida alumínio, pro mercado de autopeças. Concorrência: "
            "produtos importados caros. Diferencial: preço e não corroer.",
            "Escreve razoavelmente bem mas é prolixo, conta história antes de responder. "
            "Às vezes responde só parte da pergunta.",
            CAT_DESENVOLVIMENTO, None,
        ),
        Cenario(
            "10-analise-multipla-escolha",
            "Você é da Produção e precisa de uma análise laboratorial de uma amostra de "
            "óleo. Você quer VÁRIAS análises: as duas primeiras da lista que aparecer.",
            "Responde 'as duas primeiras', '1 e 2', 'quero aquelas duas ali'. Vago sobre "
            "números mas insistente.",
            CAT_ANALISE, None,
        ),
        Cenario(
            "11-fora-de-escopo",
            "Você quer abrir um chamado pro RH sobre o seu vale-transporte que não veio. "
            "Você é do setor de Produção. Isso NÃO é do Dpto Químico.",
            "Simples e direto, mas insiste um pouco quando o bot diz que não dá.",
            None, False,
        ),
        Cenario(
            "12-regiao-por-cidade",
            "Você é do Comercial, quer registrar ocorrência. A região do cliente é Sumaré, "
            "mas você só sabe dizer o nome da cidade, nunca o código.",
            "Diz só 'sumare', 'é em sumare mesmo', nunca com código numérico. Escreve sem "
            "acento e tudo minúsculo.",
            CAT_OCORRENCIA, None,
        ),
        Cenario(
            "13-lote-curto",
            "Você é da Produção, registrando ocorrência do produto CONCRET. Quando pedirem "
            "o lote, você primeiro dá um número curto ('123'), depois o completo "
            "('1234567890123').",
            "Dá informação incompleta primeiro, corrige depois quando insistem.",
            CAT_OCORRENCIA, None,
        ),
        Cenario(
            "14-email-invalido",
            "Você é do Comercial, registrando ocorrência. Quando pedirem email do contato "
            "você dá um inválido primeiro ('joao arroba empresa'), depois o certo "
            "('joao@empresa.com.br').",
            "Escreve email por extenso ('arroba'), não entende formato. Leiga de verdade.",
            CAT_OCORRENCIA, None,
        ),
        Cenario(
            "15-silencio-e-monossilabos",
            "Você é da Produção e quer registrar uma ocorrência mas está com pressa e "
            "responde o mínimo possível. Você só quer que acabe logo.",
            "Responde 'sim', 'ok', 'aham', 'ta', 'nao sei' na maioria das vezes. Só dá "
            "informação real se o bot insistir de um jeito bem específico.",
            None, None,
        ),
        Cenario(
            "16-mistura-gerente-supervisor",
            "Você é do Comercial. Você sabe que Bruno Tiara da Silva é GERENTE, mas quando "
            "pedirem SUPERVISOR você responde 'bruno tiara' mesmo assim, porque pra você "
            "tanto faz.",
            "Confunde os cargos o tempo todo. Só depois de o bot explicar direito é que "
            "você diz 'ah entao nao sei o supervisor'.",
            CAT_OCORRENCIA, None,
        ),
        Cenario(
            "17-visita-tecnica-completa",
            "Você é do Comercial e quer agendar uma visita técnica num cliente em "
            "Florianópolis. É a primeira ocorrência lá. Cliente: Náutica Sul, contato Pedro.",
            "Escreve razoável, responde certo, mas às vezes com o nome da cidade em vez do "
            "código da região.",
            CAT_VISITA, None,
        ),
        Cenario(
            "18-repete-a-mesma-coisa",
            "Você é da Produção e quer registrar uma ocorrência de vazamento. Você repete "
            "quase a mesma frase toda vez que responde, sem acrescentar informação nova.",
            "Repete 'é um vazamento no produto', 'o produto vazou', 'deu vazamento' — "
            "variações da mesma coisa, sem responder o que foi perguntado.",
            None, None,
        ),
        Cenario(
            "19-cancela-no-meio",
            "Você é do Comercial, começa a registrar uma ocorrência e no meio desiste: "
            "'deixa pra la', 'esquece'. Depois volta atrás e quer continuar.",
            "Impaciente, desiste e volta. Escreve curto e informal.",
            None, None,
        ),
        Cenario(
            "20-nome-empresa-no-contato",
            "Você é do Comercial, registrando ocorrência pro cliente 'Alumínios Garden "
            "LTDA'. Quando pedirem o nome do CONTATO da empresa, você repete o nome da "
            "EMPRESA por engano, e só depois dá o nome da pessoa (Márcia).",
            "Confunde empresa com pessoa. Escreve tudo minúsculo, sem acento.",
            CAT_OCORRENCIA, None,
        ),
    ]


async def main(qtd: int, max_rodadas: int, verbose: bool, so_cenario: int | None) -> int:
    todos = _cenarios()
    if so_cenario is not None:
        todos = [c for c in todos if c.nome.startswith(f"{so_cenario:02d}-")]
    else:
        todos = todos[:qtd]

    print(f"Rodando {len(todos)} cenários (máx {max_rodadas} rodadas cada).")
    print("Banco em memória, WhatsApp desligado — NADA toca produção.\n")

    todas_violacoes: list[Violacao] = []
    resumo: list[tuple[str, int, int, bool]] = []
    custo_total = 0.0

    for cenario in todos:
        print(f"[{cenario.nome}]")
        try:
            sim, violacoes = await _rodar_cenario(cenario, max_rodadas, verbose)
        except Exception as exc:  # noqa: BLE001 — o simulador não pode morrer
            print(f"    !! EXCEÇÃO NÃO TRATADA: {type(exc).__name__}: {exc}")
            todas_violacoes.append(
                Violacao(cenario.nome, -1, "excecao", f"{type(exc).__name__}: {exc}")
            )
            resumo.append((cenario.nome, 0, 1, False))
            continue
        custo = sum(a.get("custo") or 0 for a in sim.auditorias)
        custo_total += float(custo)
        criou = sim.chamado_criado is not None
        marca = "OK " if not violacoes else "!! "
        print(
            f"    {marca}{len(sim.auditorias)} rodadas, "
            f"chamado={'sim' if criou else 'nao'}, "
            f"{len(violacoes)} violações, US$ {float(custo):.4f}"
        )
        for v in violacoes:
            print(f"       - r{v.rodada} [{v.regra}] {v.detalhe}")
        todas_violacoes.extend(violacoes)
        resumo.append((cenario.nome, len(sim.auditorias), len(violacoes), criou))

    print("\n" + "=" * 72)
    print(f"RESUMO: {len(todos)} cenários, {len(todas_violacoes)} violações, "
          f"custo total US$ {custo_total:.4f}")
    por_regra: dict[str, int] = {}
    for v in todas_violacoes:
        por_regra[v.regra] = por_regra.get(v.regra, 0) + 1
    for regra, n in sorted(por_regra.items(), key=lambda kv: -kv[1]):
        print(f"  {n:3d}x {regra}")
    return 1 if todas_violacoes else 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--cenarios", type=int, default=20)
    parser.add_argument("--max-rodadas", type=int, default=14)
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--so-cenario", type=int, default=None)
    parser.add_argument("--seed", type=int, default=1234)
    args = parser.parse_args()
    random.seed(args.seed)
    sys.exit(asyncio.run(main(args.cenarios, args.max_rodadas, args.verbose, args.so_cenario)))
