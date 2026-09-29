# Runbook — `automacao-worker` (automação de criação/desligamento de acessos)

> Governado por [`plano_md_mestre_automacao_acessos.md`](../plano_md_mestre_automacao_acessos.md)
> (Seções 2, 5 e 6). Contrato: [`docs/automacao_api.md`](automacao_api.md) (v1).
> Código do worker: projeto `Automação/` (repositório separado — `worker.py`,
> `flow.py`, `Dockerfile`, `entrypoint.sh`).

## 1. O que é

Segundo serviço do projeto Railway do portal. **Stateless**, sem domínio
público, sem acesso ao banco: faz *pull* de jobs em `/api/automacao/jobs/proximo`,
executa os fluxos (MS Graph, UBD/Skore, WMW e CompanySIP via Playwright, SAP
Service Layer) e devolve o resultado em `/jobs/{id}/resultado`. Todo o estado
(fila, aprovação, notas, credenciais na mensagem ao RH) fica no portal.

```
Railway ─ portal (web)  ◄── HTTPS (token) ── Railway ─ automacao-worker
                                                 │ direto: Graph, Skore, WMW, SIP
                                                 │ túnel Tailscale (userspace) ─► SAP 10.151.4.40:50000
```

## 2. Variáveis de ambiente (serviço `automacao-worker`)

| Env | Obrigatória | Valor / observação |
|---|---|---|
| `WORKER_MODE` | — | `1` (já vem do Dockerfile): sem TTY, sem arquivo de log, JSON em stdout |
| `PORTAL_API_URL` | ✅ | `https://<domínio do portal>` (o worker acrescenta `/api/automacao`) |
| `AUTOMACAO_WORKER_TOKEN` | ✅ | **mesmo valor** da env do portal. Gerar com `openssl rand -hex 32`. Rotação: trocar nos dois serviços e reiniciar o worker |
| `AUTOMACAO_CONTRATO_VERSAO` | — | `1`. O worker recusa rodar se `/saude` devolver outra versão |
| `WORKER_ID` | — | ex.: `railway-automacao-1` (aparece no card e na nota interna) |
| `WORKER_POLL_S` / `WORKER_POLL_PAUSADA_S` | — | 20 / 60 s |
| `WORKER_HEARTBEAT_S` | — | 120 s (ticker dentro de etapas longas do Playwright). Tem de ser **menor** que `AUTOMACAO_HEARTBEAT_TIMEOUT_S` do portal (900) |
| `WORKER_TIPOS` | — | vazio = todos os tipos que o portal liberar (`AUTOMACAO_TIPOS`) |
| `TS_AUTHKEY` | ✅ no Railway | auth key do Tailscale: *ephemeral + reusable + pre-authorized*, com `tag:automacao-worker`. Sem ela o entrypoint **pula o túnel** (plano B on-prem) |
| `SAP_PROXY_URL` | — | o entrypoint define `http://localhost:1055` quando há `TS_AUTHKEY`. Só defina à mão fora do Railway |
| `MS_TENANT_ID`, `MS_CLIENT_ID`, `MS_CLIENT_SECRET` | ✅ | app-only do Entra ID (`Directory.ReadWrite.All`, `User.ReadWrite.All`, `Group.ReadWrite.All`, `MailboxSettings.ReadWrite`) |
| `UBD_API_TOKEN` (+ `UBD_API_URL`, `UBD_COMPANY_ID`, `UBD_WORKSPACE_SLUG`) | ✅ | Skore M2M |
| `SAP_API_URL`, `SAP_COMPANY_DB`, `SAP_USERNAME`, `SAP_PASSWORD` | ✅ | Service Layer em `SBO_BONDMANN_PRD` |
| `WMW_ADMIN_USER`, `WMW_ADMIN_PASS`, `WMW_BASE_URL` | ✅ | conta **dedicada** à automação (rastreabilidade) |
| `SIP_ADMIN_USER`, `SIP_ADMIN_PASS`, `SIP_BASE_URL`, `SIP_SERVER_DOMAIN` | ✅ | idem; **não há mais default no código** |

Essas credenciais ficam **só** no serviço do worker — nunca no serviço web do
portal (Seção 7 do plano). No portal, as envs são `AUTOMACAO_*` (ver
`.env.example`); `AUTOMACAO_ATIVA=false` é o kill switch global e
**`AUTOMACAO_ALERTA_EMAIL`** (lista separada por vírgula) define quem recebe os
alertas — sem ela **nenhum alerta sai** (fica só um warning no log do portal).
Valor acordado com o gestor: `osvaldo.bello@bondmann.com.br,giordano.burtet@bondmann.com.br`.

## 3. Deploy no Railway

1. **Novo serviço** no mesmo projeto do portal → *Deploy from GitHub repo* →
   repositório da automação (`Dockerfile` na raiz; `railway.json` já define
   builder e política de restart). Sem domínio público. Memória: mínimo **2 GB**
   (imagem Playwright ~1,5 GB; pico ~1 GB no scraping).
2. Variáveis da Seção 2. Só então `AUTOMACAO_WORKER_TOKEN` no serviço do portal
   (mesmo valor) — enquanto o portal não tiver a env, a API responde 404 e o
   worker reinicia em loop com "API fechada" no log (esperado).
3. Log esperado no boot (uma linha JSON cada):
   - `iniciando tailscaled (userspace) …` → `tailscale conectado: 100.x.y.z`
   - `Worker railway-automacao-1 iniciando contra https://…/api/automacao (SAP via túnel http://localhost:1055)`
   - `Handshake ok: contrato v1, automação ativa|PAUSADA, tipos liberados […], fila {…}`
4. **Pré-requisito on-prem (F3a)** — ver Seção 3.1. O entrypoint faz um
   self-check no boot e loga `SAP alcançável via túnel: … respondeu HTTP 4xx`
   (qualquer código HTTP prova alcance) ou `SAP INALCANÇÁVEL via túnel`.
5. **F5 (sincronização de liderança na UBD)** — ordem obrigatória: deploy do
   **worker** primeiro (o tipo `SINCRONIZAR_LIDERANCA` não existe até o merge
   dele) → aplicar as migrations **`0092`** e depois **`0093`** em produção
   (Supabase, SQL Editor) → deploy do **portal** → só então incluir
   `SINCRONIZAR_LIDERANCA` em `AUTOMACAO_TIPOS` do portal (ver Seção 4.1).

### 3.1 Túnel Tailscale até o SAP (passo a passo)

**A. Máquina on-prem (subnet router)** — qualquer servidor sempre ligado que
enxergue `10.151.4.40:50000` (testar antes:
`curl -sk -X POST https://10.151.4.40:50000/b1s/v1/Login` → 4xx = ok).

- Linux (preferido):
  ```bash
  curl -fsSL https://tailscale.com/install.sh | sh
  echo 'net.ipv4.ip_forward = 1' | sudo tee /etc/sysctl.d/99-tailscale.conf && sudo sysctl -p /etc/sysctl.d/99-tailscale.conf
  sudo tailscale up --advertise-routes=10.151.4.40/32 --hostname=sap-subnet-router
  ```
- Windows Server: instalar o Tailscale, logar com a conta admin, depois em
  PowerShell (admin): `tailscale up --advertise-routes=10.151.4.40/32 --hostname=sap-subnet-router --unattended`
  (`--unattended` mantém o serviço rodando sem usuário logado).

Anunciar **só o /32** do SAP (não a /24): menor superfície.

**B. Console admin (login.tailscale.com/admin)**
1. *Machines* → `sap-subnet-router` → *Edit route settings* → aprovar
   `10.151.4.40/32`. Desligar *Key expiry* dessa máquina (senão o túnel cai
   em 180 dias).
2. *Access controls* → política (HuJSON):
   ```json
   {
     "tagOwners": { "tag:automacao-worker": ["autogroup:admin"] },
     "acls": [
       { "action": "accept", "src": ["autogroup:admin"], "dst": ["*:*"] },
       { "action": "accept", "src": ["tag:automacao-worker"], "dst": ["10.151.4.40:50000"] }
     ]
   }
   ```
   A segunda regra é a única do worker: ele não enxerga mais nada da rede.
3. *Settings → Keys → Generate auth key*: **Reusable** ✅, **Ephemeral** ✅,
   **Pre-authorized** ✅, *Tags* = `tag:automacao-worker`, expiração 90 dias
   (é a validade da *key*, não do nó; anotar a data para rotação). Copiar
   `tskey-auth-…` — é a env `TS_AUTHKEY` do worker.

**C. Railway** — `TS_AUTHKEY` no serviço `automacao-worker` e redeploy. No
log: `tailscale conectado: 100.x.y.z` e depois `SAP alcançável via túnel`.

O mesmo `Dockerfile` roda numa VM on-prem (plano B): `docker run --env-file .env
<imagem>` sem `TS_AUTHKEY` → conexão direta ao SAP.

## 4. Operação do dia a dia

- **Onde ver**: card "Automação de acessos" no atendimento do chamado (status,
  etapas, worker, histórico) + nota interna técnica por execução. `/health/ready`
  do portal reporta "último worker visto há X min".
- **Um job por vez** por réplica. Poll a cada 20 s; quando `AUTOMACAO_ATIVA=false`
  o portal responde `X-Automacao-Pausada: 1` e o worker espera 60 s.
- **Reexecução**: botão "Reexecutar pendências" cria job novo com `pular_etapas`
  = etapas já concluídas; o worker as devolve como `SKIPPED — já concluída em
  execução anterior` e o portal as conta como concluídas.
- **Parar tudo**: `AUTOMACAO_ATIVA=false` no portal (jobs na fila ficam
  esperando). Parar só o worker: *Remove/Sleep* do serviço no Railway — a
  vigilância do portal avisa a TI por e-mail após 30 min sem contato em horário
  comercial.

### 4.1 Sincronização de liderança na UBD (v2, F5)

Mantém a liderança de representantes e supervisores na UBD igual à do SAP.
Job sem chamado dono (`chamado_id` nulo — CHECK da migration `0093`); não
aparece no card de nenhum chamado, só nota interna (quando por evento) e
e-mail.

- **Gatilhos**: diário, a partir das **06h Brasília** (`AUTOMACAO_SYNC_HORA`,
  env do portal, default 6) se nenhuma sync foi criada no dia; **por
  evento**, `AUTOMACAO_SYNC_ATRASO_MIN` (env do portal, default 60) depois de
  um job **real** de CRIACAO/DESLIGAMENTO de SUPERVISOR/GERENTE concluir a
  etapa de vaga com `SUCCESS` (tempo para a `regioes` se atualizar em lote).
- **Modo**: `AUTOMACAO_SYNC_MODO` (env do portal, default `relatorio`) —
  `relatorio` só lê a UBD (`PATCH {}`), nunca grava; `aplicar` grava de
  verdade (`PATCH {"leaders": [...]}`).
- **Gate**: só entra em ação com `SINCRONIZAR_LIDERANCA` incluído em
  `AUTOMACAO_TIPOS` (portal) — sem isso a vigilância nem enfileira (evita o
  alerta de "worker mudo" por fila vencida). `WORKER_TIPOS` do worker (Seção
  2) vazio já aceita o tipo assim que o portal liberar; não precisa mexer
  nele para ligar a sync.
- **Relatório**: sempre por e-mail a `AUTOMACAO_ALERTA_EMAIL` no modo
  `relatorio` (o gestor revisa); no modo `aplicar` só com falha, não
  encontrados, divergências ou erros. Quando é por evento, o mesmo texto
  também vira nota interna no chamado que disparou.
- **Desligar rápido**: tirar `SINCRONIZAR_LIDERANCA` de `AUTOMACAO_TIPOS`, ou
  voltar `AUTOMACAO_SYNC_MODO` para `relatorio` (mantém a leitura/relatório,
  para de gravar na UBD).

Contrato completo: [`docs/automacao_api.md`](automacao_api.md), seção
`SINCRONIZAR_LIDERANCA (v2, F5)`. Desenho: `plano_md_mestre_automacao_acessos_v2.md`,
Seção 6.

## 5. Diagnóstico

| Sintoma (log/card) | Causa provável | Ação |
|---|---|---|
| `/saude respondeu 404: … API fechada` | `AUTOMACAO_WORKER_TOKEN` vazia no portal | configurar no serviço do portal |
| `/saude respondeu 401` | tokens diferentes entre os serviços | igualar e reiniciar o worker |
| `portal fala o contrato vX, este worker fala vY` (exit 3) | deploy do portal mudou `docs/automacao_api.md` | atualizar o worker (mesmo PR do contrato) |
| `tailscale up FALHOU — abortando` (reinicia em loop) | auth key expirada/revogada ou sem a tag | gerar nova key no console do Tailscale (reusable, ephemeral, pre-authorized) |
| Etapas SAP `FAILED` com timeout/conexão; resto `SUCCESS` | subnet router on-prem fora, rota não aprovada, ACL | checar o servidor on-prem (`tailscale status`), rota e ACL; reexecutar pendências |
| Etapa WMW/SIP `FAILED` ("locator", "timeout") | mudança de tela nos portais | rodar a CLI local em `--headed`, corrigir seletor no `services/*_scraper.py`; TI conclui à mão enquanto isso |
| Etapas `SKIPPED — não executada (fluxo abortado após falha em X)` | uma etapa anterior falhou; o worker aborta por regra | corrigir a causa da etapa X (o alerta traz o erro) e usar "Reexecutar pendências" |
| Etapa SAP ✅ com "não encontrado no SAP — nada a bloquear" | colaborador nunca teve usuário SAP | nada a fazer; não é pendência |
| Job `FALHOU` com "worker parou no meio" | worker reiniciado/derrubado durante o job | criação: o portal reenfileira uma vez; desligamento: **não** reenfileira — conferir nos sistemas o que foi feito antes de reexecutar |
| `Job abortado: … heartbeat 409` | vigilância do portal deu o job como morto (worker travou > 15 min) ou job cancelado | nada a fazer no worker; ver o card |
| Handshake ok mas nada roda | `AUTOMACAO_ATIVA=false`, ou `AUTOMACAO_TIPOS`/`WORKER_TIPOS` sem interseção, ou `executar_apos` no futuro | ver `/saude` (`ativa`, `tipos`, `fila`) e a data agendada no card |
| Sincronização de liderança travada / relatório parou de chegar | o índice `ux_automacao_jobs_sync_ativo` (0093) permite só **uma** sync ativa (`NA_FILA`/`EXECUTANDO`) por vez; se travou, a vigilância a marca como job travado (heartbeat vencido) e alerta por e-mail **sem link de chamado** (ela não tem um) | ler o alerta; se precisar parar até investigar, tirar `SINCRONIZAR_LIDERANCA` de `AUTOMACAO_TIPOS` (Seção 4.1) |
| Etapa "UBD Learning.rocks - Sincronização de Liderança" `FAILED` com "a UBD não devolveu leader_ids" | comportamento do `PATCH {}` de leitura mudou (`LeituraLiderancaIndisponivel`) — a etapa **inteira** falha e **nada é gravado** nesta execução (nunca é tratado como lista vazia) | não é pendência parcial: investigar a API da UBD antes de qualquer coisa; o próximo gatilho (diário ou por evento) tenta de novo sozinho |
| Um e-mail aparece em `erros` mas outro em `avisos` no relatório da sync | `erros` = falha ao consultar/gravar **um alvo** (usuário sendo verificado) — os demais alvos continuam normalmente; `avisos` = falha ao consultar **um líder gerenciado** (supervisor/gerente) — esse líder não é removido de ninguém nesta execução | normal quando isolado; só investigar se repetir sempre para o mesmo e-mail |
| Representante/supervisor sumiu do relatório (não está em `corrigidos` nem em `nao_encontrados`) | a região dele diverge entre o SAP e a `regioes` — fica **fora do cálculo** desta execução e aparece em `divergentes` | conferir `IB_CO_REGIAO` no SAP × a `regioes`; a sincronização volta a incluir a pessoa quando as duas baterem |

Logs: Railway → serviço → *Logs*. Cada linha JSON tem `job_id`, `chamado`,
`tipo`, `worker`. Senhas nunca aparecem (redação no logger + mascaramento antes
do envio); se aparecer alguma, é bug — abrir chamado para a TI.

## 6. Reversão manual de um desligamento indevido

1. Entra ID: reativar a conta (`accountEnabled=true`) e redefinir a senha; remover
   a regra de caixa de entrada "Redirecionamento Automático de Desligamento";
   readicionar aos grupos (a nota interna lista os removidos).
2. Learning.rocks: reativar o usuário (`PATCH … {"active": true}`).
3. WMW: reativar o representante e redefinir a senha (padrão `codigo#regiao`).
4. SAP: interno → `Locked=tNO`; comercial → devolver a região em `IB_CO_REGIAO`
   (`U_IB_CodCom1` representante / `U_IB_CodCom3` supervisor) de `RH2020` para o
   consultor.
5. Portal: cancelar o job `REVOGAR_LICENCA` agendado (card do chamado) — senão a
   licença M365 cai em 15 dias.

## 7. Limitações conhecidas (2026-09-15)

- **Subnet router**: `sap-router-vm` (VM Windows, `10.151.4.10`, mesma rede
  do SAP `10.151.4.40`), tag `tag:sap-router`, *key expiry* desligada —
  anuncia a rota `10.151.4.40/32` primária. O notebook do gestor
  (`sap-subnet-router`) não anuncia mais a rota. **Plano de volta**: se a VM
  cair, remarcar a rota `10.151.4.40/32` no notebook pelo console admin do
  Tailscale (a ACL e a auth key do worker não mudam).

- **Estoque de licenças SAP** (`sap_licenses.json`) é um arquivo local — num
  container é efêmero e reseta a cada deploy. A checagem "sem estoque = etapa
  falha" só é confiável na CLI on-prem. Candidato a v2: consultar o Service
  Layer ao vivo.
- Teste E2E real (F4) ainda não executado. Imagem construída e rodando no
  Railway desde 2026-09-15 (handshake + túnel validados).
- Dry-run ainda consulta a listagem de times do Skore (`get_all_teams`) —
  chamada de leitura, cai em fallback se falhar.
