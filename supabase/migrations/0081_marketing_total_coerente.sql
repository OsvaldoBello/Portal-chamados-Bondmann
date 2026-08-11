-- 0081_marketing_total_coerente.sql
-- Bug reportado pelo usuário (2026-08-11): no gráfico "Status das Demandas" a
-- barra **Total** de AGO/26 mostrava 16 enquanto as barras coloridas do mesmo
-- mês somavam 21 (14 concluídas + 6 em andamento + 1 aberta). A barra "Total"
-- ficava MENOR que a soma das partes que ela deveria totalizar.
--
-- Causa: desde a `0041`, `concluidas` conta pelo mês de RESOLUÇÃO
-- (`resolvido_em`) enquanto `total` continuou contando pelo mês de ABERTURA
-- (`created_at`). São cohorts diferentes, então a identidade
-- `total = concluídas + em andamento + abertas` deixou de valer — a própria
-- `0041` registrou isso ("o 'Total' do mês deixa de ser necessariamente a soma
-- das barras coloridas") como efeito aceito na época. Na prática o gráfico
-- ficou lendo errado: em AGO/26 as 5 demandas abertas em julho e entregues em
-- agosto entram nas "Concluídas" de agosto mas não entravam no "Total" de
-- agosto.
--
-- Mudança: `total` passa a ser exatamente a soma das três barras —
--   total = concluídas (resolvidas no mês)
--         + em andamento (abertas no mês, ainda não resolvidas)
--         + abertas      (abertas no mês, ainda não iniciadas)
-- ou seja, o cohort do mês = "o que foi ENTREGUE no mês" ∪ "o que ENTROU no
-- mês e ainda está em pé". Em AGO/26: 14 + 6 + 1 = 21 (era 16). Em JUL/26:
-- 51 + 4 + 22 = 77 (era 75).
--
-- Essa é a mesma identidade que o histórico pré-Portal já respeita:
-- em TODAS as 6 linhas de `marketing_volume_baseline` (jan-jun/26, migrations
-- `0069`/`0073`/`0074`) `total = concluidas + abertas + em_andamento` — o
-- baseline é a planilha do próprio time do Marketing, então essa é a definição
-- de "total do mês" que o setor usa. A view era a única fonte fora do padrão.
--
-- NÃO muda de cohort nesta entrega (continuam ancorados em `created_at`, "o
-- que entrou no mês"): `mkt_orig`, `sol_orig`, `atrasos` e `tempo_medio`.
-- Consequência: `mkt_orig + sol_orig` (aba "3. Origem da Demanda") mede as
-- ABERTURAS do mês e por isso não bate mais com `total` nos meses de Portal —
-- os percentuais derivados no backend/front (% origem Marketing, % atrasos)
-- passaram a dividir por `mkt_orig + sol_orig`, e não por `total`, justamente
-- para não mudarem de valor por causa desta migration
-- (`app/repositories/admin.py`, `app/static/js/admin_marketing.js`,
-- `app/services/export_marketing.py`). Nos meses de baseline os dois batem
-- (`mkt_orig + sol_orig = total`), então lá nada muda.
--
-- Regra (changelog `0065`): redefinir a partir da definição VIGENTE no banco
-- (a da `0080`, conferida com `pg_get_viewdef` antes desta entrega), nunca de
-- uma migration anterior.

BEGIN;

CREATE OR REPLACE VIEW vw_marketing_volume_mensal AS
WITH abertos AS (
  SELECT
    date_trunc('month', c.created_at AT TIME ZONE 'America/Sao_Paulo')::date AS mes,
    count(*) FILTER (WHERE c.status IN ('NOVO', 'A_FAZER')) AS abertas,
    count(*) FILTER (WHERE c.status NOT IN ('RESOLVIDO', 'NOVO', 'A_FAZER')) AS em_andamento,
    count(*) FILTER (WHERE lower(coalesce(c.origem_demanda, '')) = 'marketing') AS mkt_orig,
    count(*) FILTER (WHERE lower(coalesce(c.origem_demanda, '')) <> 'marketing') AS sol_orig,
    count(*) FILTER (WHERE (COALESCE(c.resolvido_em, now()) - c.created_at) > interval '5 days') AS atrasos,
    round(
      avg(EXTRACT(EPOCH FROM (c.resolvido_em - c.created_at)) / 86400.0)
        FILTER (WHERE c.resolvido_em IS NOT NULL)
    , 1) AS tempo_medio
    FROM chamados c
    JOIN departamentos d ON d.id = c.departamento_id
   WHERE d.nome = 'Marketing'
     AND c.chamado_principal_id IS NULL
   GROUP BY 1
),
concluidos AS (
  SELECT
    date_trunc('month', c.resolvido_em AT TIME ZONE 'America/Sao_Paulo')::date AS mes,
    count(*) AS concluidas,
    COALESCE(sum(COALESCE(c.volume, 1)), 0)::int AS volume
    FROM chamados c
    JOIN departamentos d ON d.id = c.departamento_id
   WHERE d.nome = 'Marketing'
     AND c.resolvido_em IS NOT NULL
     AND c.chamado_principal_id IS NULL
   GROUP BY 1
)
SELECT
  COALESCE(a.mes, cc.mes)          AS mes,
  -- Soma das três barras do gráfico, por definição (ver cabeçalho).
  (COALESCE(cc.concluidas, 0)
   + COALESCE(a.abertas, 0)
   + COALESCE(a.em_andamento, 0)) AS total,
  COALESCE(cc.concluidas, 0)       AS concluidas,
  COALESCE(a.abertas, 0)           AS abertas,
  COALESCE(a.em_andamento, 0)      AS em_andamento,
  COALESCE(cc.volume, 0)           AS volume,
  COALESCE(a.mkt_orig, 0)          AS mkt_orig,
  COALESCE(a.sol_orig, 0)          AS sol_orig,
  COALESCE(a.atrasos, 0)           AS atrasos,
  a.tempo_medio                    AS tempo_medio
  FROM abertos a
  FULL OUTER JOIN concluidos cc ON cc.mes = a.mes;

ALTER VIEW vw_marketing_volume_mensal SET (security_invoker = true);

COMMIT;
