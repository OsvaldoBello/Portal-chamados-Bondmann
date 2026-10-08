# Plano de Implementação — Correção de Regra e Tempo de Atraso em Demandas de Marketing

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Corrigir a lógica de detecção e o cálculo de dias de atraso nas demandas de Marketing, ancorando o atraso na Data de Entrega (`limite_resolucao`) em vez da data de criação (`created_at`) e removendo a regra conflitante de corte cego em 5 dias desde a abertura.

**Architecture:** 
1. Atualização da view SQL `vw_marketing_volume_mensal` (migration `0096`) para contabilizar `atrasos` apenas quando a demanda possui prazo (`limite_resolucao IS NOT NULL`), não é `sem_prazo` e ultrapassou o `limite_resolucao`.
2. Correção da query `atraso_rows` em `AdminRepo.mkt_dashboard_data` (`app/repositories/admin.py`) para calcular `dias = resolução - limite_resolucao` e filtrar apenas demandas que efetivamente ultrapassaram o prazo.
3. Atualização visual nos templates do dashboard (`dashboard_marketing.html`, `relatorio_marketing_export.html`) e exportador Excel (`export_marketing.py`) para substituir os textos herdados de "> 5 dias" por nomenclatura coerente com atraso real registrado.
4. Ajuste dos testes unitários e de integração (`test_export_marketing.py`, `test_admin.py`, etc.) e validação de todos os passos de CI/CD.

**Tech Stack:** Python 3.12+, FastAPI, asyncpg, Supabase PostgreSQL, Jinja2, TailwindCSS, openpyxl, pytest, ruff, mypy.

## Global Constraints
- Uma demanda de Marketing só é considerada atrasada quando `NOT COALESCE(sem_prazo, false)`, `limite_resolucao IS NOT NULL` e `COALESCE(resolvido_em, now()) > limite_resolucao`.
- O tempo de atraso exibido na coluna "Dias" deve ser a diferença entre a conclusão (ou instante atual) e a data limite de resolução: `EXTRACT(EPOCH FROM (COALESCE(resolvido_em, now()) - limite_resolucao)) / 86400.0`.
- Demandas com `sem_prazo = true` NUNCA atrasam e não devem figurar na tabela de atrasos nem na contagem de atrasos.
- Demandas entregues dentro do prazo (mesmo que com ciclo de vida longo, ex: criada em julho para entrega em novembro) NUNCA atrasam.
- Todas as migrations devem seguir a sequência estrita (migration `0096`) e passar em `scripts/check_migrations_sequence.py`.
- O pipeline de CI (`ruff`, `mypy`, `check_vendor_bundles`, `check_migrations_sequence`, `pytest`) deve ser 100% validado localmente.

---

### Task 1: Banco de Dados — Migration 0096 (`vw_marketing_volume_mensal`)

**Files:**
- Create: `supabase/migrations/0096_marketing_atrasos_por_prazo.sql`
- Modify: `plano_mestre_desenvolvimento.md`
- Modify: `docs/CHANGELOG.md`

**Interfaces:**
- Produces: View `vw_marketing_volume_mensal` com coluna `atrasos` computada por estouro de `limite_resolucao`.

- [ ] **Step 1: Criar migration 0096**

Criar `supabase/migrations/0096_marketing_atrasos_por_prazo.sql` recriando a view `vw_marketing_volume_mensal` a partir da definição vigente da `0082`, ajustando o CTE `abertos`:
```sql
-- 0096_marketing_atrasos_por_prazo.sql
-- Ajusta o cálculo da coluna `atrasos` em `vw_marketing_volume_mensal`:
-- Anteriormente, contava demandas cujo tempo desde a criação era superior a 5 dias:
--   count(*) FILTER (WHERE (COALESCE(m.resolvido_em, now()) - m.created_at) > interval '5 days')
-- Isso conflitava com demandas com prazo longo (ex.: abertura em julho para entrega em novembro)
-- e demandas `sem_prazo`.
--
-- Nova regra: atraso é o estouro do prazo acordado (`limite_resolucao` decorrente de `data_entrega`).
-- Demandas `sem_prazo` nunca atrasam.

BEGIN;

CREATE OR REPLACE VIEW vw_marketing_volume_mensal AS
WITH mkt AS (
  SELECT c.*
    FROM chamados c
    JOIN departamentos d ON d.id = c.departamento_id
   WHERE d.nome = 'Marketing'
     AND c.chamado_principal_id IS NULL
),
cohort AS (
  SELECT date_trunc('month', m.resolvido_em AT TIME ZONE 'America/Sao_Paulo')::date AS mes, m.*
    FROM mkt m WHERE m.resolvido_em IS NOT NULL
  UNION ALL
  SELECT date_trunc('month', m.created_at AT TIME ZONE 'America/Sao_Paulo')::date AS mes, m.*
    FROM mkt m WHERE m.resolvido_em IS NULL
),
do_mes AS (
  SELECT
    mes,
    count(*) AS total,
    count(*) FILTER (WHERE resolvido_em IS NOT NULL) AS concluidas,
    count(*) FILTER (WHERE resolvido_em IS NULL AND status IN ('NOVO', 'A_FAZER')) AS abertas,
    count(*) FILTER (WHERE resolvido_em IS NULL AND status NOT IN ('RESOLVIDO', 'NOVO', 'A_FAZER')) AS em_andamento,
    COALESCE(sum(COALESCE(volume, 1)) FILTER (WHERE resolvido_em IS NOT NULL), 0)::int AS volume,
    count(*) FILTER (WHERE lower(coalesce(origem_demanda, '')) = 'marketing') AS mkt_orig,
    count(*) FILTER (WHERE lower(coalesce(origem_demanda, '')) <> 'marketing') AS sol_orig
    FROM cohort
   GROUP BY 1
),
abertos AS (
  SELECT
    date_trunc('month', m.created_at AT TIME ZONE 'America/Sao_Paulo')::date AS mes,
    count(*) AS aberturas,
    count(*) FILTER (
      WHERE NOT COALESCE(m.sem_prazo, false)
        AND m.limite_resolucao IS NOT NULL
        AND COALESCE(m.resolvido_em, now()) > m.limite_resolucao
    ) AS atrasos,
    round(
      avg(EXTRACT(EPOCH FROM (m.resolvido_em - m.created_at)) / 86400.0)
        FILTER (WHERE m.resolvido_em IS NOT NULL)
    , 1) AS tempo_medio
    FROM mkt m
   GROUP BY 1
)
SELECT
  COALESCE(t.mes, a.mes)      AS mes,
  COALESCE(t.total, 0)        AS total,
  COALESCE(t.concluidas, 0)   AS concluidas,
  COALESCE(t.abertas, 0)      AS abertas,
  COALESCE(t.em_andamento, 0) AS em_andamento,
  COALESCE(t.volume, 0)       AS volume,
  COALESCE(t.mkt_orig, 0)     AS mkt_orig,
  COALESCE(t.sol_orig, 0)     AS sol_orig,
  COALESCE(a.atrasos, 0)      AS atrasos,
  a.tempo_medio               AS tempo_medio,
  COALESCE(a.aberturas, 0)    AS aberturas
  FROM do_mes t
  FULL OUTER JOIN abertos a ON a.mes = t.mes;

ALTER VIEW vw_marketing_volume_mensal SET (security_invoker = true);

COMMIT;
```

- [ ] **Step 2: Validar sequência de migrations**

Run: `python scripts/check_migrations_sequence.py`
Expected: PASS (sequência 0001..0096 contínua).

---

### Task 2: Repositório Admin — Correção de `AdminRepo.mkt_dashboard_data`

**Files:**
- Modify: `app/repositories/admin.py:825-840`

**Interfaces:**
- Produces: `atraso_rows` com dias calculados sobre `limite_resolucao` e filtro correto de atraso.

- [ ] **Step 1: Atualizar query `atraso_rows` em `app/repositories/admin.py`**

Substituir em `AdminRepo.mkt_dashboard_data`:
```python
            atraso_rows = await conn.fetch(
                """
                SELECT c.titulo,
                       date_trunc('month', c.created_at AT TIME ZONE 'America/Sao_Paulo')::date AS mes,
                       EXTRACT(EPOCH FROM (COALESCE(c.resolvido_em, now()) - c.limite_resolucao)) / 86400.0 AS dias,
                       c.causa_atraso,
                       c.especificacao_atraso
                  FROM chamados c
                  JOIN departamentos d ON d.id = c.departamento_id
                 WHERE d.nome = 'Marketing'
                   AND NOT COALESCE(c.sem_prazo, false)
                   AND c.limite_resolucao IS NOT NULL
                   AND COALESCE(c.resolvido_em, now()) > c.limite_resolucao
                 ORDER BY c.created_at ASC
                """
            )
```

---

### Task 3: Frontend e Exportação — Ajuste de Textos e Rótulos de Atraso

**Files:**
- Modify: `app/templates/admin/dashboard_marketing.html:155-160, 180-205`
- Modify: `app/templates/admin/relatorio_marketing_export.html:150-155, 175-200`
- Modify: `app/services/export_marketing.py:127, 174-186`

**Interfaces:**
- Consumes: `mktData.atrasosData`
- Produces: UI consistente com "Demandas com Atraso" / "Causas Registradas" sem referência fixa a "> 5 dias".

- [ ] **Step 1: Atualizar `app/templates/admin/dashboard_marketing.html`**
- No card KPI: substituir `Atrasos >5 dias` por `Demandas com atraso`.
- No título da tabela: substituir `⚠️ Demandas com Atraso > 5 dias — Causas Registradas` por `⚠️ Demandas com Atraso — Causas Registradas`.
- No card do gráfico de causas: substituir `Distribuição de causas para atrasos > 5 dias` por `Distribuição de causas de atraso`.

- [ ] **Step 2: Atualizar `app/templates/admin/relatorio_marketing_export.html`**
- Mesmas atualizações que no dashboard principal.

- [ ] **Step 3: Atualizar `app/services/export_marketing.py`**
- Linha 127: `("Demandas com atraso", atrasos)` em vez de `("Atrasos > 5 dias", atrasos)`.
- Linha 177: `_titulo(ws, "Demandas com Atraso", len(colunas))`.

---

### Task 4: Testes Unitários e de Integração

**Files:**
- Modify: `tests/test_export_marketing.py`
- Modify: `tests/test_export_html.py`
- Modify: `tests/test_admin.py`

- [ ] **Step 1: Atualizar testes de exportação Excel e HTML**
- Atualizar fixtures e asserts que verificavam o texto literal "Atrasos > 5 dias" para "Demandas com atraso".

- [ ] **Step 2: Adicionar teste unitário de regressão para a query de atrasos do Marketing**
- Validar caso de chamado criado em julho com entrega em novembro (sem atraso).
- Validar caso de chamado sem_prazo (sem atraso).
- Validar caso de chamado concluído no prazo (sem atraso).
- Validar caso de chamado concluído após o prazo (atraso = concluído - prazo).

---

### Task 5: Validação do CI/CD Completo e Documentação

**Files:**
- Modify: `plano_mestre_desenvolvimento.md`
- Modify: `docs/CHANGELOG.md`

- [ ] **Step 1: Executar linters e checagens**
Run: `python -m ruff check .`
Run: `python -m mypy`
Run: `python scripts/check_migrations_sequence.py`
Run: `python scripts/check_vendor_bundles.py`

- [ ] **Step 2: Executar suíte completa pytest**
Run: `python -m pytest tests/`

- [ ] **Step 3: Atualizar `plano_mestre_desenvolvimento.md` e `docs/CHANGELOG.md`**
Registrar a migration `0096` e o ajuste de cálculo de atrasos no changelog e plano mestre.
