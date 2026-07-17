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

## Checklist rápido do gestor

- [ ] (a) Branch protection em `claude/develop` — via UI **ou** `gh api` (escolher a
      variante de revisão conforme tamanho do time; ver ressalva do mantenedor solo).
- [ ] (b) Supabase hospedado: **Minimum password length** 6 → 8.
- [ ] (c) Supabase hospedado: habilitar **MFA/TOTP** (destrava a ativação do MFA — item 3.3).
- [ ] Marcar os itens como feitos na tabela de progresso do
      `plano_melhorias_auditoria.md` (linhas 3.2 e 3.3).
