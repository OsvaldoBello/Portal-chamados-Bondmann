# WUZAPI no Railway — runbook de deploy (Fase 1)

Serviço novo no **mesmo projeto Railway** do portal, sem Dockerfile próprio:
Railway builda o portal a partir do repositório (como já faz hoje) e o wuzapi
a partir de uma **imagem pronta do Docker Hub**. Os dois falam entre si pela
rede privada do projeto — o wuzapi nunca ganha domínio público.

```
Projeto Railway
├── portal-chamados   (existente — build do Dockerfile do repo)
└── wuzapi            (novo — imagem asternic/wuzapi, sem domínio público)
        │
        └── Volume (1) → /app/dbdata  (sessão do whatsmeow)
```

Isso é a continuação da Fase 0 (`feat/whatsapp-provider-wuzapi`) — o adapter,
o webhook e as variáveis já existem no código. Este runbook só cobre o que é
específico do Railway: nenhuma linha de `app/` muda aqui.

---

## 0. O que foi confirmado direto na documentação do Railway/wuzapi

Para não repetir suposição como fato, o que segue foi checado, não chutado:

- `-datadir` do wuzapi, quando vazio, resolve para `<diretório do executável>/dbdata`. Como o binário mora em `/app/wuzapi` (`WORKDIR /app` do Dockerfile oficial), **o default já é `/app/dbdata`** — não precisa passar `-datadir` na mão, só apontar o volume pra lá.
- `-port` default `8080`, `-address` default `0.0.0.0`.
- **Custom Start Command do Railway substitui o `ENTRYPOINT` inteiro** (não só acrescenta argumentos ao `CMD`) — então o comando customizado precisa ser a chamada completa do binário, não só as flags extras.
- Volume do Railway é **exclusivo de um serviço** (não é um bind mount compartilhável como no `docker-compose.yml` genérico desta pasta) e **não pode ser usado com réplicas** — nunca escale este serviço horizontalmente.
- Railway oferece **backup nativo de volume** (incremental, copy-on-write — mesma garantia de consistência de um snapshot de bloco), com agendamento diário/semanal/mensal pela própria dashboard.
- `railway ssh` abre shell **dentro do container já implantado** (não é um túnel do seu laptop pra rede privada — isso não existe no Railway hoje). É o caminho usado abaixo para o QR Code, sem nunca expor porta pública.
- A imagem runtime do wuzapi tem `curl`, mas **não tem o binário `sqlite3`** — o `docs/wuzapi/backup_wuzapi.sh` (que depende dele) não roda dentro deste container. Ver seção 6.

---

## 1. Criar o serviço (dashboard)

1. No projeto do portal → **New** → **Docker Image**.
2. Imagem: `asternic/wuzapi:sha-9487eca` — **não use `latest`**, mesmo motivo do `Dockerfile` do portal fixar a versão do Python/Node. `sha-9487eca` é o que a tag `latest` apontava na data desta pesquisa (2026-09); confira o hash atual em [hub.docker.com/r/asternic/wuzapi/tags](https://hub.docker.com/r/asternic/wuzapi/tags) antes de colar — a tag muda a cada release.
3. Nome do serviço: **`wuzapi`** (é o nome que vira o hostname da rede privada, `wuzapi.railway.internal` — mudar o nome depois muda o hostname).
4. **Não clique em "Generate Domain".** Sem domínio público é o estado padrão; é isso que queremos.

### Custom Start Command

Settings → Deploy → **Custom Start Command**:

```
/app/wuzapi -logtype json -color=false -skipmedia
```

Substitui o `ENTRYPOINT` da imagem (`--logtype=console --color=true`) por
logs em JSON (mesmo padrão do resto da stack) e liga `-skipmedia` — decisão
já justificada em `docs/pesquisa_wuzapi_migracao.md` (Eixo 4.3): sem ela, uma
foto de 5 MB vira ~6,7 MB de JSON por webhook.

### Variáveis de ambiente

Settings → Variables → **Raw Editor**, cole:

```env
WUZAPI_ADMIN_TOKEN=<openssl rand -hex 24>
WUZAPI_GLOBAL_HMAC_KEY=<openssl rand -hex 32>
WUZAPI_GLOBAL_ENCRYPTION_KEY=<openssl rand -hex 32>
WEBHOOK_FORMAT=json
SESSION_DEVICE_NAME=Portal Chamados Bondmann
TZ=America/Sao_Paulo
```

`WUZAPI_GLOBAL_HMAC_KEY` aqui é a mesma string que vai virar
`WUZAPI_WEBHOOK_HMAC_KEY` no portal (seção 3) — o wuzapi assina com ela, o
portal confere com ela.

Equivalente via CLI (depois de `railway link` apontando pro serviço `wuzapi`
deste projeto):

```bash
railway variable set WUZAPI_ADMIN_TOKEN=$(openssl rand -hex 24) -s wuzapi
railway variable set WUZAPI_GLOBAL_HMAC_KEY=$(openssl rand -hex 32) -s wuzapi
railway variable set WUZAPI_GLOBAL_ENCRYPTION_KEY=$(openssl rand -hex 32) -s wuzapi
railway variable set WEBHOOK_FORMAT=json SESSION_DEVICE_NAME="Portal Chamados Bondmann" TZ=America/Sao_Paulo -s wuzapi
```

### Volume

Settings → Volumes → **New Volume** → mount path `/app/dbdata`.

CLI: `railway volume add --mount-path /app/dbdata -s wuzapi`

É este volume — não o serviço, não o build — que garante que um redeploy não
peça QR Code de novo (ver Eixo 1 da pesquisa).

### Rede privada / porta

Se o Railway pedir explicitamente uma porta para a rede privada (campo de
"Networking"/"Private Port" nas Settings — a imagem não declara `EXPOSE`,
então pode não autodetectar), informe **8080**.

### Restart policy e região

- **Restart Policy: On Failure.**
- **Nunca ative réplicas** neste serviço (ver seção 0 — volume não suporta).
- Região: de preferência a mesma do serviço `portal-chamados`, para a rede
  privada não atravessar região à toa.

---

## 2. Confirmar no primeiro deploy (não presuma)

```bash
railway logs -s wuzapi
```

Três coisas para checar, nesta ordem:

1. **Logs saindo em JSON** (prova que o Custom Start Command pegou, não o
   `ENTRYPOINT` original).
2. **Volume montado** — `railway ssh -s wuzapi` e depois `ls -la /app/dbdata`
   dentro do shell: deve existir e estar vazio (primeiro boot) ou com
   `main.db`/`users.db` (boot seguinte).
3. **Rede privada respondendo.** Do serviço `portal-chamados`:
   ```bash
   railway ssh -s portal-chamados
   curl -sS -m 5 http://wuzapi.railway.internal:8080/session/status
   ```
   Sem token dá 401 — **o que importa aqui é não dar timeout/connection
   refused.** Se der, o candidato nº 1 é o bind: `-address 0.0.0.0` é
   IPv4-only, e ambientes Railway criados antes de 16/10/2025 exigem bind em
   `::` (IPv6) para a rede privada funcionar. Ambientes novos aceitam os
   dois; se o seu não aceitar, ajuste o Custom Start Command:
   ```
   /app/wuzapi -logtype json -color=false -skipmedia -address ::
   ```
   e repita o teste.

---

## 3. Ligar o portal ao wuzapi

No serviço `portal-chamados`, acrescentar (ainda com
`WHATSAPP_PROVIDER=meta` — só liga na Fase 2, depois do pareamento e do
aquecimento):

```env
WUZAPI_BASE_URL=http://wuzapi.railway.internal:8080
WUZAPI_TOKEN=<o token do usuário, criado no passo 4>
WUZAPI_WEBHOOK_HMAC_KEY=<a MESMA WUZAPI_GLOBAL_HMAC_KEY do passo 1>
```

`WUZAPI_BASE_URL` usando o hostname `.railway.internal` só funciona **de
dentro de outro serviço do mesmo projeto** — é exatamente o que
`app/whatsapp_client.py` (Fase 0) espera: nunca um endereço público.

---

## 4. Criar o usuário do wuzapi e parear o QR Code

Tudo via `railway ssh` — o shell abre **dentro do container do wuzapi**, então
os `curl` abaixo usam `localhost`, nunca o hostname da rede privada nem uma
porta pública:

```bash
railway ssh -s wuzapi
```

Dentro do shell:

```bash
# 1. cria o usuário (uma vez; fica em users.db, sobrevive a redeploy)
curl -s -X POST http://localhost:8080/admin/users \
  -H "Authorization: $WUZAPI_ADMIN_TOKEN" -H "Content-Type: application/json" \
  -d '{"name":"portal-chamados","token":"'"$(openssl rand -hex 24)"'",
       "webhook":"https://<domínio-público-do-portal>/api/webhooks/wuzapi",
       "events":"Message"}'
# guarde o token retornado — é o WUZAPI_TOKEN do passo 3

# 2. conecta a sessão, assinando só o evento necessário
curl -s -X POST http://localhost:8080/session/connect \
  -H "Token: <o token acima>" -H "Content-Type: application/json" \
  -d '{"Subscribe":["Message"],"Immediate":false}'

# 3. pede o QR Code
curl -s -H "Token: <o token acima>" http://localhost:8080/session/qr
```

O último comando devolve `{"data":{"QRCode":"data:image/png;base64,AAAA..."}}`.
Copie a string inteira (a partir de `data:image/png`) e cole direto na barra
de endereço de um navegador — ela abre como imagem sem precisar decodificar
nada na mão. Escaneie no celular do **chip dedicado**
(WhatsApp → Aparelhos conectados → Conectar aparelho).

Confirme:

```bash
curl -s -H "Token: <o token acima>" http://localhost:8080/session/status
# {"code":200,"data":{"Connected":true,"LoggedIn":true},"success":true}
```

`webhook` no passo 1 aponta para o **domínio público do portal** (o mesmo
onde `/api/webhooks/whatsapp` já responde hoje) — é a Meta/Railway do lado de
fora chamando o portal, direção oposta da rede privada interna.

---

## 5. Monitoramento

Sem domínio público, o healthcheck HTTP nativo do Railway (que assume tráfego
externo) não se aplica bem aqui — deixe-o desligado nas Settings e confie no
**Restart Policy: On Failure** para o container.

O acompanhamento ativo (`GET /session/status` a cada 5 min, alerta por
e-mail se `Connected`/`LoggedIn` vier `false`) está implementado em
`app/services/wuzapi_monitor.py` — task de fundo no mesmo padrão da
reconciliação da triagem (`app/ia/triagem.py::iniciar_reconciliacao`),
registrada no lifespan de `app/main.py`. Kill switch: só roda com
`WUZAPI_BASE_URL`/`WUZAPI_TOKEN` configurados e
`WUZAPI_MONITOR_ALERTA_EMAIL` preenchido; `WUZAPI_MONITOR_INTERVALO_S<=0`
desliga. Só alerta depois de `WUZAPI_MONITOR_FALHAS_PARA_ALERTAR` falhas
**consecutivas** (default 2 — evita e-mail por uma instabilidade de rede de
um ciclo só) e manda um segundo e-mail avisando quando a sessão volta.

Roda independente de `WHATSAPP_PROVIDER` — durante o aquecimento (Fase 1,
provider ainda `meta`) já queremos saber se o pareamento cair, antes de
qualquer automação depender dele. Testes em `tests/test_wuzapi_monitor.py`.

---

## 6. Backup (o que muda em relação ao `docker-compose.yml` genérico)

O `docs/wuzapi/backup_wuzapi.sh` desta pasta foi escrito para o
`docker-compose.yml` de referência (self-hosted): um container **separado**
lendo o **mesmo volume Docker**, com `sqlite3` instalado, rodando
`.backup` a cada 6h. Nenhuma dessas três premissas vale no Railway:

- volume é exclusivo de um serviço — não dá para montar um segundo
  container lendo o mesmo volume;
- a imagem runtime do wuzapi não tem o binário `sqlite3` (só o driver Go
  embutido no próprio processo);
- não há mecanismo nativo de cron/sidecar simples para rodar isso por conta
  própria sem um terceiro serviço competindo pelo mesmo dado.

Em vez de forçar esse desenho num lugar onde ele não encaixa, use os dois
mecanismos que o Railway já dá nativamente:

**Primário — snapshot automático do volume.** Volume → aba **Backups** →
ative **Daily** (retém 6 dias) e **Weekly** (retém 1 mês). É
incremental/copy-on-write — a mesma garantia de um snapshot de bloco (todos
os arquivos do SQLite, incluindo `-wal`/`-shm`, congelados no mesmo instante),
então é seguro mesmo com o wuzapi escrevendo o tempo todo — diferente de um
`cp` ingênuo, que não tem essa atomicidade. Restauração é um clique
(Backups → escolher a data → **Restore** → **Deploy**); documente aqui quando
fizer o primeiro teste de restauração — **ainda não testado**, e é isso que
transforma "tem backup" em "sabemos restaurar".

**Secundário — cópia fria portátil, antes de mudança arriscada** (troca de
versão da imagem, migração de região). Como não existe forma segura de
copiar um SQLite em WAL vivo sem o `sqlite3 .backup` (que não está
disponível aqui), o procedimento é parar o serviço, baixar os arquivos, e
religar:

```bash
# "parar" um serviço no Railway = escalar a região pra 0 réplicas (não existe
# `railway service stop` nesta CLI — comando verificado em 2026-09,
# v5.47.2; se uma versão futura reintroduzir stop/start, prefira-o).
# A região é a que aparece em `railway status` (aqui, sfo).
railway service scale -s wuzapi sfo=0
railway volume files download /main.db     ./backups/main-$(date +%Y%m%d).db     -v wuzapi-volume -s wuzapi --overwrite
railway volume files download /main.db-wal ./backups/main-$(date +%Y%m%d).db-wal -v wuzapi-volume -s wuzapi --overwrite
railway volume files download /main.db-shm ./backups/main-$(date +%Y%m%d).db-shm -v wuzapi-volume -s wuzapi --overwrite
railway volume files download /users.db    ./backups/users-$(date +%Y%m%d).db    -v wuzapi-volume -s wuzapi --overwrite
railway service scale -s wuzapi sfo=1
```

(`-wal`/`-shm` podem não existir se o SQLite tiver feito checkpoint
recentemente — normal, baixe só os arquivos que existirem.)

Os quatro arquivos precisam ser tratados como um conjunto — restaurar só o
`.db` sem o `-wal`/`-shm` da mesma cópia é o mesmo problema descrito no
`backup_wuzapi.sh` original. Não automatize isso em cron: é uma cópia fria,
com o serviço parado por alguns segundos — o backup nativo do Railway é que
deve carregar a rotina diária.

---

## 7. Checklist de saída da Fase 1

- [x] Serviço `wuzapi` criado, sem domínio público
- [x] Custom Start Command aplicado e confirmado nos logs (JSON, não console)
- [x] Volume em `/app/dbdata`, confirmado por `railway status` (montado, 0GB/48.8GB)
- [x] Rede privada respondendo (`curl` do portal para `wuzapi.railway.internal:8080` sem timeout — 192ms)
- [x] `WUZAPI_BASE_URL`/`WUZAPI_TOKEN`/`WUZAPI_WEBHOOK_HMAC_KEY` setados no portal (provider ainda `meta`)
- [x] Usuário criado, sessão pareada com o **chip dedicado**, `LoggedIn: true` — confirmado com envio de mensagem de teste
- [x] Monitor de sessão (`app/services/wuzapi_monitor.py`) implementado e configurado (`WUZAPI_MONITOR_ALERTA_EMAIL`, alerta após 2 falhas seguidas a cada 5 min)
- [x] Backup nativo do volume ativado (Daily + Weekly) e um backup manual disparado (2026-09-02)
- [ ] **Restauração ainda não testada** — fazer antes do fim da Fase 1: restaurar o backup manual de 2026-09-02 num volume de teste (ou confirmar com o Railway que `Restore` não é destrutivo antes de testar no mesmo serviço) e reconferir `session/status` depois
- [ ] Redeploy do serviço testado uma vez, confirmando que a sessão sobrevive sem novo QR

Só depois desse checklist fechado é que faz sentido considerar a Fase 2
(`WHATSAPP_PROVIDER=wuzapi` num departamento piloto) — aquecimento de 7 dias
sem automação antes disso, como já registrado na pesquisa.
