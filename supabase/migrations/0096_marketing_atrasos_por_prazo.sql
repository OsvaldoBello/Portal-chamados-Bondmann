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
-- Cohort de ABERTURA (`created_at`) — só o que ficou nele: quantas demandas
-- entraram no mês, os atrasos dessas demandas e o tempo médio delas.
abertos AS (
  SELECT
    date_trunc('month', m.created_at AT TIME ZONE 'America/Sao_Paulo')::date AS mes,
    count(*) AS aberturas,
    count(*) FILTER (
      WHERE m.status IN ('NOVO', 'A_FAZER', 'EM_ATENDIMENTO', 'AGUARDANDO_TERCEIROS')
        AND NOT COALESCE(m.sem_prazo, false)
        AND m.limite_resolucao IS NOT NULL
        AND now() > m.limite_resolucao
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
