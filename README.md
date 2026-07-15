# Portal de Chamados — Bondmann Química

Help desk interno da Bondmann Química: colaboradores abrem chamados para os
setores com fila de atendimento (**TI**, **RH**, **Marketing**), e o staff de
cada setor atende, responde e conduz o chamado até a resolução, com SLA,
histórico auditável e avaliação (CSAT) do autor.

> A fonte de verdade de arquitetura, decisões de engenharia e schema é o
> [`plano_mestre_desenvolvimento.md`](plano_mestre_desenvolvimento.md). Este
> README é um resumo operacional — qualquer dúvida mais profunda sobre *por
> que* uma decisão foi tomada, ou o estado de implementação de cada fase, está
> lá (Seção 7 / Tabela de Estado de Implementação).

---

## Stack

| Camada | Tecnologia |
|---|---|
| Backend | Python 3.12 + FastAPI (ASGI), Uvicorn |
| Dados | Supabase (PostgreSQL + RLS, Auth, Storage) |
| Acesso a dados | `asyncpg` (domínio, via Supavisor *transaction mode*) + `supabase-py` async (Auth/Storage) |
| Frontend | Server-rendered com Jinja2 + HTMX 2.0 (fragmentos), Alpine.js (build CSP), Tailwind CSS (build CLI) |
| Testes | `pytest` + `pytest-asyncio`, contra Supabase local (RLS real, sem mocks) |
| Deploy | Docker (Railway — alvo único de produção, decisão 2026-07-15) |

Padrão de página: o FastAPI é a única fonte de verdade — o navegador só exibe
e dispara requisições HTMX; nenhuma regra de negócio roda no cliente. Alpine.js
cuida apenas de estado efêmero de UI (abrir/fechar modal, toggle de aba); nunca
guarda dados de domínio (lista de chamados, status, conteúdo de mensagem).

## Estrutura do projeto

```
app/
  auth/            sessão, dependências de autenticação, client Supabase
  domain/           lógica de negócio pura (ex.: feriados p/ cálculo de SLA)
  repositories/      acesso a dados por domínio (admin, chamados)
  routes/            rotas HTTP (portal, workspace, admin, perfil, common)
  security/          CSRF, headers, JWT, validação de upload
  static/            JS e assets versionados
  templates/         Jinja2 (portal, workspace, admin) + fragmentos HTMX
supabase/
  migrations/        histórico de migrations SQL (schema + RLS), ordem numérica
tests/               pytest, um arquivo por área
```

## Modelo de acesso (quem vê o quê)

O isolamento não é por "empresa cliente" (o portal é uso interno, sem
multi-tenant externo) — é por **departamento** de destino do chamado:

- **Funcionário (CLIENTE):** vê e avalia apenas os chamados que abriu.
- **Staff (OPERADOR/ADMIN) de um setor com fila** (TI/RH/Marketing): vê e
  atende só os chamados do próprio setor, além dos que ele mesmo abriu.
- **Líder de setor** (ADMIN com `departamento_id`, mesmo em setor sem fila):
  enxerga em modo leitura os chamados abertos pela sua equipe, mesmo que
  destinados a outro setor — não atende fora do seu setor.
- **Notas internas** (`is_interna = true`) nunca chegam ao autor do chamado.

Toda essa matriz é aplicada via **Row Level Security no Postgres**, não em
código Python — ver [Segurança](#segurança) abaixo.

## Rodando localmente

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows
pip install -r requirements.txt -r requirements-dev.txt

supabase start                  # stack Supabase local (Postgres + Auth + Storage)
# aplica as migrations em supabase/migrations/ na ordem numérica

copy .env.example .env          # preencher SUPABASE_URL, chaves, DATABASE_URL etc.
uvicorn app.main:app --reload --port 8080
```

Build do CSS (Tailwind, purge via `tailwind.config.js`):

```bash
npm install
npm run build:css               # ou o script equivalente em package.json
```

## Testes

```bash
pytest
```

- Testes de RLS/isolamento rodam **contra Supabase local de verdade**
  (`supabase start`), impersonando cada papel via
  `SET LOCAL ROLE authenticated` + `set_config('request.jwt.claims', ...)` —
  o mesmo mecanismo que o app usa em produção. **Não mockamos o client
  Supabase** para nada que toque RLS.
- Lógica pura (matemática de SLA, geração de código do chamado, formatação de
  data/fuso) é coberta por unit tests com mocks.
- Cada teste roda em transação com rollback — sem efeito colateral entre
  testes, e testes nunca mutam staging/produção.

## Boas práticas adotadas neste repositório

- **Versionamento travado:** toda dependência é *pinned* em
  `requirements.txt`/`pyproject.toml` (sem `>=` soltos); mudança de versão é
  decisão explícita, não efeito colateral de um `pip install`.
- **Migrations numeradas e imutáveis:** `supabase/migrations/NNNN_descrição.sql`,
  nunca editadas após aplicadas — correções viram uma nova migration
  (`_fix_...`). Isso mantém o histórico do schema reproduzível em qualquer
  ambiente (local, staging, produção).
- **Documento vivo:** decisões de arquitetura, contradições resolvidas entre
  spec e briefing, e lacunas preenchidas ficam registradas no
  `plano_mestre_desenvolvimento.md` com marcação explícita
  (`[DECISÃO DE ENGENHARIA]`, `⚠️ A VALIDAR`) — nada de "combinado tácito" que
  só existe na cabeça de quem escreveu o código.
- **TDD adaptado:** nenhuma rota ou função de lógica não-trivial é
  considerada pronta sem suíte verde em `pytest`.
- **Sem lógica de negócio no cliente:** o backend é a única fonte de verdade;
  HTMX/Alpine só reagem ao que o servidor manda.
- **Commits e PRs por escopo:** mudanças de schema (migration), backend e
  frontend relacionadas a uma mesma feature andam juntas; mudanças
  não-relacionadas não são misturadas no mesmo commit.

## Segurança

Resumo da Seção 3 do plano mestre — cada item ali tem o racional completo.

### Autenticação e sessão
- Autenticação delegada ao **Supabase Auth (GoTrue)** — login, hash/verificação
  de senha, refresh e recuperação de senha (`/esqueci-senha` → OTP por e-mail →
  `/redefinir-senha`) são responsabilidade do Supabase, não reimplementados
  no app. Ver [planejamento de melhoria do hashing](#próximo-passo-planejado--hashing-de-senha) abaixo.
- **Sem signup público:** colaboradores são criados direto no Supabase
  (Authentication → Users); a promoção de papel/departamento é feita por SQL
  (`supabase/registro_usuarios.sql`).
- **Cookies de sessão:** access token JWT em cookie `httpOnly + Secure +
  SameSite=Lax`; refresh token em cookie separado `httpOnly + Secure +
  SameSite=Strict`. Nunca em `localStorage` nem `Authorization: Bearer` no
  browser.
- **Verificação de JWT local por request** (JWKS RS256/ES256, com fallback
  HS256 legado) — evita validar contra a API do Supabase a cada chamada.

### Isolamento de dados (RLS)
- **Row Level Security habilitado em todas as tabelas de domínio.** A
  `service_role` key (que bypassa RLS) **nunca** serve dado de usuário —
  fica restrita a jobs administrativos auditados e jamais chega ao browser.
- Domínio acessado via `asyncpg` sob Supavisor *transaction mode*, com claims
  injetados por transação (`SET LOCAL` + `set_config('request.jwt.claims', ...)`)
  — as políticas de RLS enxergam o usuário autenticado mesmo sob connection
  pooling.
- **Defesa em profundidade:** mesmo com RLS ativo, queries também filtram
  explicitamente por `departamento_id`/`cliente_id`.
- **Cache em memória é tenant/setor-scoped** por construção — chave de cache
  sempre inclui o escopo, para não vazar dado entre departamentos.

### Entrada e transporte
- **CSRF:** double-submit cookie + header `X-CSRF-Token`, assinado com
  `itsdangerous`, validado em toda mutação HTMX (`POST/PUT/PATCH/DELETE`).
- **CSP estrita** sem `unsafe-eval`/`unsafe-inline` em `script-src` — por
  isso Alpine.js roda no **build CSP** (sem `eval`/`Function`).
- **HSTS, `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`,
  `Referrer-Policy` restritiva** aplicados via middleware em toda resposta.
- **Validação de input via Pydantic** em todo corpo/query/form; autoescape do
  Jinja2 sempre ligado; **SQL 100% parametrizado** (sem concatenação).
- **Rate limiting (`slowapi`)** em `/login` e abertura de chamado, com IP real
  extraído de `X-Forwarded-For` (atrás do proxy Railway).

### Upload de anexos
- Bucket do Storage **privado**, nunca público; **signed URLs com TTL de 1h**,
  regeneradas a cada renderização (nunca cacheadas além do TTL).
- Limite de **10MB** por arquivo, allow-list de tipos, e **validação do MIME
  real por magic bytes** (`python-magic`) — não confia em `Content-Type`/
  extensão enviados pelo cliente.
- Nome de arquivo sempre sanitizado/gerado (sem path traversal).

### Segredos
- `SUPABASE_URL`, `anon key`, `service_role key`, `DATABASE_URL` e segredos de
  JWT ficam em variáveis de ambiente (Pydantic Settings). `.env` nunca é
  commitado.

---

## Próximo passo planejado — hashing de senha

Ver plano detalhado abaixo (seção separada nesta entrega). Resumo: hoje o
hashing de senha **não é código deste repositório** — é feito pelo GoTrue
(Supabase Auth), que usa bcrypt internamente. O plano avalia se/onde vale a
pena reforçar isso (parâmetros do GoTrue, política de senha, MFA) versus
implementar hashing próprio (cenário só necessário se algum fluxo passar a
armazenar credencial fora do Supabase Auth).
