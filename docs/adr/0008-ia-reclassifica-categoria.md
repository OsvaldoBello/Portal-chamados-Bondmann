# ADR-0008 — A IA de triagem reclassifica categoria/subcategoria automaticamente

**Status:** Aceito · **Data:** 2026-08-04 · **Ref.:** [`plano_md_mestre_IA.md`](../../plano_md_mestre_IA.md) Seções 0 (Regra de Ouro #3), 2.3, 2.5 e 8.2

## Contexto

Desde a F1, a triagem por IA **sugere** a categoria correta na nota interna
(`categoria_sugerida`) e a Regra de Ouro #3 do plano de IA era categórica: *"a
IA nunca muda categoria/prioridade/status sozinha (apenas sugere na nota
interna)"*.

A sugestão só vira correção se um atendente ler a nota e agir. Enquanto isso o
chamado fica na categoria errada — e categoria é o que orienta o roteamento e a
leitura das filas do Workspace. O gestor pediu (2026-08-04) que, **quando a
divergência for evidente**, a IA aplique a troca de categoria e subcategoria na
hora da análise, em vez de deixar a correção pendurada numa sugestão.

Isto é uma reversão parcial e deliberada de uma regra de ouro. O que não muda:
prioridade e status seguem sendo apenas sugestão, e a IA continua sem concluir
atendimento.

## Decisão

A triagem passa a poder escrever `chamados.categoria_id`/`subcategoria_id`, sob
um conjunto de guardas **estruturais** — o mesmo princípio da Seção 3.1 do plano
de IA (*proteção estrutural ≻ promessa de prompt*): o que impede o dano não é o
prompt pedir bom comportamento, é o motor não ter como fazer diferente.

| Guarda | Onde | O que impede |
|---|---|---|
| `IA_TRIAGEM_RECLASSIFICACAO_DEPARTAMENTOS` (CSV, default vazio) | `app/config.py` | Feature desligada por default; rollback é env, sem deploy. |
| `categoria_divergente=true` declarado pelo modelo | `app/ia/schemas.py` | Sugerir continua sendo o caminho da dúvida — `categoria_sugerida` sozinha não aplica nada. |
| Confiança ≥ `IA_TRIAGEM_RECLASSIFICACAO_CONFIANCA_MINIMA` (default `ALTA`; inválido degrada para `ALTA`) | `resolver_reclassificacao` | Troca por palpite. |
| Justificativa não vazia | `resolver_reclassificacao` | Troca sem registro do porquê — é o que o atendente lê na nota e no histórico. |
| Destino tem que existir no **catálogo ativo do departamento do próprio chamado** (casamento por nome normalizado, ids vêm do catálogo) | `resolver_reclassificacao` | Categoria alucinada; fuga para categoria de outro setor. O modelo devolve nome, nunca id. |
| `dados_formulario` preenchido ⇒ não reclassifica | `_aplicar_reclassificacao` | Órfãos de rótulo no Dpto Químico, cujo formulário dinâmico é indexado pelo **nome** da categoria (`app/domain/formularios_quimico.py`). |
| Nenhum `CATEGORIA_ALTERADA`/`IA_RECLASSIFICACAO` anterior no histórico | `_aplicar_reclassificacao` | A IA desfazer decisão de humano, ou brigar consigo mesma entre rodadas. |
| `UPDATE` com compare-and-swap contra a categoria analisada | `_aplicar_reclassificacao` | Sobrescrever uma troca feita por alguém durante a chamada de modelo. |
| Chamado com atendimento iniciado | `_executar` (estado fresco pós-modelo) | Reclassificar por cima de quem já está no caso. |

Auditoria: o que o modelo **propôs** fica em `ia_triagens.resultado` (campos
novos do schema); o que foi **aplicado** fica em `historico_chamados` com a ação
`IA_RECLASSIFICACAO` (de/para com ids e nomes, motivo, rodada, confiança),
assinada pelo perfil "Assistente IA". A nota interna sempre declara a troca.

Sem migration: `historico_chamados.acao` é `text` livre, e as colunas de
categoria/subcategoria já existem.

## Escopo: o Dpto Químico fica de fora nesta entrega

O prompt do Passe A do Químico **não** aprende os campos novos. Como
`categoria_divergente` tem default `false` no schema, o Químico não reclassifica
nada mesmo que alguém o inclua na env — e a guarda do `dados_formulario` barra
de novo, porque todo chamado do Químico nasce de formulário dinâmico.

Duas razões: (1) a categoria do Químico **é** a chave do layout do formulário, e
trocá-la orfanaria as respostas já gravadas; (2) alterar
`app/ia/prompts/quimico_*.md` dispara o gatilho permanente da Seção 8.3 (bateria
de red team comportamental contra o modelo real antes do merge), que exige chave
de produção e decisão do gestor. Incluir o Químico é uma entrega própria.

## Alternativas descartadas

| Alternativa | Por que não |
|---|---|
| Manter só a sugestão na nota (status quo) | É exatamente o que o gestor pediu para mudar: a correção depende de alguém ler a nota. |
| Aplicar sempre que `categoria_sugerida` divergir | O campo existe desde a F1 para dúvidas ("poderia ser outra"). Usá-lo como gatilho de escrita transformaria toda hesitação do modelo em mudança de fila. |
| Modelo devolver o `id` da categoria | Convida a alucinação de UUID e obriga a mandar ids no prompt. Nome + catálogo fechado é mais barato e falha fechado. |
| Fila de reclassificações para o staff aprovar | É a sugestão de hoje com mais passos — não resolve o atraso, e exige UI nova. |
| Governar pelo `IA_TRIAGEM_MODO_SOMBRA` | Sombra é sobre falar com o autor (mensagem pública/e-mail). Reclassificar é escrita interna; misturar as duas coisas tiraria o Químico da sombra junto, ou prenderia o TI. Lista própria. |

## Consequências

- **Risco assumido:** uma reclassificação errada tira o chamado da fila em que
  alguém o esperava. Mitigações: confiança ALTA por default, justificativa
  obrigatória na nota, histórico auditável, e a troca só acontece **uma vez** —
  o staff corrigir depois é definitivo (a IA não volta atrás).
- **Rollback:** esvaziar `IA_TRIAGEM_RECLASSIFICACAO_DEPARTAMENTOS` no Railway.
  Sem deploy, sem migration, sem perda das notas já geradas.
- **Observação inicial:** acompanhar `historico_chamados` com
  `acao='IA_RECLASSIFICACAO'` cruzado com `CATEGORIA_ALTERADA` posterior —
  reclassificação que o staff desfaz é o sinal de que o limiar está frouxo.
