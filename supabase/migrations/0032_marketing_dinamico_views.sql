-- 0032_marketing_dinamico_views.sql
-- Decisão de produto 2026-07-09: `marketing_midia_regional` estava engessada em
-- só 5 meses seedados manualmente (`mes text`, ex. 'JAN/26') — sem CRUD, dava
-- pra adicionar um mês novo só via migration. E os gráficos do dashboard de
-- Marketing recalculavam tudo em Python a partir de uma busca SEM LIMITE de
-- TODOS os chamados do Marketing já abertos (nada "leve"). Duas mudanças:
--   1. `marketing_midia_regional.mes` vira `date` (dia 1 do mês) — a UI formata
--      "JAN/26" na apresentação; o CRUD entra em `/admin/gestao`.
--   2. Duas VIEWS novas agregam os chamados do Marketing por mês — o banco guarda
--      só dado bruto (`chamados`), as views calculam sob demanda; o dashboard
--      deixa de puxar toda a tabela de chamados para agregar em Python.

BEGIN;

-- 1) `mes` text -> date (dia 1 do mês). Converte os 5 valores seedados em 0024
--    ('JAN/26'..'MAI/26'); qualquer mês novo cadastrado depois já entra como date.
ALTER TABLE marketing_midia_regional ADD COLUMN IF NOT EXISTS mes_data date;

UPDATE marketing_midia_regional
   SET mes_data = to_date(
     '01/' ||
     CASE upper(substring(mes from 1 for 3))
       WHEN 'JAN' THEN '01' WHEN 'FEV' THEN '02' WHEN 'MAR' THEN '03' WHEN 'ABR' THEN '04'
       WHEN 'MAI' THEN '05' WHEN 'JUN' THEN '06' WHEN 'JUL' THEN '07' WHEN 'AGO' THEN '08'
       WHEN 'SET' THEN '09' WHEN 'OUT' THEN '10' WHEN 'NOV' THEN '11' WHEN 'DEZ' THEN '12'
     END || '/20' || substring(mes from 5 for 2),
     'DD/MM/YYYY'
   )
 WHERE mes_data IS NULL;

ALTER TABLE marketing_midia_regional ALTER COLUMN mes_data SET NOT NULL;
ALTER TABLE marketing_midia_regional DROP CONSTRAINT IF EXISTS marketing_midia_regional_mes_key;
ALTER TABLE marketing_midia_regional DROP COLUMN mes;
ALTER TABLE marketing_midia_regional RENAME COLUMN mes_data TO mes;
ALTER TABLE marketing_midia_regional ADD CONSTRAINT marketing_midia_regional_mes_key UNIQUE (mes);

-- RLS já existia (herdada da tabela) — TI lê/escreve, os demais só leem
-- (mesma política de catálogo das outras tabelas de gestão; sem mudança aqui,
-- a tabela não tinha RLS própria além do grant padrão de authenticated).

-- 2) Views de agregação mensal do Marketing (dado bruto = `chamados`; nada fica
--    pré-calculado em tabela). `America/Sao_Paulo` para bater com o resto do
--    sistema (Seção 5.2 do plano mestre).
CREATE OR REPLACE VIEW vw_marketing_volume_mensal AS
SELECT
  date_trunc('month', c.created_at AT TIME ZONE 'America/Sao_Paulo')::date AS mes,
  count(*) AS total,
  count(*) FILTER (WHERE c.status = 'RESOLVIDO') AS concluidas,
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
 GROUP BY 1;

CREATE OR REPLACE VIEW vw_marketing_setor_mensal AS
SELECT
  date_trunc('month', c.created_at AT TIME ZONE 'America/Sao_Paulo')::date AS mes,
  COALESCE(NULLIF(c.setor, ''), 'Outros') AS setor,
  count(*) AS total
  FROM chamados c
  JOIN departamentos d ON d.id = c.departamento_id
 WHERE d.nome = 'Marketing'
 GROUP BY 1, 2;

-- Views herdam RLS das tabelas de origem (chamados/departamentos) — sem policy
-- própria a criar; `security_invoker` é o padrão do Postgres para views normais.
ALTER VIEW vw_marketing_volume_mensal SET (security_invoker = true);
ALTER VIEW vw_marketing_setor_mensal  SET (security_invoker = true);

COMMIT;
