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

Campos comuns: `versao` (`"1"`), `tipo`, `chamado {id, codigo}`,
`pular_etapas` (lista de `step_name` já concluídos num job anterior — o
worker deve **pular** essas etapas e devolvê-las como `SKIPPED` com
`error_message: "já concluída em execução anterior"`; o portal conta essas
etapas como **concluídas** na classificação, não como pendência).

Nomes de etapa (`step_name`) que o worker devolve — são constantes em
`flow.py` (`STEP_*`) e parte do contrato:

| Tipo | Etapas (na ordem) |
|---|---|
| `CRIACAO` | `Microsoft 365 (MS Graph) - Criação de Conta e Grupos` · `UBD Learning.rocks - Cadastro e Atribuição de Times` · `WMW Vendas Web - Cadastro e Gerador de Link` (só REPRESENTANTE) · `CompanySIP PABX - Criação de Ramal Interno` (só INTERNO) · `SAP Business One (Service Layer) - Criação de Usuário Interno` (INTERNO) ou `SAP Business One (Service Layer) - Vínculo Comercial (Consultor PJ)` (REPRESENTANTE com região) |
| `DESLIGAMENTO` | `Microsoft 365 (MS Graph) - Bloqueio e Encaminhamento` · `UBD Learning.rocks - Inativação da Conta` · `WMW Vendas Web - Bloqueio de Acesso` (só REPRESENTANTE) · `SAP Business One (Service Layer) - Bloqueio e Liberação de Licenças` (INTERNO) ou `SAP Business One (Service Layer) - Transferência para RH2020` (demais) |
| `REVOGAR_LICENCA` | `Microsoft 365 (MS Graph) - Revogação da Licença` |

A etapa "Portal de Chamados Bondmann - Conta e Permissões" é acrescentada
pelo **portal** ao processar uma `CRIACAO` concluída (não vem do worker).

### `CRIACAO`
| Campo | Tipo | Notas |
|---|---|---|
| `nome_completo` | str | |
| `email` | str | sempre `@bondmann.com.br`, minúsculo |
| `perfil` | `REPRESENTANTE` \| `INTERNO` \| `SUPERVISOR` | → `UserProfileType` |
| `telefone` | str | DDD + número |
| `gestor_email` | str \| null | → `manager_email` / `leaders` do UBD |
| `data_inicio` | `YYYY-MM-DD` \| null | só informativo (o portal já agendou o job) |
| `cargo` | str \| null | só INTERNO → `job_title` |
| `portal_papel` | `CLIENTE` \| `OPERADOR` \| `ADMIN` \| null | só INTERNO; **a conta do Portal é criada pelo próprio portal** — o worker NÃO executa a etapa "Portal de Chamados" |
| `portal_setor` | str \| null | idem |
| `licencas_sap` | list[str] | subconjunto de `PROFESSIONAL, CRM, FINANCEIRA, LOGISTICA`; vazia = nenhuma |
| `regiao` | `{codigo, nome, completo}` \| null | REPRESENTANTE/SUPERVISOR; `completo` = `"082-ARARAQUARA"` |
| `dispositivo_wmw` | `IOS` \| `ANDROID` \| `SIMULADOR` \| null | só REPRESENTANTE |
| `observacoes` | str \| null | texto livre do RH; não executar nada com base nele |

### `DESLIGAMENTO`
| Campo | Tipo | Notas |
|---|---|---|
| `nome_completo`, `email`, `perfil` | | como acima |
| `data_desligamento` | `YYYY-MM-DD` | informativo (o portal agendou às 18h desse dia) |
| `regiao` | `{codigo, nome, completo}` \| null | região a transferir para RH2020 |
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
