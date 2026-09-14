# Migração Meta Cloud API → WUZAPI (whatsmeow)

Pesquisa técnica e plano de execução — 2026-09-01
Alvo: `app/whatsapp_client.py`, `app/routes/whatsapp.py`, `app/ia/whatsapp_intake.py`
Artefatos prontos: [`docs/wuzapi/`](wuzapi/) (cliente, rota de webhook, fila de
notificações, migration, compose, backup, patches)

---

## 0. Antes de tudo: dois fatos que mudam a decisão

**0.1 — O risco não é técnico, é de conta.** O whatsmeow fala o protocolo do
WhatsApp Web com um número comum pareado por QR Code. Isso viola os Termos de
Serviço do WhatsApp, e a documentação do próprio wuzapi diz isso em letras
maiúsculas ("usar este software violando os Termos pode fazer seu número ser
banido"). Banimento não tem SLA, não tem suporte e não tem recurso prático: o
número some, e com ele o histórico de conversa dos representantes. Nada nesta
pesquisa elimina esse risco — o que dá para fazer é reduzir a probabilidade
(Eixos 2 e 3) e garantir que a queda seja recuperável em minutos (Eixo 1 +
fallback para e-mail, que já existe no portal). **Mitigação obrigatória: chip
dedicado, não o número principal da empresa.**

**0.2 — O custo que se evita hoje é menor do que parece; em outubro/2026 ele
cresce.** Como o intake é sempre iniciado pelo funcionário/representante, as
respostas do bot caem na janela de atendimento de 24h, que na Cloud API é
gratuita hoje. O que já custa é a **notificação ativa** (chamado respondido
fora da janela → template utility, ~R$ 0,04–0,05 por mensagem no Brasil). A
partir de **1º/10/2026** a Meta passa a cobrar também as mensagens de serviço
e as respostas utility dentro da janela — ou seja, o intake inteiro, que hoje
é grátis, vira custo por mensagem. É essa data, e não o preço de hoje, que
justifica ter a alternativa pronta e testada.

**Recomendação:** manter os dois provedores vivos atrás da flag
`WHATSAPP_PROVIDER` (o adaptador do Eixo 5 faz exatamente isso), rodar o
wuzapi em piloto com um chip dedicado e um departamento, e só considerar o
corte total quando o piloto tiver 30 dias sem incidente. Custo zero com
rollback de um `env var` é uma posição melhor do que custo zero irreversível.

---

## 1. Eixo 1 — Sessão: persistência, reconexão e backup

### 1.1 Onde o pareamento vive

O wuzapi usa o `sqlstore` do whatsmeow. Dois bancos, ambos no diretório de
dados (default `dbdata/`, ao lado do executável; `-datadir` muda):

| Arquivo | Conteúdo | Perder custa |
|---|---|---|
| `dbdata/main.db` | store do whatsmeow: identidade do device pareado, chaves Noise/Signal, prekeys, app state | **QR Code novo** + reaquecimento do número |
| `dbdata/users.db` | usuários do wuzapi: nome, token, webhook, eventos assinados | um `POST /admin/users` |

DSN real do SQLite montado pelo wuzapi:

```
file:dbdata/main.db?_pragma=foreign_keys(1)&_pragma=journal_mode(WAL)&_pragma=busy_timeout(10000)
```

Com as cinco variáveis `DB_USER`, `DB_PASSWORD`, `DB_NAME`, `DB_HOST`,
`DB_PORT` preenchidas, o wuzapi troca SQLite por PostgreSQL (mesmo schema,
`sqlstore.New(ctx, "postgres", ...)`). **Para um único número, fique no
SQLite**: menos peça móvel, e o backup é um arquivo. Postgres só se passar a
haver várias sessões/instâncias.

> Não aponte o wuzapi para o Postgres do Supabase da aplicação. São dezenas de
> tabelas próprias do whatsmeow, com escrita constante de chaves, num banco que
> tem RLS e migrations versionadas da app. Se for Postgres, que seja um banco
> separado.

### 1.2 Restart sem QR Code

O pareamento sobrevive a restart/deploy/atualização de imagem desde que
`dbdata/` esteja num volume. No compose de referência
([`docs/wuzapi/docker-compose.yml`](wuzapi/docker-compose.yml)):

```yaml
    volumes:
      - wuzapi-dbdata:/app/dbdata
```

**No Railway** (onde o portal já roda): serviço novo a partir de uma imagem
pronta do Docker Hub (não do repositório do wuzapi — não há Dockerfile
próprio a manter), com um **Volume** montado em `/app/dbdata`, e o portal
falando com ele pela rede privada (`http://wuzapi.railway.internal:8080`) —
sem expor domínio público. Sem o volume, cada deploy do Railway recria o
filesystem e pede QR de novo. Runbook completo, passo a passo (dashboard +
CLI), em [`docs/wuzapi/railway/README.md`](wuzapi/railway/README.md) —
inclui a ressalva de que o backup do
[`backup_wuzapi.sh`](wuzapi/backup_wuzapi.sh) genérico desta seção **não
roda no Railway** (volume exclusivo de um serviço, sem `sqlite3` na imagem
runtime) e por isso usa os mecanismos nativos da plataforma em vez dele.

Confira uma vez, com o container de pé, que os arquivos estão mesmo no ponto
montado (se a imagem mudar o WORKDIR, o mount muda junto):

```bash
docker compose exec wuzapi ls -la /app/dbdata
```

### 1.3 Continuidade das conversas em andamento

Três camadas, e nenhuma delas depende do wuzapi lembrar de nada:

1. **Reconexão** — o whatsmeow reconecta sozinho ao WebSocket; mensagens
   enviadas durante a queda são entregues pelo servidor do WhatsApp quando a
   sessão volta (o protocolo é offline-first, igual ao WhatsApp Web). Só é
   preciso que o container volte com o mesmo `main.db`.
2. **Estado da conversa** — vive em `whatsapp_conversas`
   (`mensagens_acumuladas`, `rodada`), no Postgres do portal. Reboot do wuzapi
   não toca nisso: a rodada seguinte continua exatamente de onde parou.
3. **Mensagem que chegou com o portal fora do ar** — o wuzapi tem retry de
   webhook (`WEBHOOK_RETRY_ENABLED`, `WEBHOOK_RETRY_COUNT`,
   `WEBHOOK_RETRY_DELAY_SECONDS`); ligue os três. A âncora durável
   (`whatsapp_mensagens_recebidas`, migration 0086) e a reconciliação que já
   existe cobrem o resto — inclusive o caso "webhook gravado mas a task morreu
   no restart", que é o mesmo problema do incidente BOND-2026-00593.

**Idempotência:** o `Info.ID` do whatsmeow é único por chat, não globalmente.
Como `whatsapp_mensagens_recebidas.wamid` é UNIQUE global, a rota grava
`wuz:<telefone>:<Info.ID>` — mantém a garantia da coluna, não colide com os
`wamid` da Meta já gravados e ainda identifica a origem.

### 1.4 Backup

[`docs/wuzapi/backup_wuzapi.sh`](wuzapi/backup_wuzapi.sh), rodando de 6 em 6h
num container de 5 MB do próprio compose. O ponto não óbvio: **`cp` do `.db`
com o processo vivo é backup podre**. O banco está em WAL e escreve chaves o
tempo todo; a cópia pode sair sem as páginas do `-wal` e só se revelar
corrompida no dia do desastre. O script usa a API de backup online do SQLite
(`sqlite3 "file:...?mode=ro" ".backup ..."`), comprime, roda
`PRAGMA integrity_check` no arquivo gerado e mantém 14 dias.

Restauração (procedimento completo no rodapé do script): parar o container,
copiar `main.db`/`users.db` para o volume, **apagar os `-wal`/`-shm` antigos**
(eles pertencem ao banco substituído e reaplicam páginas por cima da
restauração) e subir. Validação:

```bash
curl -s -H "Token: $WUZAPI_TOKEN" http://127.0.0.1:8081/session/status
# {"code":200,"data":{"Connected":true,"LoggedIn":true},"success":true}
```

`LoggedIn: false` = pareamento revogado (alguém removeu o aparelho no celular,
ou o backup é anterior a um logout) → QR novo, não tem jeito.

---

## 2. Eixo 2 — "Digitando…" (ChatPresence)

### 2.1 Endpoint

```bash
curl -X POST http://wuzapi:8080/chat/presence \
  -H "Token: $WUZAPI_TOKEN" -H "Content-Type: application/json" \
  -d '{"Phone":"5551994105691","State":"composing","Media":""}'
```

| Campo | Valores | Efeito no celular |
|---|---|---|
| `State` | `composing` \| `paused` | "digitando…" / limpa |
| `Media` | `""` \| `"audio"` | com `audio`: "gravando áudio…" |

Complemento: `POST /chat/markread` com `{"Id":[...],"ChatPhone":...,"SenderPhone":...}`
dá o tique azul. Um número que responde sem nunca marcar lida é anômalo.

### 2.2 Encadeamento no ciclo da IA

Três momentos, implementados em
[`docs/wuzapi/whatsapp_client.py`](wuzapi/whatsapp_client.py):

1. **Na chegada da mensagem** (`_receber_mensagem`, depois de identificar o
   perfil): dispara `composing` numa task — aparece em ~200ms, antes de o LLM
   começar. É o "vi sua mensagem" que um humano dá.
2. **Durante o LLM** (`_processar_conversa`, ao redor de
   `chamar_modelo_estruturado`): o context manager `digitando()` mantém o
   estado. Detalhe que quase todo mundo erra: **o indicador do WhatsApp expira
   sozinho em ~10 segundos** — uma rodada de 8s deixaria a pessoa vendo o
   "digitando" sumir e voltar. O `digitando()` renova a cada 8s e sai sempre
   em `paused`, inclusive se o bloco levantar exceção (bot travado em
   "digitando…" para sempre é o artefato que se quer evitar).
3. **No envio** (`responder_humanizado`): atraso entre
   `WHATSAPP_DIGITACAO_MIN_S` e `WHATSAPP_DIGITACAO_MAX_S`, escalando com o
   comprimento do texto (~25 caracteres/segundo, teto configurável) e com
   jitter aleatório. Duas mensagens saindo com 30 ms de diferença é assinatura
   de automação; cadência sempre idêntica também.

Com `WHATSAPP_PROVIDER=meta`, `presenca()` é no-op — o mesmo código roda nos
dois provedores.

---

## 3. Eixo 3 — Notificações ativas sem flood e sem denúncia

### 3.1 Fila, não `create_task`

O disparo direto do handler ("operador respondeu → manda WhatsApp") tem dois
defeitos: cinco operadores respondendo no mesmo segundo viram cinco mensagens
em milissegundos (padrão de disparador em massa), e a task morre no restart —
o solicitante nunca fica sabendo. A proposta é uma **outbox em tabela**
([`0091_whatsapp_notificacoes_fila.sql`](wuzapi/0091_whatsapp_notificacoes_fila.sql))
com um worker único
([`notificacoes_whatsapp.py`](wuzapi/notificacoes_whatsapp.py)):

| Controle | Default | Por quê |
|---|---|---|
| Intervalo global + jitter | 2s + `random(0..3s)` | espaça e desalinha do relógio |
| Intervalo por destinatário | 120s | três respostas seguidas no mesmo chamado = uma mensagem |
| Coalescência | índice único parcial em `dedup_key` enquanto `PENDENTE` | duas atualizações antes do envio viram uma |
| Janela de silêncio | 07h–21h (America/Sao_Paulo) | notificação de madrugada é o gatilho clássico de "Bloquear/Denunciar" |
| Backoff | 30s × 2ⁿ, teto 30min, 5 tentativas | sessão caída não vira martelo no log |
| Claim | `FOR UPDATE SKIP LOCKED` | duas réplicas no Railway não duplicam mensagem |

Volume seguro para um chip novo, como referência prática: comece em ~20–30
mensagens ativas/dia, cresça devagar (ver 3.3). Com os defaults acima, o teto
mecânico é de ~1.800/hora — o limite real é comportamental, não técnico.

### 3.2 Redação (o fator nº 1 de banimento é a denúncia, não o volume)

Formato implementado em `texto_notificacao()`:

```
[Chamado BND-1234] Impressora do setor fiscal não imprime

Seu chamado recebeu uma resposta — Ana (TI) acabou de responder.

Ver no portal: https://portal.bondmann.com.br/chamados/8f2c…

Se quiser, pode responder por aqui mesmo que eu registro no chamado.
```

Regras que sustentam esse formato:

- **Identificação do chamado na primeira linha.** A pessoa reconhece que é
  continuação de algo que **ela** abriu antes de ler o resto. Mensagem que
  começa com saudação genérica parece abordagem fria.
- **Nome de quem respondeu.** Mensagem de pessoa, não de robô anônimo.
- **Sem link encurtado.** Encurtador é marcador clássico de spam; use o
  domínio do portal, sempre o mesmo.
- **Sem emoji promocional, sem "🔥", sem caixa alta.** Nada que se pareça com
  marketing — a categoria "marketing" é a que gera denúncia.
- **Convite explícito a responder.** Conversa de mão dupla é sinal forte de
  conta legítima; monólogo é sinal de disparo em massa.
- **Nunca escrever para quem não iniciou.** Só perfis com
  `telefone_normalizado` casado e conversa prévia — o que a
  `resolver_perfil_por_telefone` já garante.
- **Saída fácil.** Uma linha de "responda SAIR para não receber mais avisos
  por aqui" na primeira notificação de cada pessoa, honrada de verdade
  (coluna de opt-out no perfil). Quem pode sair não denuncia.

### 3.3 Agenda e aquecimento

1. **Chip dedicado**, corporativo, nunca o número principal — e nunca um
   número que já foi usado para disparo em massa.
2. **Antes de qualquer automação:** WhatsApp Business instalado, foto,
   descrição, horário comercial; 3–5 dias de conversa manual real com colegas.
3. **Aquecimento:** semana 1 ≤ 20 mensagens/dia; semana 2 ≤ 50; semana 3 ≤
   100; só depois o volume alvo. Sempre com pessoas que **respondem** —
   resposta recebida vale mais que qualquer truque.
4. **Agenda:** peça a cada funcionário/representante para salvar o número nos
   contatos (comunicado interno + QR de contato no onboarding). Estar na
   agenda derruba a probabilidade de denúncia e é o que mais protege o número.
5. **Anúncio institucional antes de ligar:** e-mail interno dizendo qual é o
   número, o que ele manda e que ninguém vai pedir senha por ali. Também é
   defesa contra phishing usando o nome da empresa.
6. **Sem grupos, sem listas de transmissão, sem mídia não solicitada.**

---

## 4. Eixo 4 — Mapeamento de contratos

### 4.1 Webhook de entrada

Meta (`entry[].changes[].value.messages[]`) → wuzapi (evento por mensagem):

| Dado | Meta Cloud API | WUZAPI |
|---|---|---|
| Envelope | `{"object":"whatsapp_business_account","entry":[{"changes":[{"value":{...}}]}]}` | `{"type":"Message","event":{"Info":{…},"Message":{…}},"token":"…"}` |
| Id da mensagem | `messages[].id` (`wamid.HBg…`) | `event.Info.ID` (32 hex) |
| Remetente | `messages[].from` (`5551994105691`) | `event.Info.Sender` (`5551994105691:12@s.whatsapp.net`) |
| Nome do contato | `contacts[].profile.name` | `event.Info.PushName` |
| Direção | só recebidas | `event.Info.IsFromMe` (filtrar) |
| Grupo | não se aplica | `event.Info.IsGroup` (filtrar) |
| Texto | `type:"text"` → `text.body` | `Message.conversation` **ou** `Message.extendedTextMessage.text` |
| Imagem | `type:"image"` → `image.id`, `image.caption` | `Message.imageMessage` → `url`+`mediaKey`+`fileEncSHA256`… , `caption` |
| Documento | `type:"document"` → `document.id`, `document.filename` | `Message.documentMessage` → idem + `fileName` |
| Documento com legenda | igual ao documento | **`documentWithCaptionMessage.message.documentMessage`** (envelope!) |
| Assinatura | `X-Hub-Signature-256: sha256=<hex>` (HMAC do body com App Secret) | `x-hmac-signature` (HMAC-SHA256 com `WUZAPI_GLOBAL_HMAC_KEY`) |
| Formato | JSON sempre | `WEBHOOK_FORMAT=json` (cru) ou `form` (`jsonData=…&token=…`) — **use `json`** |
| Reentrega | at-least-once da Meta | `WEBHOOK_RETRY_*` |

Armadilhas tratadas em
[`rota_webhook_wuzapi.py`](wuzapi/rota_webhook_wuzapi.py):

- **JID → telefone:** cortar em `@`, remover o sufixo de device (`:12`).
  Grupo (`@g.us`), canal (`@newsletter`) e o identificador anônimo novo
  (`@lid`) **não são telefone** — no caso de `@lid`, tentar `Info.SenderAlt` e
  depois `Info.Chat`. A normalização de fato (DDI, nono dígito) continua sendo
  da função SQL `normalizar_telefone_br()`, fonte única do repo.
- **Envelopes:** `ephemeralMessage`, `viewOnceMessage*` e
  `documentWithCaptionMessage` embrulham a mensagem real — sem desembrulhar,
  o PDF com legenda (caso mais comum do RH) some.
- **Ruído:** `reactionMessage` e `protocolMessage` (editar/apagar) são
  descartados antes de virar linha no banco.

### 4.2 Envio

| Operação | Meta | WUZAPI |
|---|---|---|
| Texto | `POST /{phone_id}/messages` `{"type":"text","text":{"body":…}}` | `POST /chat/send/text` `{"Phone":…,"Body":…}` |
| Documento | `{"type":"document","document":{"link":"https://…","filename":…}}` — a Meta baixa a URL | `POST /chat/send/document` `{"FileName":…,"Document":"data:application/pdf;base64,…"}` — **só base64** |
| Imagem | `{"type":"image","image":{"link":…}}` | `POST /chat/send/image` `{"Image":"data:image/jpeg;base64,…","Caption":…}` |
| Presença | não existe | `POST /chat/presence` |
| Lida | `{"status":"read","message_id":…}` | `POST /chat/markread` |
| Auth | `Authorization: Bearer <token>` | `Token: <token do usuário>` |
| Resposta | `{"messages":[{"id":"wamid…"}]}` | `{"code":200,"success":true,"data":{"Id":…,"Timestamp":…}}` — **pode vir 200 com `success:false`** |
| Janela 24h | obrigatória (fora dela, template aprovado) | não existe |
| Template com botões | template aprovado pela Meta | `POST /chat/send/template` (botões nativos, sem aprovação) |

Como os formulários em branco são estáticos da própria app
(`app/static/formularios/…`), o `WuzapiClient` **lê o arquivo do disco** em
vez de baixar da própria URL pública — com verificação de path traversal — e
só cai para HTTP se a URL for externa.

### 4.3 Mídia recebida

A Meta dá um `media_id` opaco (dois GETs para baixar). O wuzapi manda o
descritor de criptografia no webhook e expõe o download:

```bash
curl -X POST http://wuzapi:8080/chat/downloaddocument \
  -H "Token: $WUZAPI_TOKEN" -H "Content-Type: application/json" \
  -d '{"Url":"https://mmg.whatsapp.net/d/f/…","Mimetype":"application/pdf",
       "FileSHA256":"…","FileEncSHA256":"…","MediaKey":"…","FileLength":2039}'
```

(`/chat/downloadimage`, `/downloadaudio`, `/downloadvideo` são análogos.)

Para não precisar de migration, o descritor é serializado num token opaco
`wuz:<base64url(json)>` guardado na coluna `midia_id` (text) — `token_midia()`
na ida, `baixar_midia()` na volta, escolhendo o endpoint pelo mimetype e
respeitando `whatsapp_intake_midia_max_bytes` duas vezes (pelo `FileLength`
declarado, antes de baixar, e pelo binário decodificado). Daí em diante o
fluxo é o de hoje: OpenAI Vision e Supabase Storage não mudam.

Rodar o wuzapi com **`-skipmedia`** é deliberado: sem a flag, ele baixa e
embute o binário em base64 no próprio webhook (`base64`, `mimeType`,
`fileName` no nível de cima) — uma foto de 5 MB vira ~6,7 MB de JSON por
evento, que teria de ser persistido na hora, sendo que o intake só busca a
imagem quando o modelo decide que precisa dela.

---

## 5. Eixo 5 — Arquitetura no FastAPI

```
app/routes/whatsapp.py  (Meta)  ─┐
app/routes/wuzapi.py    (wuzapi)─┴─> whatsapp_intake.receber_mensagem_normalizada()
                                          │
                                          ├─ whatsapp_conversas / mensagens_recebidas (inalterado)
                                          └─ app/whatsapp_client.py
                                                 get_client() → MetaClient | WuzapiClient
                                                        ▲
                            enviar_mensagem_texto / enviar_documento / baixar_midia
                            (mesmos nomes e assinaturas de hoje)
```

A escolha de design que mais economiza trabalho: **preservar as três funções
de módulo**. `app/ia/whatsapp_intake.py` importa
`from app.whatsapp_client import enviar_mensagem_texto` em quatro lugares — com
as assinaturas mantidas, essas linhas não mudam, e o intake (3.400 linhas, com
suíte de testes adversariais) fica fora do raio da migração. O `Protocol`
`WhatsAppClient` fica por baixo, para as capacidades novas (`presenca`,
`marcar_lida`) e para os testes.

Arquivos prontos: [`whatsapp_client.py`](wuzapi/whatsapp_client.py),
[`rota_webhook_wuzapi.py`](wuzapi/rota_webhook_wuzapi.py),
[`notificacoes_whatsapp.py`](wuzapi/notificacoes_whatsapp.py),
[`docker-compose.yml`](wuzapi/docker-compose.yml),
[`backup_wuzapi.sh`](wuzapi/backup_wuzapi.sh),
[`0091_…sql`](wuzapi/0091_whatsapp_notificacoes_fila.sql),
[`env.example`](wuzapi/env.example) e as edições em
[`PATCHES.md`](wuzapi/PATCHES.md).

---

## 6. Runbook de pareamento (primeira subida)

No Railway — que é onde este piloto vai rodar — use
[`docs/wuzapi/railway/README.md`](wuzapi/railway/README.md) em vez do
`docker compose` abaixo: os comandos mudam (`railway ssh` no lugar do túnel
SSH, Custom Start Command no lugar do `command:` do compose), mas a lógica —
volume antes de tudo, nunca porta pública, QR só depois da rede privada
responder — é a mesma. O runbook a seguir fica como referência para um
deploy self-hosted (VPS/servidor próprio).

```bash
# 1. segredos
openssl rand -hex 24  # WUZAPI_ADMIN_TOKEN
openssl rand -hex 24  # WUZAPI_TOKEN (usuário)
openssl rand -hex 32  # WUZAPI_WEBHOOK_HMAC_KEY e WUZAPI_ENCRYPTION_KEY

# 2. sobe a stack
docker compose -f docs/wuzapi/docker-compose.yml --env-file docs/wuzapi/.env up -d

# 3. cria o usuário (uma vez; fica em users.db)
curl -X POST http://127.0.0.1:8081/admin/users \
  -H "Authorization: $WUZAPI_ADMIN_TOKEN" -H "Content-Type: application/json" \
  -d '{"name":"portal-chamados","token":"'"$WUZAPI_TOKEN"'",
       "webhook":"https://portal.bondmann.com.br/api/webhooks/wuzapi",
       "events":"Message"}'

# 4. conecta e assina só o evento necessário
curl -X POST http://127.0.0.1:8081/session/connect \
  -H "Token: $WUZAPI_TOKEN" -H "Content-Type: application/json" \
  -d '{"Subscribe":["Message"],"Immediate":false}'

# 5. QR Code (túnel SSH, nunca porta pública)
#    ssh -L 8081:127.0.0.1:8081 usuario@servidor
curl -s -H "Token: $WUZAPI_TOKEN" http://127.0.0.1:8081/session/qr
# devolve {"QRCode":"data:image/png;base64,…"} — cole no navegador e escaneie
# no celular do chip dedicado (Aparelhos conectados → Conectar aparelho)

# 6. confirma
curl -s -H "Token: $WUZAPI_TOKEN" http://127.0.0.1:8081/session/status
curl -X POST http://127.0.0.1:8081/chat/send/text -H "Token: $WUZAPI_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"Phone":"5551994105691","Body":"teste de pareamento"}'
```

Monitoramento contínuo: um check a cada 5 min em `/session/status`; se
`Connected` ou `LoggedIn` vier `false` por mais de dois ciclos, alerta — é o
sinal de sessão derrubada (ou de banimento) antes de o usuário reclamar.

---

## 7. Checklist anti-bloqueio (operação contínua)

**Antes de ligar**

- [ ] Chip dedicado, WhatsApp Business, perfil completo, nunca o número principal
- [ ] 3–5 dias de conversa manual real antes de qualquer automação
- [ ] Comunicado interno com o número + pedido de salvar na agenda
- [ ] Nenhuma porta do wuzapi exposta na internet (só rede interna/túnel)
- [ ] HMAC do webhook configurado e validado nos dois lados
- [ ] Backup rodando **e** uma restauração ensaiada em ambiente de teste

**No código**

- [ ] `composing` na chegada + renovado durante o LLM + `paused` no fim
- [ ] Atraso de 1–2,5s com jitter antes de cada mensagem
- [ ] `markread` nas mensagens recebidas
- [ ] Fila de notificações com intervalo global, por contato e janela 07h–21h
- [ ] `[Chamado #…]` na primeira linha de toda notificação ativa
- [ ] Sem link encurtado, sem emoji promocional, sem caixa alta
- [ ] Opt-out ("SAIR") honrado de verdade
- [ ] Só escreve para perfil cadastrado (`telefone_normalizado` casado)

**Rotina**

- [ ] Aquecimento: ≤20/dia na semana 1, ≤50 na semana 2, ≤100 na semana 3
- [ ] Alerta em `/session/status` a cada 5 min
- [ ] Acompanhar taxa de resposta: queda súbita costuma ser o primeiro sinal
      de shadowban, antes do bloqueio formal
- [ ] Nunca enviar para número que não iniciou conversa
- [ ] Nunca grupo, lista de transmissão ou mídia não solicitada
- [ ] E-mail segue como canal formal — o WhatsApp é conveniência, e o
      fallback tem que continuar funcionando sem ele

---

## 8. Plano de corte e rollback

| Fase | O que | Critério de saída |
|---|---|---|
| 0 | Aplicar patches com `WHATSAPP_PROVIDER=meta` | Suíte verde; comportamento idêntico ao de hoje |
| 1 | Subir wuzapi, parear chip dedicado, aquecer | 7 dias, sem automação |
| 2 | Piloto: `wuzapi` num departamento (ex.: TI), intake só | 30 dias sem incidente; zero denúncia |
| 3 | Ligar notificações ativas (`WHATSAPP_NOTIFICACAO_ATIVA=true`) | Taxa de resposta estável |
| 4 | Estender aos demais departamentos | — |

**Rollback:** `WHATSAPP_PROVIDER=meta` + restart. As credenciais da Meta
continuam no `.env`, a rota `/api/webhooks/whatsapp` nunca foi removida, e a
rota do wuzapi descarta eventos quando o provedor ativo não é ele (evita
retry infinito durante a volta). Tempo estimado: um restart.

---

## 9. A confirmar na sua instância (não invente, confira)

Três pontos que a documentação pública não fecha e que valem um `curl` cada,
logo depois do pareamento:

1. **Campo do binário em `/chat/downloadX`.** O cliente aceita `Data`,
   `data`, `Base64`, `base64` e `Content`, com ou sem prefixo `data:` — mas
   confirme qual é o da sua versão e simplifique o código depois:
   `curl -X POST …/chat/downloadimage -d @descritor.json | head -c 200`
2. **Codificação do `x-hmac-signature`** (hex ou base64). O validador aceita
   os dois; olhe o header de um evento real e trave num só.
3. **Ponto de montagem real do `dbdata`** na tag da imagem que você fixou
   (`docker compose exec wuzapi ls -la /app/dbdata` — no Railway,
   `railway ssh -s wuzapi` e depois `ls -la /app/dbdata`).
4. **Bind IPv4 vs IPv6 na rede privada do Railway** — `-address 0.0.0.0`
   (default) não fala com um ambiente Railway legado (IPv6-only). Se o
   `curl` do portal para `wuzapi.railway.internal:8080` der timeout, troque
   para `-address ::` no Custom Start Command. Detalhe e teste em
   [`docs/wuzapi/railway/README.md`](wuzapi/railway/README.md#2-confirmar-no-primeiro-deploy-não-presuma).

E uma verificação de produto, não de código: **confirmar com o gestor que o
número usado será um chip dedicado**. Toda a mitigação de risco desta pesquisa
assume isso.

---

## Fontes

- [wuzapi — API.md (referência de endpoints)](https://github.com/asternic/wuzapi/blob/main/API.md)
- [wuzapi — README (variáveis, flags, persistência, Docker)](https://github.com/asternic/wuzapi/blob/main/README.md)
- [wuzapi — repositório](https://github.com/asternic/wuzapi)
- [WhatsApp API Pricing 2026: fim da janela gratuita em outubro](https://blog.peppercloud.com/whatsapp-api-pricing-everything-you-need-to-know/)
- [WhatsApp Business API — preços no Brasil (2026)](https://www.messagecentral.com/blog/whatsapp-business-api-pricing-brazil)
- [WhatsApp API Pricing 2026 — categorias e mudanças](https://www.wati.io/en/blog/whatsapp-api-pricing-guide/)
- [Railway — Private Networking](https://docs.railway.com/private-networking)
- [Railway — Volumes (exclusividade por serviço, sem réplica)](https://docs.railway.com/reference/volumes)
- [Railway — Backups de volume (incremental/copy-on-write)](https://docs.railway.com/reference/backups)
- [Railway — Custom Start Command (substitui o ENTRYPOINT)](https://docs.railway.com/guides/start-command)
- [Railway — CLI (`ssh`, `variable`, `volume`)](https://docs.railway.com/cli)
- [Docker Hub — asternic/wuzapi (tags)](https://hub.docker.com/r/asternic/wuzapi/tags)
