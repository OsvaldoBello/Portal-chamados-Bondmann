# ADR-0007 — MFA (TOTP) com enforcement de `aal2` para ADMIN

**Status:** Aceito · **Data:** 2026-07-16 · **Ref.:** plano mestre Seção 3.4.1 · plano de melhorias itens 2.8 (B6) e 3.3

## Contexto

A auditoria (item 2.8/B6) avaliou MFA para contas de staff e registrou o
faseamento **ADMIN-first** na Seção 3.4.1: Fase 1 (Sprint 3) TOTP opcional para
staff; Fase 2 (Sprint 4) obrigatório para `ADMIN`. Esta ADR registra as decisões
tomadas ao implementar a **Fase 1**.

Restrições que moldaram o desenho:

- Auth é **delegada ao GoTrue/Supabase** (ADR-0001 / Seção 3.4.1). O GoTrue já
  suporta TOTP nativo — não há troca de provedor.
- O app **verifica o JWT localmente** (`app/security/jwt_verifier.py`, Seção 3.6)
  e resolve papel a partir dos claims (`app/auth/dependencies.py`). Chamar o
  GoTrue por request seria um gargalo — foi exatamente a razão da verificação
  local existir.
- O GoTrue expõe o nível de garantia no claim **`aal`**: `aal1` = só senha,
  `aal2` = senha + fator MFA verificado **nesta sessão**.

## Decisão

### 1. O segredo TOTP vive **só no GoTrue**

Nada de segredo TOTP no nosso banco. O app orquestra `enroll → challenge →
verify` sobre a sessão do usuário (`app/auth/mfa.py`), com um **cliente isolado**
por operação (`create_isolated_client` + `set_session`) — mesma razão do fluxo de
redefinição de senha: as chamadas MFA mutam a sessão do GoTrue e reusar o cliente
global compartilhado causaria corrida entre requests.

### 2. Enforcement lê o estado **dos claims**, não da rede

Rotas do painel ADMIN (`admin_context`) exigem `aal2` para o papel `ADMIN`. Para
decidir entre *redirecionar* (tem MFA, sessão aal1) e *avisar* (não tem MFA), o
enforcement precisa saber se o usuário tem fator verificado. Alternativas
consideradas:

| Opção | Por que não |
|---|---|
| Chamar `admin.mfa.list_factors()` por request | Uma ida à rede em **toda** requisição de ADMIN em aal1 — contraria a razão de a verificação de JWT ser local. |
| Coluna `perfis.mfa_enabled` | Exige migration **e** mexer no trigger `enforce_perfil_self_so_avatar` (0033), que hoje só libera `avatar_path` na auto-escrita — risco de RLS desproporcional para um booleano. |

**Escolhida:** espelhar um booleano em **`app_metadata.mfa_enabled`** (Admin API),
que o GoTrue embute no JWT. O enforcement lê `claims["app_metadata"]["mfa_enabled"]`
+ `claims["aal"]` — **local, sem rede, sem migration**. É o mesmo padrão de
dual-write já usado para `app_metadata.role` (item 1.5/M12), e é só um booleano —
não é segredo.

O espelho é gravado no enroll (`true`) e no reset por TI (`false`). Fica
defasado apenas no intervalo entre a ativação e o próximo token — irrelevante,
porque uma sessão recém-verificada já está em `aal2` e passa de qualquer forma.

### 3. Fase 1 = **opcional com aviso** (`[DECISÃO DO GESTOR]`, 2026-07-16)

- `ADMIN` **com** MFA + sessão `aal1` ⇒ redirect para `/mfa/verify`
  (`MfaChallengeRequired` → handler em `app/main.py`; HTMX recebe `HX-Redirect`).
- `ADMIN` **sem** MFA ⇒ entra normalmente, com **nudge** na UI. Não bloqueia:
  travar todo admin no dia do deploy, antes de haver suporte formado, era o risco
  que o faseamento da Seção 3.4.1 queria evitar.
- `OPERADOR`/`CLIENTE` ⇒ **fora** do enforcement nesta fatia (nem bloqueio nem
  aviso), mesmo com MFA ativo.

Tornar obrigatório para `ADMIN` é a **Fase 2** (Sprint 4), condicionada à adoção:
vira remover o ramo do nudge e redirecionar todo `aal1` para o enroll.

Reforço no login: quem já tem fator verificado vai direto ao step-up em vez da
home — os fatores vêm na própria resposta do `sign_in_with_password`, então isso
não custa chamada extra.

### 4. Recovery = **reset por admin/TI** (`[DECISÃO DO GESTOR]`, 2026-07-16)

Quem perde o autenticador pede ao TI, que remove o(s) fator(es) via Admin API
(`POST /admin/usuarios/{id}/reset-mfa`); o usuário re-enrola no próximo acesso.

Descartados nesta fatia:
- **Recovery codes** — o GoTrue não os gera nativamente; construí-los significaria
  guardar (mesmo hasheados) mais um segredo sob nossa guarda, contra a decisão 1.
- **Reset por e-mail** — amplia a superfície: quem controla a caixa de e-mail
  contornaria o MFA, esvaziando o segundo fator.

### 5. Lockout / janela de re-desafio = **padrões do GoTrue** (`[DECISÃO DO GESTOR]`, 2026-07-16)

`aal2` vale pela vida da sessão (persiste no refresh); o limite de tentativas de
verificação é o do próprio GoTrue. Sem re-desafio periódico próprio — exigiria
rastrear o instante do último `aal2` e uma expiração paralela à do GoTrue, escopo
que a Fase 1 não justifica. Nossos endpoints ainda têm rate limit de borda
(`10/minute`, Seção 2.4) contra força bruta.

## Consequências

- **Sem migration, sem mudança de RLS/schema** — o estado de MFA vive todo no
  GoTrue; o único reflexo do nosso lado é um claim.
- **Sem custo por request:** o enforcement é aritmética sobre claims já
  verificados. Nenhuma rota ficou mais lenta.
- **Testável sem rede:** claims sem `mfa_enabled` degradam para o comportamento de
  transição (nudge) — por isso a suíte (`tests/test_mfa.py`) cobre enforcement,
  enroll e verify sem tocar o GoTrue.
- **Deriva possível:** apagar um fator direto no dashboard do Supabase (sem passar
  pelo reset do TI) deixa `mfa_enabled=true` sem fator. O usuário cai no
  `/mfa/verify`, que ao não achar fator manda para `/mfa` e o deixa re-ativar —
  degrada para um passo a mais, não para bloqueio.
- **`[AÇÃO DO GESTOR]`:** o TOTP precisa estar habilitado no projeto Supabase
  **hospedado** (o `supabase/config.toml` local já foi alinhado). Ver
  `docs/runbook_hardening_gestor.md`.
