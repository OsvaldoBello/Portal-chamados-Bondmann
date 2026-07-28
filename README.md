# Portal de Chamados, Bondmann Química

Help desk interno da Bondmann Química. Colaboradores abrem chamados para os
setores que têm fila de atendimento (TI, RH e Marketing), e o staff de cada
setor atende, responde e conduz o chamado até fechar, com SLA, histórico
auditável e avaliação (CSAT) do autor no final.

> Arquitetura, decisões de engenharia e schema ficam no
> [`plano_mestre_desenvolvimento.md`](plano_mestre_desenvolvimento.md). Este
> README é só o resumo operacional. Se a dúvida for sobre *por que* uma decisão
> foi tomada, ou sobre o estado de implementação de cada fase, é lá que está
> (Seção 7 e a Tabela de Estado de Implementação).

---

## Stack

| Camada | Tecnologia |
|---|---|
| Backend | Python 3.12 + FastAPI (ASGI), Uvicorn |
| Dados | Supabase (PostgreSQL + RLS, Auth, Storage) |
| Acesso a dados | `asyncpg` (domínio, via Supavisor *transaction mode*) + `supabase-py` async (Auth/Storage) |
| Frontend | Server-rendered com Jinja2 + HTMX 2.0 (fragmentos), Alpine.js (build CSP), Tailwind CSS (build CLI) |
| Testes | `pytest` + `pytest-asyncio`, contra Supabase local (RLS real, sem mocks) |
| Deploy | Docker no Railway (alvo único de produção, decisão de 2026-07-15) |

O padrão de página é simples: o FastAPI decide tudo, e o navegador só exibe e
dispara requisições HTMX. Nenhuma regra de negócio roda no cliente. O Alpine.js
cuida de estado efêmero de UI, como abrir e fechar modal ou alternar aba, e
nunca guarda dado de domínio (lista de chamados, status, conteúdo de mensagem).

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

O isolamento não é por "empresa cliente", já que o portal é de uso interno e não
tem multi-tenant externo. Ele é por departamento de destino do chamado:

- Funcionário (papel CLIENTE) vê e avalia apenas os chamados que abriu.
- Staff (OPERADOR ou ADMIN) de um setor com fila, ou seja, TI, RH e Marketing,
  atende só os chamados do próprio setor, mais os que ele mesmo abriu.
- Líder de setor, que é o ADMIN com `departamento_id` mesmo em setor sem fila,
  enxerga em modo leitura os chamados abertos pela sua equipe, inclusive os
  destinados a outro setor. Ele não atende fora do setor dele.
- Notas internas (`is_interna = true`) nunca chegam ao autor do chamado.

Essa matriz inteira é aplicada por Row Level Security no Postgres, e não em
código Python. Ver [Segurança](#segurança) abaixo.

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

Os testes de RLS e isolamento rodam contra um Supabase local de verdade
(`supabase start`), impersonando cada papel com `SET LOCAL ROLE authenticated` e
`set_config('request.jwt.claims', ...)`, que é o mesmo mecanismo que o app usa em
produção. Nada que toque RLS é testado com o client Supabase mockado.

Lógica pura, como a matemática de SLA, a geração do código do chamado e a
formatação de data e fuso, tem unit test com mock. Cada teste roda dentro de uma
transação com rollback, então não há efeito colateral entre testes e nenhum
teste muta staging ou produção.

## Boas práticas adotadas neste repositório

- Toda dependência é *pinned* em `requirements.txt` e `pyproject.toml`, sem
  `>=` solto. Trocar de versão é decisão explícita, não efeito colateral de um
  `pip install`.
- Migrations seguem o formato `supabase/migrations/NNNN_descrição.sql` e não são
  editadas depois de aplicadas. Correção vira uma migration nova (`_fix_...`),
  o que mantém o histórico do schema reproduzível em qualquer ambiente: local,
  staging ou produção.
- Decisões de arquitetura, contradições resolvidas entre spec e briefing e
  lacunas preenchidas ficam registradas no `plano_mestre_desenvolvimento.md` com
  marcação explícita (`[DECISÃO DE ENGENHARIA]`, `⚠️ A VALIDAR`). Nada de
  combinado tácito que só existe na cabeça de quem escreveu o código.
- TDD adaptado: nenhuma rota ou função de lógica não-trivial é considerada
  pronta sem suíte verde no `pytest`.
- Nada de lógica de negócio no cliente. O backend decide, HTMX e Alpine só
  reagem ao que o servidor manda.
- Commits e PRs por escopo: migration, backend e frontend de uma mesma feature
  andam juntos, e mudanças não-relacionadas não entram no mesmo commit.

## Segurança

Resumo da Seção 3 do plano mestre. O racional completo de cada item está lá.

### Autenticação e sessão

A autenticação é delegada ao Supabase Auth (GoTrue): login, hash e verificação
de senha, refresh e recuperação de senha (`/esqueci-senha`, OTP por e-mail,
`/redefinir-senha`) são responsabilidade do Supabase, não reimplementados aqui.
Ver o [planejamento de melhoria do hashing](#próximo-passo-planejado-hashing-de-senha)
abaixo.

- Não existe signup público. Colaboradores são criados direto no Supabase
  (Authentication → Users), e a promoção de papel e departamento é feita por SQL
  (`supabase/registro_usuarios.sql`).
- O access token JWT vai em cookie `httpOnly + Secure + SameSite=Lax` e o
  refresh token em um cookie separado `httpOnly + Secure + SameSite=Strict`.
  Nunca em `localStorage`, nunca como `Authorization: Bearer` no browser.
- O JWT é verificado localmente a cada request (JWKS RS256/ES256, com fallback
  HS256 legado), o que evita bater na API do Supabase a cada chamada.

### Isolamento de dados (RLS)

- Row Level Security está habilitado em todas as tabelas de domínio. A
  `service_role` key, que bypassa RLS, nunca serve dado de usuário: fica
  restrita a jobs administrativos auditados e jamais chega ao browser.
- O domínio é acessado via `asyncpg` sob Supavisor *transaction mode*, com os
  claims injetados por transação (`SET LOCAL` e
  `set_config('request.jwt.claims', ...)`). Assim as políticas de RLS enxergam o
  usuário autenticado mesmo com connection pooling no meio.
- Defesa em profundidade: mesmo com RLS ativo, as queries continuam filtrando
  explicitamente por `departamento_id` e `cliente_id`.
- O cache em memória é escopado por tenant e setor por construção. A chave de
  cache sempre inclui o escopo, para não vazar dado entre departamentos.

### Entrada e transporte

- CSRF com double-submit cookie e header `X-CSRF-Token`, assinado com
  `itsdangerous` e validado em toda mutação HTMX (`POST/PUT/PATCH/DELETE`).
- CSP estrita, sem `unsafe-eval` nem `unsafe-inline` em `script-src`. É por isso
  que o Alpine.js roda no build CSP, que não usa `eval` nem `Function`.
- HSTS, `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff` e um
  `Referrer-Policy` restritivo aplicados por middleware em toda resposta.
- Validação de input com Pydantic em todo corpo, query e form. O autoescape do
  Jinja2 fica sempre ligado e o SQL é 100% parametrizado, sem concatenação.
- Rate limiting (`slowapi`) em `/login` e na abertura de chamado, com o IP real
  extraído de `X-Forwarded-For`, já que a aplicação fica atrás do proxy do
  Railway.

### Upload de anexos

- O bucket do Storage é privado, nunca público. O acesso é por signed URL com
  TTL de 1h, regenerada a cada renderização e nunca cacheada além do TTL.
- Limite de 10MB por arquivo, allow-list de tipos e validação do MIME real por
  magic bytes (`python-magic`). O `Content-Type` e a extensão enviados pelo
  cliente não são levados a sério.
- O nome do arquivo é sempre sanitizado ou gerado, sem espaço para path
  traversal.

### Segredos

`SUPABASE_URL`, `anon key`, `service_role key`, `DATABASE_URL` e os segredos de
JWT ficam em variáveis de ambiente (Pydantic Settings). O `.env` nunca é
commitado.

---

## Próximo passo planejado: hashing de senha

O plano detalhado está na seção separada desta entrega. Em resumo: hoje o
hashing de senha não é código deste repositório. Quem faz é o GoTrue (Supabase
Auth), que usa bcrypt internamente. O plano avalia se e onde vale reforçar isso,
mexendo em parâmetros do GoTrue, política de senha e MFA, contra a alternativa
de implementar hashing próprio. Esse segundo cenário só passa a ser necessário
se algum fluxo começar a armazenar credencial fora do Supabase Auth.
