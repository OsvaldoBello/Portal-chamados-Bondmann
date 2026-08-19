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
    # Confiança geral da análise — perguntas ao usuário (F2) exigem o limiar
    # `IA_TRIAGEM_PERGUNTAS_CONFIANCA_MINIMA` (default BAIXA desde 2026-07-24).
    confianca: Literal["ALTA", "MEDIA", "BAIXA"] = "MEDIA"
    # Pré-análise técnica para a nota interna (2–6 frases, pt-BR).
    pre_analise: str = Field(min_length=1)
    # Sugestões APENAS quando divergem do chamado. Prioridade e status seguem
    # sendo só sugestão (Regra #3); categoria/subcategoria podem ser APLICADAS
    # pelo motor quando `categoria_divergente` é true e todas as guardas da
    # F8 passam (`triagem.resolver_reclassificacao`).
    categoria_sugerida: str | None = None
    subcategoria_sugerida: str | None = None
    # A classificação atual está EVIDENTEMENTE errada? (F8, 2026-08-04.)
    # Default false: modelo/prompt que não conhece o campo nunca reclassifica —
    # é assim que o Químico fica fora da feature sem depender de env (o prompt
    # do Passe A não ensina estes campos; mudá-lo reabriria o red team, 8.3).
    categoria_divergente: bool = False
    # Por que a troca se justifica, citando o que no chamado a sustenta. Vazio
    # ⇒ nada é aplicado, mesmo com `categoria_divergente=true`: a justificativa
    # é o registro que o atendente lê na nota e no histórico.
    categoria_justificativa: str | None = None
    prioridade_sugerida: Literal["BAIXA", "MEDIA", "ALTA", "URGENTE"] | None = None
    # Perguntas que fariam a triagem avançar (máx. 3). Em modo sombra ficam
    # registradas na nota interna; na F2 viram mensagem pública.
    perguntas: list[str] = Field(default_factory=list, max_length=3)
    # Re-triagem (rodada > 1): perguntas da rodada ANTERIOR que o autor deixou
    # sem resposta alguma, copiadas literalmente. "Não sei"/"não tenho essa
    # informação" conta como RESPONDIDA — o autor não tem o dado. Lista vazia
    # fecha o ciclo de perguntas (`triagem.decidir_acao`): a rodada extra
    # existe só para cobrar o que ficou em branco, nunca para abrir uma
    # bateria nova de perguntas (BOND-2026-00653).
    perguntas_nao_respondidas: list[str] = Field(default_factory=list, max_length=3)
    # Termos para a busca de chamados semelhantes (F3 — FTS português).
    termos_busca: list[str] = Field(default_factory=list, max_length=8)


class SaidaWhatsAppIntake(BaseModel):
    """Saída da extração de intake de chamado via WhatsApp.

    Dado o histórico da conversa (+ imagem opcional) e o catálogo de
    departamento→categoria→subcategoria injetado no prompt, decide se há
    informação suficiente para abrir o chamado. Campos extras são ignorados
    (tolerância a divagação); campos obrigatórios ausentes invalidam o JSON.
    """

    model_config = {"extra": "ignore"}

    # Há dado suficiente (título, descrição, setor, departamento, categoria,
    # subcategoria, prioridade) para abrir o chamado sem voltar a perguntar?
    informacoes_suficientes: bool
    confianca: Literal["ALTA", "MEDIA", "BAIXA"] = "MEDIA"
    # De 1 a 3 perguntas quando informacoes_suficientes=False (decisão do
    # gestor 2026-08-18: o modelo escolhe quantas, mandando uma só quando falta
    # pouco e as 3 quando o relato é vago). Lista NÃO vazia é validada em
    # código, não pelo schema — o modelo às vezes omite mesmo quando deveria
    # vir. Mesmo teto de 3 do ciclo de perguntas da triagem do portal.
    perguntas: list[str] = Field(default_factory=list, max_length=3)
    titulo: str | None = None
    descricao: str | None = None
    # Setor DEMANDANTE (o do próprio autor), perguntado na conversa — nome
    # literal da lista de setores ativos injetada no prompt. Mesmo campo
    # obrigatório do formulário de abertura do portal.
    setor: str | None = None
    # Nomes LITERAIS do catálogo injetado no prompt — qualquer nome fora do
    # catálogo é tratado como alucinação pelo chamador (nunca vira INSERT).
    departamento: str | None = None
    categoria: str | None = None
    subcategoria: str | None = None
    # Prioridade derivada do relato (impacto × urgência). Ausente/inválida
    # degrada para MEDIA no chamador — nunca bloqueia a abertura.
    prioridade: Literal["BAIXA", "MEDIA", "ALTA", "URGENTE"] | None = None
    # Achado em produção (2026-08-19, teste real do gestor pelo setor
    # Brigadistas): com o piloto restrito a poucos departamentos, um relato
    # que não se encaixa em NENHUMA combinação do catálogo fornecido fazia o
    # modelo ficar perguntando sem fim, tentando achar informação que nunca
    # ia resolver — porque o destino simplesmente não existe no catálogo
    # atual. `true` diz "já entendi o relato, mas não há pra onde mandar";
    # `decidir_acao_intake` encerra a conversa nesse caso, mesmo com
    # `informacoes_suficientes: false` e rodada < teto.
    assunto_fora_do_escopo: bool = False


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
