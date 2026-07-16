# ADR-0003 — Pivô: sistema interno com roteamento por departamento

**Status:** Aceito · **Data:** 2026-07-01 · **Ref.:** plano mestre Seção 3.2, migrations `0008`/`0009`

## Contexto

O desenho original (spec v2.0) modelava o portal como **multi-tenant por
empresa** — isolamento pela `empresa_id`, pensado para múltiplos clientes
externos contratando o serviço. Na prática, o portal é de **uso interno** da
Bondmann Química: ninguém compra/contrata, não há tenants externos. Manter o
eixo de isolamento em "empresa" não refletia o uso real e obrigava gambiarras
(uma única empresa semeada, `empresa_id` como plumbing sem função de negócio).

## Decisão

O eixo de isolamento muda de **empresa** para **departamento** (o setor que
atende o chamado):

- `departamentos` (TI, RH, Marketing — hoje) vira tabela gerenciável, igual a
  categorias.
- Staff (`OPERADOR`/`ADMIN`) tem um `departamento_id`; só vê/atende os
  chamados do próprio setor (refinado depois pelas migrations `0020`, `0027`,
  `0028` — ver histórico em `docs/CHANGELOG.md`).
- Funcionário (`CLIENTE`, sem departamento de atendimento) vê apenas os
  chamados que abriu.
- `empresas`/`planos_sla` viram apenas configuração interna de SLA (org única
  Bondmann), não mais uma dimensão de isolamento.
- Cadastro deixa de ser signup público — é feito direto no Supabase
  (Authentication > Users) + promoção via `supabase/registro_usuarios.sql`.

Materializado nas migrations `0008_departamentos_roteamento` +
`0009_harden_auth_departamento`.

## Consequências

- Todo o modelo de RLS (Seção 3 do plano mestre) passa a girar em torno de
  `departamento_id`, não `empresa_id` — helpers `auth_departamento_id()`,
  `auth_is_ti()`.
- Decisões subsequentes (líder de setor, autoatendimento Marketing/RH) são
  refinamentos **em cima** deste pivô, não pivôs novos — daí não terem ADR
  próprio, só entradas de changelog.
- `empresa_id` continua existindo no schema (plumbing do path de Storage e do
  motor de SLA) mas deixou de ser uma fronteira de segurança ativa.
- Este é o pivô mais estrutural do projeto — reverter significaria refazer
  RLS, rotas e templates do zero.
