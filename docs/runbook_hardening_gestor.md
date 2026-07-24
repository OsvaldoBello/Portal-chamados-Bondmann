# Runbook — Hardening que depende do gestor

> **Por que este documento existe.** Duas travas de segurança do projeto só podem
> ser fechadas por quem tem **admin no repositório GitHub** e **acesso ao painel do
> Supabase hospedado** — nenhuma delas cabe num PR de código (são configuração de
> plataforma, não arquivo versionado). Este runbook é o passo a passo exato para o
> gestor (Osvaldo) executar as duas, mais o comando `gh`/API equivalente para quem
> preferir automatizar.
>
> Origem: item **3.2** do `plano_melhorias_auditoria.md` (Sprint 3), que consolida
> as pendências `[AÇÃO DO GESTOR]` já registradas nos itens **0.4** (branch
> protection) e **2.8/B6** (política de senha do Supabase).
>
> **Contexto atual (confirmado nesta data):**
> - Repositório: `OsvaldoBello/Portal-chamados-Bondmann` — **público**, branch
>   default **`claude/develop`** (não há branch `main`).
> - Branch protection em `claude/develop`: **ausente** (`gh api .../protection` →
>   `404 Branch not protected`).
> - Política de senha do app: mínimo **8** caracteres, fonte única em
>   `app/security/password_policy.py::SENHA_MIN_CHARS` (NIST 800-63B). O
>   `supabase/config.toml` **local** já está em 8; o projeto **hospedado** segue no
>   default **6**.

---

## (a) Branch Protection em `claude/develop`

**Objetivo:** merge só com **CI verde**, **1 revisão aprovada** e **sem
force-push/deleção** da branch. Hoje qualquer push entra direto — o CI roda, mas
não bloqueia nada (foi exatamente o que deixou o item 0.4 "meio pronto").

### Checks de CI que devem ser exigidos

Os nomes abaixo são o campo `name:` de cada job (workflows em
`.github/workflows/`). Exija **apenas os que rodam em todo PR**:

| Check (nome do job) | Workflow | Exigir? |
|---|---|---|
| `Suíte pytest + cobertura` | `ci.yml` | ✅ sim |
| `Build Tailwind` | `ci.yml` | ✅ sim |
| `Numeração de migrations` | `ci.yml` | ✅ sim |
| `Lint (ruff)` | `ci.yml` | ✅ sim |
| `Type-check (mypy, gradual)` | `ci.yml` | ✅ sim |
| `Matriz de RLS (Supabase local)` | `e2e-rls.yml` | ⚠️ **não** exigir |

> **Por que não exigir o `e2e-rls`:** ele só dispara em PRs que tocam
> `supabase/migrations/**`, `app/repositories/**`, `app/db.py` ou `tests/e2e/**`
> (filtro de `paths` no workflow). Se virar check obrigatório, todo PR que **não**
> toca esses caminhos ficaria eternamente "pendente" nesse check e nunca poderia dar
> merge. Ele continua rodando e visível quando relevante — só não deve ser
> *required*.
>
> **Os nomes acima valem a partir do PR do item 3.1** (que renomeou o job de teste
> para incluir cobertura e adicionou o job de mypy). O jeito à prova de erro de
> digitação: abra um PR qualquer, deixe o CI rodar uma vez, e selecione os checks na
> lista que o GitHub oferece (ele lista os nomes exatos já observados).

### Caminho 1 — pela UI do GitHub (fallback, sem token)

1. Repositório → **Settings** → **Branches** (menu lateral) → **Add branch
   ruleset** (ou **Add classic branch protection rule**).
2. **Branch name pattern:** `claude/develop`.
3. Marque:
   - **Require a pull request before merging** → **Require approvals: 1**.
   - **Require status checks to pass before merging** → **Require branches to be up
     to date before merging** (strict) → adicione os 5 checks da tabela acima.
   - **Do not allow bypassing the above settings** (equivale a `enforce_admins`; ver
     ressalva do mantenedor solo abaixo).
   - **Restrict force pushes** (bloqueia `git push --force`).
   - **Restrict deletions** (ninguém apaga a branch).
4. **Create** / **Save changes**.

### Caminho 2 — via `gh api` (para quem tem admin + token com escopo `repo`)

> O `gh` desta máquina já está autenticado como `OsvaldoBello` (dono do repo =
> admin) com escopo `repo`. O comando abaixo aplica a proteção de uma vez. **Ele
> altera configuração do repositório** — rode com consciência (é reversível: veja
> "Reverter" no fim).

```bash
gh api -X PUT \
  repos/OsvaldoBello/Portal-chamados-Bondmann/branches/claude/develop/protection \
  --input - <<'JSON'
{
  "required_status_checks": {
    "strict": true,
    "contexts": [
      "Suíte pytest + cobertura",
      "Build Tailwind",
      "Numeração de migrations",
      "Lint (ruff)",
      "Type-check (mypy, gradual)"
    ]
  },
  "enforce_admins": true,
  "required_pull_request_reviews": {
    "required_approving_review_count": 1,
    "dismiss_stale_reviews": true
  },
  "restrictions": null,
  "allow_force_pushes": false,
  "allow_deletions": false
}
JSON
```

> **Nota sobre a URL:** a branch tem barra (`claude/develop`) e mesmo assim entra
> literal no path (`.../branches/claude/develop/protection`) — a API do GitHub
> resolve corretamente (confirmado: um `GET` no mesmo path já respondeu
> `404 Branch not protected`, não `Not Found` do repo).

#### ⚠️ Ressalva do mantenedor solo (importante)

`enforce_admins: true` + `required_approving_review_count: 1` significa que **até o
admin precisa de 1 aprovação de outra pessoa** para dar merge — e ninguém aprova o
próprio PR. Se hoje **você é o único mantenedor**, isso te tranca fora do merge.
Duas saídas:

- **Manter a revisão obrigatória, afrouxar só para o admin:** troque para
  `"enforce_admins": false`. CI verde + sem force-push continuam valendo para todos;
  o admin pode fazer o merge sem a 2ª pessoa quando necessário.
- **Sem exigir revisão por ora (só CI + no-force-push):** remova o bloco
  `required_pull_request_reviews` (ou use `"required_pull_request_reviews": null`) e
  mantenha `"enforce_admins": true`. O gate de qualidade (CI) fica firme; a revisão
  entra quando o time crescer.

Escolha uma das duas conforme o tamanho do time hoje. A recomendação para um time de
1 pessoa é a **segunda** (CI obrigatório já resolve 90% do risco que o item 0.4
queria cobrir; revisão obrigatória sem revisor vira só fricção).

### Verificar

```bash
gh api repos/OsvaldoBello/Portal-chamados-Bondmann/branches/claude/develop/protection \
  --jq '{checks: .required_status_checks.contexts, reviews: .required_pull_request_reviews.required_approving_review_count, force_push: .allow_force_pushes.enabled}'
```

### Reverter (se precisar)

```bash
gh api -X DELETE \
  repos/OsvaldoBello/Portal-chamados-Bondmann/branches/claude/develop/protection
```

---

## (b) Política de senha do Supabase hospedado — mínimo 6 → 8

**Objetivo:** o GoTrue (Supabase Auth) é a **única barreira real** para quem chamar a
Auth API direto, por fora do nosso formulário. Hoje o projeto hospedado aceita senha
de **6** caracteres, enquanto a aplicação e o `supabase/config.toml` local já exigem
**8** (NIST 800-63B, `SENHA_MIN_CHARS`). Alinhar fecha essa brecha.

> **Por que não dá para automatizar aqui:** não há Management API do Supabase exposta
> via MCP nesta sessão. O ajuste é manual no painel (ou via Management API com um
> Personal Access Token do Supabase, fora do escopo deste repo).

### Caminho pelo dashboard do Supabase

1. <https://supabase.com/dashboard> → projeto de **produção**
   (`iurlzlhbnoemkzgexcfk`).
2. Menu lateral **Authentication** → **Sign In / Providers** (ou **Policies**,
   conforme a versão do painel) → seção **Password**.
3. Campo **Minimum password length**: trocar **6 → 8**.
4. (Opcional, mas alinhado ao NIST) **Password requirements / strength**: manter
   **sem** exigência de composição (maiúscula/símbolo/dígito obrigatórios) — o
   comprimento é o que a decisão registrada prioriza; regra de composição arbitrária
   induz padrões previsíveis ("Senha123!"). Ver Seção 3.4.1 do plano mestre.
5. **Save**.

### Verificar

- No painel, reabrir a mesma tela e conferir **Minimum password length = 8**.
- Teste funcional (opcional): tentar criar/redefinir uma senha de 7 caracteres pela
  Auth API direta — deve ser rejeitada pelo GoTrue (antes passava). Pelo formulário
  da aplicação já era barrado (`SENHA_MIN_CHARS`), então esse teste só cobre o
  caminho "por fora do formulário".

> **Enquanto (b) não é feito:** o mínimo de 8 continua garantido pela aplicação (as
> rotas `/redefinir-senha` e `/admin/usuarios` validam `SENHA_MIN_CHARS`). A única
> exposição é alguém chamando a Auth API do GoTrue diretamente — o que exige a `anon
> key` e passar por fora do fluxo normal.

---

## (c) Habilitar MFA/TOTP no Supabase hospedado

**Objetivo:** o item 3.3 (Sprint 3) implementou o MFA por TOTP — telas de ativação
(`/mfa`), step-up no login e exigência de `aal2` nas rotas do painel ADMIN. O
GoTrue só aceita `enroll`/`verify` de fatores TOTP se o recurso estiver **ligado no
projeto**; o `supabase/config.toml` (stack local) já está alinhado, o **hospedado**
precisa do mesmo ajuste.

> **Sem este passo:** `POST /mfa/enroll` responde **503** com a mensagem
> "Não foi possível iniciar a ativação do MFA…". Nada quebra para quem não usa MFA
> — ninguém é bloqueado (Fase 1 é *opcional com aviso*), apenas ninguém consegue
> ativar.

### Caminho pelo dashboard do Supabase

1. <https://supabase.com/dashboard> → projeto de **produção** (`iurlzlhbnoemkzgexcfk`).
2. **Authentication** → **Multi-Factor Authentication** (em algumas versões do
   painel: **Sign In / Providers** → seção **Multi-Factor Authentication**).
3. Habilitar **TOTP (Authenticator app)** — tanto *enroll* quanto *verify*.
4. **Save**.

### Verificar

- Entrar no portal com uma conta `ADMIN` → acessar **`/mfa`** → **Ativar verificação
  em duas etapas**: o QR code deve aparecer (se responder 503, o TOTP ainda não está
  ligado).
- Concluir a ativação com o código do autenticador; sair e entrar de novo: o login
  deve levar direto à tela de verificação (step-up).

> **Ordem sugerida:** faça (c) **antes** de anunciar o MFA ao time — e ative o seu
> próprio ADMIN primeiro, para validar o fluxo ponta a ponta com o recovery
> (reset por TI em **Contas de usuário → Resetar MFA**) já disponível como rede de
> segurança.

---

## (d) Ativar o agente Químico (F4 da frente de IA) — senha do role `ia_worker` + envs

A migration `0054_base_quimico` criou o role `ia_worker` **sem senha** (login
impossível até este passo). Ele é a conexão dedicada do Passe B do agente
Químico: enxerga o catálogo/fichas/playbooks e **não tem permissão** nas
quantidades das formulações (`base_quimico_formulacoes`) — garantia de banco,
já verificada em produção.

1. **Definir a senha** (SQL Editor do Supabase, projeto `iurlzlhbnoemkzgexcfk`):

   ```sql
   ALTER ROLE ia_worker PASSWORD '<senha-forte-gerada>';
   ```

   Gerar com `openssl rand -base64 24` (ou similar). NUNCA colar a senha em
   chat/commit/doc — só no painel do Railway.

2. **Configurar no Railway** (mesmo host/porta do `DATABASE_URL` atual, via
   Supavisor 6543, trocando usuário e senha):

   ```text
   IA_WORKER_DATABASE_URL=postgresql://ia_worker.iurlzlhbnoemkzgexcfk:<senha>@<host-supavisor>:6543/postgres
   IA_TRIAGEM_DEPARTAMENTOS=TI,Dpto Químico
   ```

   (Manter `IA_TRIAGEM_MODO_SOMBRA=true` — o Químico só sai da sombra após a
   F5/red team e o DPA da OpenAI aceito.)

3. **Reingestão da base** (sempre que a planilha/PDF mudarem — arquivos ficam
   fora do repositório, com o gestor):

   ```bash
   python scripts/ingestao_base_quimico.py --planilha "<caminho do .xlsx>" --fichas-pdf "<caminho do .pdf>"
   ```

   Re-executável (upsert): rodar de novo nunca duplica. Conferir no final as
   contagens e a lista de páginas sem produto identificado.

4. **Fichas — RESOLVIDO com o químico (2026-07-24): 65 de 66 produtos com
   ficha em produção.** O fatiamento foi revisado página a página (1 página =
   1 ficha, âncora `>> NOME`; comparação alfanumérica tolera "WAY45"×"WAY 45",
   "D.F.D.", etc.). As 11 fichas que estavam órfãs foram tratadas conforme o
   químico esclareceu (no script `ingestao_base_quimico.py`):

   | Ficha (pág) | Tratamento |
   |---|---|
   | FEMME (30) / FLORAL (31) | `ALIASES_FICHAS` → DESINFETANTE FEMME / FLORAL |
   | LAVANDA (36) | **`CORRECOES_NOME_PRODUTO`**: o S101 estava rotulado "DESINFETANTE FLORAL" por engano → renomeado para "DESINFETANTE LAVANDA" e recebe esta ficha |
   | OX (50) / SNAP (61) | `ALIASES_FICHAS` → BASE OX / BASE SNAP |
   | BRIL (14) + GRAXCAR II (35) | `ALIASES_FICHAS` → BASE BRIL (duas fichas) |
   | CONCENTRADO (20) + SHAMP (59) | `ALIASES_FICHAS` → BASE CONCENTRADO (duas fichas) |
   | PROTEC (53) + PROTETIVO (54) | `ALIASES_FICHAS` → PROTEC (não há "BASE PROTEC" na planilha; PROTEC concentra as duas) |
   | AW-B 32/46/68 (8–10), LW-B 32/46/68 (44–46) | comprados prontos, sem formulação → `PRODUTOS_SO_FICHA`: entram como produto + ficha, sem componentes |
   | LIMPTEC 100 INCOLOR (41), SABOLIQ SEM AROMA INCOLOR (56) | `ALIASES_FICHAS` → anexadas ao produto principal |

   **Continua sem ficha (esperado):** só `TRIACID - DESCONTINUADO`
   (descontinuado). **Fichas do PDF ainda sem produto na planilha, a cargo do
   gestor** (o script as reporta como "esperado"):
   - **ADITIVO 1090** (pág 3) e **ADITIVO ANTI-INCRUSTANTE** (pág 4) — o gestor
     vai adicioná-los à planilha depois; ao reingerir, as fichas entram
     sozinhas (âncora casa).
   - **LB 20 H** (pág 39) — produto não liberado para venda; ignorado de
     propósito (na lista `FICHAS_ESPERADAS_SEM_PRODUTO`).

   Quando os aditivos entrarem na planilha, remover a entrada correspondente de
   `FICHAS_ESPERADAS_SEM_PRODUTO` e reingerir. Consulta para reconferir
   produtos sem ficha:

   ```sql
   SELECT p.nome, p.chave_produto, p.segmento FROM base_quimico_produtos p
    WHERE NOT EXISTS (SELECT 1 FROM base_quimico_fichas f
                        WHERE f.chave_produto = p.chave_produto)
    ORDER BY p.nome;
   ```

   **Correção pendente no arquivo-mestre (gestor):** o S101 (LAVANDA rotulado
   como FLORAL) está corrigido no banco via `CORRECOES_NOME_PRODUTO`, mas o
   ideal é o químico corrigir o nome na planilha-fonte e então remover essa
   entrada do script.

5. **Reachability (recuperação seletiva) — a resolver depois:** o dropdown
   "Produto" do formulário de abertura (`app/domain/formularios_quimico.py`)
   usa nomes COMERCIAIS (BRIL, CONCENTRADO, OX, SNAP, GRAXCAR II, SHAMP,
   PROTETIVO, "DESINFETANTES BONDMANN (LAVANDA/FEMME/FLORAL)") enquanto a base
   guarda o produto sob o nome da planilha (BASE BRIL, PROTEC, DESINFETANTE
   LAVANDA...). Um chamado que seleciona o nome comercial pode não casar com a
   linha da base na recuperação seletiva do Passe B — a ficha existe mas não é
   puxada. AW-B/LW-B não têm esse problema (nome idêntico ao dropdown). Fica
   como próximo ajuste: um de-para dropdown→produto compartilhado entre o form
   e `identificar_produtos`.

---

## Checklist rápido do gestor

- [ ] (a) Branch protection em `claude/develop` — via UI **ou** `gh api` (escolher a
      variante de revisão conforme tamanho do time; ver ressalva do mantenedor solo).
- [ ] (b) Supabase hospedado: **Minimum password length** 6 → 8.
- [ ] (c) Supabase hospedado: habilitar **MFA/TOTP** (destrava a ativação do MFA — item 3.3).
- [ ] (d) IA/Químico: senha do `ia_worker` + `IA_WORKER_DATABASE_URL` +
      `Dpto Químico` em `IA_TRIAGEM_DEPARTAMENTOS` (Railway) + DPA OpenAI
      (gate do go-live) + revisar os 20 produtos sem ficha.
- [ ] Marcar os itens como feitos na tabela de progresso do
      `plano_melhorias_auditoria.md` (linhas 3.2 e 3.3).
