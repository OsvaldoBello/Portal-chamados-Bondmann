-- 0041_marketing_concluidas_por_mes_resolucao.sql
-- Decisão de produto 2026-07-10: `vw_marketing_volume_mensal` agrupava TUDO
-- (total/abertas/em_andamento/concluidas/volume/...) pelo mês de ABERTURA
-- (created_at). Isso distorce o gráfico "Status das Demandas" quando um
-- chamado é aberto num mês e só é fechado (resolvido_em) em outro — caso do
-- lote de chamados históricos do Marketing importados em 2026-07-10, abertos
-- entre jan/26 e jun/26 mas efetivamente encerrados no sistema em jul/26.
--
-- Mudança: "Concluídas" passa a contar pelo mês de RESOLUÇÃO
-- (resolvido_em), não de abertura. `total`/`abertas`/`em_andamento`/`volume`/
-- `mkt_orig`/`sol_orig`/`atrasos`/`tempo_medio` continuam pelo mês de
-- abertura (cohort de "o que entrou este mês"). Como as duas contagens agora
-- vêm de cohorts diferentes, o "Total" do mês deixa de ser necessariamente a
-- soma das barras coloridas — um chamado aberto em fev/26 e resolvido em
-- jul/26 conta no "Total" de fev/26 mas nas "Concluídas" de jul/26.

BEGIN;

CREATE OR REPLACE VIEW vw_marketing_volume_mensal AS
WITH abertos AS (
  SELECT
    date_trunc('month', c.created_at AT TIME ZONE 'America/Sao_Paulo')::date AS mes,
    count(*) AS total,
    count(*) FILTER (WHERE c.status = 'NOVO') AS abertas,
    count(*) FILTER (WHERE c.status NOT IN ('RESOLVIDO', 'NOVO')) AS em_andamento,
    COALESCE(sum(COALESCE(c.volume, 1)), 0)::int AS volume,
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
   GROUP BY 1
),
concluidos AS (
  SELECT
    date_trunc('month', c.resolvido_em AT TIME ZONE 'America/Sao_Paulo')::date AS mes,
    count(*) AS concluidas
    FROM chamados c
    JOIN departamentos d ON d.id = c.departamento_id
   WHERE d.nome = 'Marketing'
     AND c.resolvido_em IS NOT NULL
   GROUP BY 1
)
SELECT
  COALESCE(a.mes, cc.mes)          AS mes,
  COALESCE(a.total, 0)             AS total,
  COALESCE(cc.concluidas, 0)       AS concluidas,
  COALESCE(a.abertas, 0)           AS abertas,
  COALESCE(a.em_andamento, 0)      AS em_andamento,
  COALESCE(a.volume, 0)            AS volume,
  COALESCE(a.mkt_orig, 0)          AS mkt_orig,
  COALESCE(a.sol_orig, 0)          AS sol_orig,
  COALESCE(a.atrasos, 0)           AS atrasos,
  a.tempo_medio                    AS tempo_medio
  FROM abertos a
  FULL OUTER JOIN concluidos cc ON cc.mes = a.mes;

ALTER VIEW vw_marketing_volume_mensal SET (security_invoker = true);

COMMIT;
