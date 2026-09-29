# Contrato da API do worker — Automação de acessos (versão 1)

> Governado por [`plano_md_mestre_automacao_acessos.md`](../plano_md_mestre_automacao_acessos.md)
> (Seção 5). Este arquivo é o **único acoplamento** entre o portal e o worker
> (projeto `Automação/`): mudar qualquer campo aqui exige subir
> `AUTOMACAO_CONTRATO_VERSAO` no portal e o worker recusa rodar se o `/saude`
> devolver outra versão.

Base: `https://<portal>/api/automacao`. Modelo **pull**: o worker pergunta; o
portal nunca chama o worker.

## Autenticação

| Header | Valor |
|---|---|
| `X-Automacao-Token` | igual à env `AUTOMACAO_WORKER_TOKEN` do portal (comparação em tempo constante) |
| `X-Automacao-Worker` | identificador do worker (ex.: `railway-automacao-1`); opcional, cai para o IP |

Sem `AUTOMACAO_WORKER_TOKEN` configurada no portal, **todas** as rotas
respondem `404` (fail-closed). Token errado → `401`.

## Rotas

### `GET /saude`
Handshake no boot do worker.
```json
{"ok": true, "versao_contrato": "1", "ativa": true,
 "tipos": ["CRIACAO", "DESLIGAMENTO", "REVOGAR_LICENCA"],
 "fila": {"NA_FILA": 2, "EXECUTANDO": 0}, "worker_id": "railway-automacao-1"}
```

### `POST /jobs/proximo`
Corpo opcional: `{"tipos": ["CRIACAO", "DESLIGAMENTO"]}` (default: todos).
- `204` — nada a fazer. Header `X-Automacao-Pausada: 1` quando o kill switch
  (`AUTOMACAO_ATIVA=false`) está desligado: o worker deve esperar mais
  (ex.: 60 s) antes de perguntar de novo.
- `200` — um job foi **atribuído** a este worker (claim atômico; outro
  worker nunca recebe o mesmo job):
```json
{"id": "uuid", "tipo": "CRIACAO", "dry_run": false, "tentativa": 1,
 "chamado": {"id": "uuid", "codigo": "BD-2026-00901"},
 "payload": { ... ver abaixo ... }}
```
`chamado` é `null` num job de `SINCRONIZAR_LIDERANCA` sem origem (a
sincronização diária); a por evento leva o chamado que disparou (só
informativo — não entra no cálculo).

Recomendação de polling: a cada 15–30 s.

### `POST /jobs/{id}/heartbeat`
Corpo: `{"etapa": "WMW Vendas Web - Cadastro e Gerador de Link"}`. Enviar
**antes de cada etapa** (e a cada ~2 min dentro de etapas longas do
Playwright). O portal dá o job como morto após `AUTOMACAO_HEARTBEAT_TIMEOUT_S`
(default 900 s) sem heartbeat.
- `200 {"ok": true}`
- `409` — o job **não é mais deste worker** (vigilância o marcou como
  travado, ou já terminou). O worker deve **abortar** a execução e **não**
  enviar resultado.

### `POST /jobs/{id}/resultado`
Transição final. Enviar **uma vez**; segundo envio → `409`.
```json
{
  "etapas": [
    {"step_name": "Microsoft 365 (MS Graph) - Criação de Conta e Grupos",
     "status": "SUCCESS", "error_message": null,
     "details": {"email": "joao.souza@bondmann.com.br", "groups_assigned": ["Grupo Bondmann"]}},
    {"step_name": "WMW Vendas Web - Cadastro e Gerador de Link",
     "status": "FAILED", "error_message": "timeout no login", "details": {}}
  ],
  "erro": null,
  "credenciais": {
    "email": "joao.souza@bondmann.com.br",
    "senha_temporaria_m365": "…",
    "senha_ubd": "…",
    "link_wmw": "https://…", "senha_wmw": "082#araraquara",
    "ramal_sip": "5104", "senha_ramal_sip": "…",
    "usuario_sap": "joao", "senha_sap": "…"
  },
  "licenca": {"email": "…", "ms_user_id": "guid-entra", "offboard_date": "2026-09-30"}
}
```
- `etapas` — dump dos `StepResult` da automação (`step_name`, `status` em
  `SUCCESS|FAILED|SKIPPED`, `error_message`, `details`). Chaves de `details`
  que pareçam segredo (`senha`, `password`, `token`, …) são **mascaradas**
  pelo portal antes de gravar; mesmo assim, prefira não mandá-las ali.
- `erro` — exceção fora do fluxo (o worker morreu antes de completar as
  etapas). Presente ⇒ job `FALHOU`.
- `credenciais` — só as chaves acima são reconhecidas; vão **exclusivamente**
  para a mensagem de encerramento ao RH (D4), nunca para a tabela. Omitir
  em dry-run. Ignorado em `DESLIGAMENTO`/`REVOGAR_LICENCA`. `senha_sap`
  (2026-09-15, aditivo): senha inicial do usuário SAP gerada pelo worker
  (4–10 chars, política do B1) — antes a CLI usava um valor fixo.
  `senha_ubd` (2026-09-29, aditivo): senha inicial da UBD Learning.rocks — só
  quando a etapa UBD criou o usuário agora (quem já existia mantém a senha).
- `licenca` — só em `DESLIGAMENTO`, quando a etapa M365 concluiu: o portal
  agenda o job `REVOGAR_LICENCA` para `offboard_date + AUTOMACAO_LICENCA_DIAS`
  (default 15) às 07h de Brasília.

Resposta: `200 {"ok": true, "status": "CONCLUIDO|CONCLUIDO_COM_PENDENCIAS|FALHOU"}`.
Classificação: todas `SUCCESS` ⇒ `CONCLUIDO` (chamado é RESOLVIDO e o RH
recebe a mensagem de encerramento com as credenciais); nenhuma `SUCCESS` ou
`erro` ⇒ `FALHOU` (só nota interna + e-mail à TI); misto ⇒
`CONCLUIDO_COM_PENDENCIAS` (mensagem parcial ao RH, sem credenciais; TI conclui
à mão ou reexecuta só as pendências).

## Payload por tipo

Campos comuns: `versao` (`"1"`), `tipo`, `chamado {id, codigo}` (`null` só em
`SINCRONIZAR_LIDERANCA` sem origem), `pular_etapas` (lista de `step_name` já
concluídos num job anterior — o worker deve **pular** essas etapas e
devolvê-las como `SKIPPED` com `error_message: "já concluída em execução
anterior"`; o portal conta essas etapas como **concluídas** na
classificação, não como pendência; sempre `[]` em `SINCRONIZAR_LIDERANCA`,
que não reexecuta por partes).

Nomes de etapa (`step_name`) que o worker devolve — são constantes em
`flow.py` (`STEP_*`) e parte do contrato:

| Tipo | Etapas (na ordem) |
|---|---|
| `CRIACAO` | `Microsoft 365 (MS Graph) - Criação de Conta e Grupos` · `UBD Learning.rocks - Cadastro e Atribuição de Times` · `WMW Vendas Web - Cadastro e Gerador de Link` (só REPRESENTANTE) · `CompanySIP PABX - Criação de Ramal Interno` (só INTERNO) · `SAP Business One (Service Layer) - Criação de Usuário Interno` (INTERNO), `SAP Business One (Service Layer) - Vínculo Comercial (Consultor PJ)` (REPRESENTANTE com região) ou `SAP Business One (Service Layer) - Ocupar Vaga (Equipe/Gerência)` (SUPERVISOR/GERENTE) |
| `DESLIGAMENTO` | `Microsoft 365 (MS Graph) - Bloqueio e Encaminhamento` · `UBD Learning.rocks - Inativação da Conta` · `WMW Vendas Web - Bloqueio de Acesso` (só REPRESENTANTE) · `SAP Business One (Service Layer) - Bloqueio e Liberação de Licenças` (INTERNO), `SAP Business One (Service Layer) - Devolver Vaga (Equipe/Gerência)` (SUPERVISOR/GERENTE com `vaga`) ou `SAP Business One (Service Layer) - Transferência para RH2020` (demais) |
| `REVOGAR_LICENCA` | `Microsoft 365 (MS Graph) - Revogação da Licença` |
| `SINCRONIZAR_LIDERANCA` | `UBD Learning.rocks - Sincronização de Liderança` (etapa única) |

A etapa "Portal de Chamados Bondmann - Conta e Permissões" é acrescentada
pelo **portal** ao processar uma `CRIACAO` concluída (não vem do worker).

**Mudanças da v2 (F3/F4, 2026-09-25) — aditivas, o contrato continua `"1"`:**
`perfil` aceita `GERENTE`; campo novo `vaga` para SUPERVISOR/GERENTE (e `regiao`
nula para eles); etapas novas de vaga; `gestor_email` passa a vir da "Gerência
responsável" do interno; os `details` da etapa UBD da criação ganham
`leaders_aplicados` (ou `leaders_previstos` na simulação) e `avisos`.
**Ordem de deploy: worker primeiro, portal depois** — um worker antigo que
receba `GERENTE` ou supervisor sem `regiao` levanta `PayloadInvalido` e o job
FALHA sem tocar em sistema nenhum (fail-safe). Pelo mesmo motivo, um chamado de
supervisor aberto **antes** do deploy do portal (só com `regiao`, sem `vaga`)
falha no worker novo: reabrir o formulário e escolher a equipe.

**Mudanças da v2 (F5, 2026-09-29) — aditivas, o contrato continua `"1"`:**
tipo novo `SINCRONIZAR_LIDERANCA`, sem chamado dono (ver seção própria
abaixo). Não muda nada nos demais tipos; só entra em ação quando
`AUTOMACAO_TIPOS` o lista. Atenção: com `WORKER_TIPOS` vazio o worker **não**
manda `tipos` no `POST /jobs/proximo` e o portal usa todos os tipos que
conhece (`dom.TIPOS`) ∩ `AUTOMACAO_TIPOS` — então um worker **antigo** (sem a
F5) pegaria a sync e ela terminaria `FALHOU` com "tipo de job desconhecido"
(inofensivo: nada é tocado, e a próxima diária tenta de novo). **Ordem de
deploy: worker → migrations `0092`/`0093` em produção → portal → incluir
`SINCRONIZAR_LIDERANCA` em `AUTOMACAO_TIPOS`.**

### `CRIACAO`
| Campo | Tipo | Notas |
|---|---|---|
| `nome_completo` | str | |
| `email` | str | sempre `@bondmann.com.br`, minúsculo |
| `perfil` | `REPRESENTANTE` \| `INTERNO` \| `SUPERVISOR` \| `GERENTE` | → `UserProfileType` |
| `telefone` | str | DDD + número |
| `gestor_email` | str \| null | só INTERNO: e-mail do gerente da "Gerência responsável" (ou o digitado, com "Outra") → líder na UBD |
| `data_inicio` | `YYYY-MM-DD` \| null | só informativo (o portal já agendou o job) |
| `cargo` | str \| null | só INTERNO → `job_title` |
| `portal_papel` | `CLIENTE` \| `OPERADOR` \| `ADMIN` \| null | só INTERNO; **a conta do Portal é criada pelo próprio portal** — o worker NÃO executa a etapa "Portal de Chamados" |
| `portal_setor` | str \| null | idem |
| `licencas_sap` | list[str] | subconjunto de `PROFESSIONAL, CRM, FINANCEIRA, LOGISTICA`; vazia = nenhuma |
| `regiao` | `{codigo, nome, completo}` \| null | só REPRESENTANTE; `completo` = `"082-ARARAQUARA"` |
| `vaga` | `{tipo, codigo, nome}` \| null | obrigatória para SUPERVISOR (`tipo: "EQUIPE"`) e GERENTE (`tipo: "GERENCIA"`); `codigo` = marcador `RH20xx` na `IB_CO_REGIAO` (ex.: `{"tipo": "EQUIPE", "codigo": "RH2018", "nome": "EQUIPE SP 1"}`) |
| `dispositivo_wmw` | `IOS` \| `ANDROID` \| `SIMULADOR` \| null | só REPRESENTANTE |
| `observacoes` | str \| null | texto livre do RH; não executar nada com base nele |

### `DESLIGAMENTO`
| Campo | Tipo | Notas |
|---|---|---|
| `nome_completo`, `email`, `perfil` | | como acima |
| `data_desligamento` | `YYYY-MM-DD` | informativo (o portal agendou às 17h desse dia, ou imediato se `urgente`) |
| `urgente` | bool | aditivo (sem bump de versão): o RH marcou "Urgência"; o portal já liberou o job no clique da TI — o worker não precisa fazer nada |
| `regiao` | `{codigo, nome, completo}` \| null | só REPRESENTANTE: região a transferir para RH2020 |
| `vaga` | `{tipo, codigo, nome}` \| null | SUPERVISOR/GERENTE: marcador que volta às regiões da pessoa |
| `motivo` | str | opcional no formulário; em branco o portal envia `"Encerramento de Contrato de Trabalho"` |
| `encaminhar_para` | str | caixa que recebe os e-mails (regra de inbox) |
| `observacoes` | str \| null | |

### `REVOGAR_LICENCA`
| Campo | Tipo | Notas |
|---|---|---|
| `email` | str | |
| `ms_user_id` | str \| null | id no Entra ID (se veio do resultado do desligamento) |
| `offboard_date` | `YYYY-MM-DD` \| null | |

Equivale ao comando `limpar-licencas` da CLI para **um** usuário: remover a
licença M365 (`assignLicenses`). Devolver uma etapa única.

### Liderança na UBD (v2, F4)

Só na **criação de usuário novo** na UBD (usuário que já existia não tem a
liderança alterada — aviso na etapa). Resolvida pelo **worker**:

| Perfil | Líderes | Fonte |
|---|---|---|
| REPRESENTANTE | supervisor (`U_IB_CodCom3`) + gerente (`U_IB_CodCom4`) da região | SAP; confirmado pela função `lideranca_da_regiao` do projeto da `regioes` (GET com token, somente leitura) — diverge ⇒ não aplica e avisa; sem leitura ⇒ aplica o do SAP e avisa |
| SUPERVISOR | gerente(s) das regiões da equipe | SAP |
| GERENTE | nenhum | — |
| INTERNO | `gestor_email` | payload |

Líder inexistente na UBD ou falha de leitura ⇒ etapa **SUCCESS** com aviso em
`details.avisos`; nunca aborta o fluxo. Envs do worker: `REGIOES_API_URL`,
`REGIOES_API_KEY` (chave anon) e `REGIOES_API_TOKEN` (qualquer uma vazia ⇒ confirmação desligada).

### `SINCRONIZAR_LIDERANCA` (v2, F5)

Mantém a liderança de representantes e supervisores na UBD igual à do SAP —
a criação (acima) só resolve líder pra usuário **novo**; quem já existia
depende desta sincronização. Sem chamado dono: nasce da vigilância (diária,
`AUTOMACAO_SYNC_HORA`) ou do resultado de um job de `CRIACAO`/`DESLIGAMENTO`
de SUPERVISOR/GERENTE cuja etapa de vaga deu `SUCCESS` (por evento, com
`executar_apos = agora + AUTOMACAO_SYNC_ATRASO_MIN`). Etapa única.

| Campo | Tipo | Notas |
|---|---|---|
| `chamado` | `{id, codigo}` \| null | `null` na sincronização diária; o chamado que disparou o evento, só informativo, na por evento |
| `pular_etapas` | `[]` | sempre vazio — etapa única |
| `modo` | `"relatorio"` \| `"aplicar"` | `AUTOMACAO_SYNC_MODO` (default `relatorio`); `relatorio` só lê (`PATCH {}`), nunca grava; `aplicar` grava com `PATCH {"leaders": [...]}` (lista completa — o PATCH substitui, não acumula) |
| `lideres_gerenciados` | list[str] | e-mails (minúsculos, ordenados) dos supervisores/gerentes já vistos por sincronizações anteriores (tabela `automacao_lideranca_gerenciada`) — só estes podem ser **removidos** de alguém |

Regras (worker: `services/lideranca.py` + `_sincronizar_lideranca` em
`flow.py`):

- **Fonte = SAP, a `regioes` só confirma.** O worker lê a `IB_CO_REGIAO`
  (`U_IB_CodCom1` representante, `CodCom3` supervisor, `CodCom4` gerente) e
  confirma cada região pela função `lideranca_da_regiao` da `regioes` (GET
  somente leitura, mesma API da criação). **Divergente** (SAP ≠ `regioes`)
  ⇒ os usuários da região ficam **fora do cálculo** nesta execução (não são
  tocados) e aparecem em `divergentes`. Sem confirmação possível
  (indisponível ou sem linha) ⇒ segue com o SAP e conta em
  `regioes_nao_confirmadas`.
- **Esperados:** representante = supervisor + gerente das regiões em que é
  o `CodCom1`; supervisor = gerentes das regiões em que é o `CodCom3`;
  gerente não tem líder; interno usa `gestor_email` e não passa por esta
  etapa.
- **Espelho com proteção (V4):** `novo = (atuais − (gerenciados −
  esperados)) ∪ esperados` — remove só quem está em `lideres_gerenciados` e
  deixou de ser esperado; um líder posto manualmente (fora dessa lista)
  nunca é removido. Só grava (`set_leader_ids`) quando `novo != atuais` e
  `modo = aplicar`.
- **Leitura por `PATCH {}`:** a API não tem GET de líderes (spike F5.0,
  2026-09-29) — `PATCH` com corpo vazio não altera nada e devolve
  `leader_ids`. Se a resposta parar de trazer essa chave
  (`LeituraLiderancaIndisponivel`), a **etapa inteira falha** — nunca é
  tratado como lista vazia, porque isso apagaria líderes de todo mundo.
- **Duas fases:** o worker primeiro **lê e calcula todos** os alvos; só
  depois, com `modo = aplicar`, grava os que mudaram. Assim a falha da
  leitura acima, mesmo no meio, derruba a etapa **antes de qualquer
  gravação**.
- Falha ao consultar ou ler **um alvo** (usuário sendo verificado) que não
  seja a ausência de `leader_ids` acima ⇒ vai para `details.erros`, os
  demais alvos continuam normalmente. Falha ao **gravar** um alvo ⇒ também
  `details.erros` (`erro` começa com `"gravação falhou: "`) e ele **não**
  entra em `corrigidos` — `corrigidos` só lista o que a UBD confirmou (no
  modo `relatorio`, o que seria corrigido).
- **Conta inativa na UBD:** o desligamento inativa a conta. Líder
  **gerenciado** é resolvido aceitando conta inativa (id + e-mail idêntico),
  para que o supervisor/gerente desligado seja **removido** de quem o tinha;
  alvo e líder **esperado** exigem conta ativa (alvo inativo ⇒
  `nao_encontrados`; esperado inativo ⇒ aviso "líder X não encontrado na
  UBD", não é adicionado).
- Falha ao consultar **um líder gerenciado** (ao montar `lideres_gerenciados
  ∪ comerciais de hoje no SAP`) ⇒ vira `details.avisos`, e esse líder
  **não é removido de ninguém nesta execução** (fica de fora do cálculo,
  como se não fosse gerenciado agora). A outra origem de `avisos` é o líder
  esperado não encontrado na UBD (acima).
- `details` do resultado: `{modo, verificados, corrigidos: [{email,
  adicionados, removidos}], nao_encontrados, divergentes: [{email,
  regioes}], regioes_nao_confirmadas, erros: [{email, erro}], avisos,
  lideres_comerciais}`. O portal grava `lideres_comerciais` na tabela
  `automacao_lideranca_gerenciada` (soma com execuções anteriores) depois
  de cada resultado — é essa tabela que alimenta `lideres_gerenciados` na
  próxima chamada.

Contrato continua `"1"` (aditivo). Migrations `0092`/`0093`, gatilhos e
envs (`AUTOMACAO_SYNC_MODO`/`AUTOMACAO_SYNC_HORA`/`AUTOMACAO_SYNC_ATRASO_MIN`)
estão em `plano_md_mestre_automacao_acessos_v2.md` (Seção 6).

## Comportamento esperado do worker

1. Boot: `GET /saude`; abortar se `versao_contrato` ≠ a sua.
2. Loop: `POST /jobs/proximo` → se `204`, dormir; se job, executar.
3. `dry_run: true` ⇒ rodar os fluxos em modo simulado (o `--dry-run` que já
   existe), sem tocar em sistema nenhum, e devolver as etapas normalmente.
4. Heartbeat antes de cada etapa; `409` ⇒ abortar sem enviar resultado.
5. Nunca pedir interação (sem TTY). **Erro crítico aborta** (regra do gestor,
   2026-09-15): a primeira etapa `FAILED` interrompe o fluxo e as seguintes
   voltam como `SKIPPED` com `error_message: "não executada — fluxo abortado
   após falha em '<etapa>'"`. O portal classifica como
   `CONCLUIDO_COM_PENDENCIAS`/`FALHOU`, alerta os admins da TI por e-mail com o
   erro de cada etapa e oferece "Reexecutar pendências" (só as não concluídas).
   Exceção que **não** é erro: no `DESLIGAMENTO` de INTERNO, usuário inexistente
   no SAP (nem por código, nem por e-mail) volta `SUCCESS` com
   `details.user_found=false` e a nota "não encontrado no SAP — nada a bloquear".
6. `POST /jobs/{id}/resultado` uma única vez; em erro de rede, repetir o
   POST (idempotente: `409` na segunda entrega significa que a primeira chegou).
7. Log em stdout, com `job_id` e `chamado.codigo`; senhas mascaradas.
