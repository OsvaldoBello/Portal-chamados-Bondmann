# Tutorial — Como criar cada tipo de usuário no banco

> Portal de Chamados Bondmann Química. **Não há cadastro público (signup).** Todo
> colaborador é criado **direto no Supabase** e, se for staff, **promovido por SQL**.
> Referência de comandos: [`supabase/registro_usuarios.sql`](../supabase/registro_usuarios.sql).

## Visão geral dos papéis

| Tipo de usuário | `role` (perfis) | `departamento_id` | O que enxerga | Onde cai ao logar |
|---|---|---|---|---|
| **Funcionário** | `CLIENTE` | — (nulo) | Só os chamados que **ele abriu** | `/portal` |
| **Operador de setor** | `OPERADOR` | RH / Marketing / TI | A fila do **seu setor** + os que abriu | `/workspace` |
| **Admin de setor** (gestor) | `ADMIN` | RH / Marketing | Atende o setor **+ painel `/admin` do setor** (CSAT, SLA, avaliações) | `/workspace` |
| **TI (acesso total)** | `ADMIN` | **TI** | **Tudo** + gestão de catálogos + `/admin` de todos os setores | `/workspace` |

> **Regra de ouro:** "acesso total" **não** é o papel `ADMIN` sozinho — é **estar no
> departamento `TI`** (`auth_is_ti()`). Um `ADMIN` em RH é só o gestor do RH.

---

## Passo 1 — Criar o usuário no Supabase (vale para TODOS os tipos)

1. Painel do Supabase → **Authentication → Users → Add user**.
2. Informe **e-mail corporativo** e uma **senha provisória** (marque *Auto Confirm User*
   para não exigir verificação de e-mail).
3. Ao salvar, o trigger `handle_new_user` cria automaticamente o registro em `perfis`
   com **`role = CLIENTE`** vinculado à org interna (Bondmann).

➡️ **Se for apenas Funcionário, acabou aqui.** Ele já pode entrar em `/login` e abrir
chamados. (A senha pessoal ele mesmo troca depois pelo fluxo **"Esqueci minha senha"** →
código por e-mail → nova senha.)

Para **staff**, siga o passo 2 conforme o tipo.

---

## Passo 2 — Promover a staff (SQL Editor do Supabase)

> ⚠️ **Sempre escreva nos DOIS lugares**: `perfis` (lido pela RLS do banco) **e**
> `auth.users.raw_app_meta_data` (lido pelo gate do app via JWT). Se promover só em
> `perfis`, o app continua tratando o usuário como funcionário. **A mudança só vale no
> próximo login** — peça re-login ao usuário.

### 2a. Operador de RH (atende a fila do RH, sem relatórios)

```sql
UPDATE perfis
   SET role = 'OPERADOR',
       departamento_id = (SELECT id FROM departamentos WHERE nome = 'RH')
 WHERE id = (SELECT id FROM auth.users WHERE email = 'colaborador.rh@bondmann.com.br');

UPDATE auth.users
   SET raw_app_meta_data = COALESCE(raw_app_meta_data, '{}'::jsonb)
                           || jsonb_build_object('role', 'OPERADOR')
 WHERE email = 'colaborador.rh@bondmann.com.br';
```

Troque `'RH'` por `'Marketing'` para um operador de Marketing.

### 2b. Admin de setor / gestor do RH (atende **e** vê os relatórios do RH)

Igual ao operador, mas com `role = 'ADMIN'`. É isto que dá acesso ao painel `/admin`
**escopado ao setor** (CSAT, conformidade de SLA, rapidez de resposta, produtividade e
as notas dadas pelos funcionários aos chamados do RH). **Não** libera gestão de catálogos
(isso é só do TI).

```sql
UPDATE perfis
   SET role = 'ADMIN',
       departamento_id = (SELECT id FROM departamentos WHERE nome = 'RH')
 WHERE id = (SELECT id FROM auth.users WHERE email = 'gestor.rh@bondmann.com.br');

UPDATE auth.users
   SET raw_app_meta_data = COALESCE(raw_app_meta_data, '{}'::jsonb)
                           || jsonb_build_object('role', 'ADMIN')
 WHERE email = 'gestor.rh@bondmann.com.br';
```

### 2c. TI — acesso total ao sistema

`role = 'ADMIN'` **no departamento `TI`**. Vê e atende **todos** os chamados de todos os
setores, gere catálogos (departamentos/categorias) e pode **repassar** chamados entre
setores.

```sql
UPDATE perfis
   SET role = 'ADMIN',
       departamento_id = (SELECT id FROM departamentos WHERE nome = 'TI')
 WHERE id = (SELECT id FROM auth.users WHERE email = 'ti@bondmann.com.br');

UPDATE auth.users
   SET raw_app_meta_data = COALESCE(raw_app_meta_data, '{}'::jsonb)
                           || jsonb_build_object('role', 'ADMIN')
 WHERE email = 'ti@bondmann.com.br';
```

---

## Passo 3 — Conferir (opcional, mas recomendado)

```sql
SELECT p.nome, u.email, p.role, d.nome AS departamento,
       (u.raw_app_meta_data->>'role') AS role_no_jwt
  FROM perfis p
  JOIN auth.users u ON u.id = p.id
  LEFT JOIN departamentos d ON d.id = p.departamento_id
 ORDER BY p.role, d.nome NULLS FIRST;
```

`role` (perfis) e `role_no_jwt` (auth.users) devem **bater**. Se divergirem, o passo 2
não escreveu nos dois lugares.

---

## Reverter para funcionário comum

```sql
UPDATE perfis SET role = 'CLIENTE', departamento_id = NULL
 WHERE id = (SELECT id FROM auth.users WHERE email = 'fulano@bondmann.com.br');
UPDATE auth.users
   SET raw_app_meta_data = COALESCE(raw_app_meta_data, '{}'::jsonb)
                           || jsonb_build_object('role', 'CLIENTE')
 WHERE email = 'fulano@bondmann.com.br';
```

## Criar um novo departamento

Pela UI (TI logado): **Admin → Gestão de catálogos → Departamentos**. Ou por SQL:

```sql
INSERT INTO departamentos (nome) VALUES ('Financeiro');
```

## Fluxo de primeira senha / esquecimento

Não é preciso guardar a senha de cada colaborador. Depois de criado no Supabase, o
usuário usa **"Esqueci minha senha"** no login → recebe um **código de 6 dígitos** por
e-mail → informa o código e cria a própria senha.

> ⚠️ **Config única necessária:** em *Authentication → Email Templates → Reset Password*,
> o corpo do e-mail precisa conter `{{ .Token }}` (o código). Sem isso, só vai o link.

---

## Apêndice — Criar a conta inteira por SQL (sem o painel)

Quando você não quer abrir *Authentication → Users*, dá para criar o usuário **já
promovido** direto no SQL Editor. Este bloco cria a conta em `auth.users`, deixa o
trigger montar o perfil, promove a ADMIN do setor e registra a *identity* de e-mail
(exigida pelo login por senha). É **idempotente** (pula se o e-mail já existir) e foi o
método usado para criar `admin.rh@` e `admin.marketing@`.

```sql
DO $$
DECLARE v_uid uuid; rec record;
BEGIN
  FOR rec IN (
    SELECT * FROM (VALUES
      ('admin.rh@bondmann.com.br',        'Admin RH',        'RH'),
      ('admin.marketing@bondmann.com.br', 'Admin Marketing', 'Marketing')
    ) AS t(email, nome, depto)
  ) LOOP
    IF EXISTS (SELECT 1 FROM auth.users WHERE email = rec.email) THEN CONTINUE; END IF;
    v_uid := gen_random_uuid();
    INSERT INTO auth.users (
      instance_id, id, aud, role, email, encrypted_password,
      email_confirmed_at, created_at, updated_at,
      raw_app_meta_data, raw_user_meta_data,
      confirmation_token, recovery_token, email_change_token_new, email_change
    ) VALUES (
      '00000000-0000-0000-0000-000000000000', v_uid, 'authenticated', 'authenticated',
      rec.email, crypt('Bondmann@2026', gen_salt('bf')),  -- troque a senha
      now(), now(), now(),
      jsonb_build_object('provider','email','providers', ARRAY['email'], 'role','ADMIN'),
      jsonb_build_object('nome', rec.nome),
      '', '', '', ''
    );
    UPDATE perfis SET role = 'ADMIN',
           departamento_id = (SELECT id FROM departamentos WHERE nome = rec.depto)
     WHERE id = v_uid;
    INSERT INTO auth.identities (
      provider_id, user_id, identity_data, provider, last_sign_in_at, created_at, updated_at
    ) VALUES (
      v_uid::text, v_uid,
      jsonb_build_object('sub', v_uid::text, 'email', rec.email, 'email_verified', true),
      'email', now(), now(), now()
    );
  END LOOP;
END $$;
```

> Observações: `pgcrypto` fornece `crypt()`/`gen_salt('bf')` (já habilitado). A coluna
> `auth.identities.email` é **gerada** — não a inclua no INSERT. Para um **funcionário**
> (CLIENTE) em vez de admin, basta **remover** o `UPDATE perfis` e trocar o `role` do
> `raw_app_meta_data` para `'CLIENTE'`.
