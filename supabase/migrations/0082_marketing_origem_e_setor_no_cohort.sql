-- 0082_marketing_origem_e_setor_no_cohort.sql
-- Pedido do usuário (2026-08-11, na sequência da `0081`): "alinhe as abas 3 e 4
-- com o total também". A `0081` fez o "Total" do mês ser o cohort do mês
-- (entregue no mês + entrou no mês e ainda em pé), mas a aba
-- "3. Origem da Demanda" (`mkt_orig`/`sol_orig`) e a "4. Solicitantes"
-- (`vw_marketing_setor_mensal`) continuaram contando só as ABERTURAS do mês —
-- então as barras dessas duas abas somavam 16 num mês cujo Total dizia 21.
--
-- Mudança: as duas quebras passam a ser calculadas sobre o MESMO cohort do
-- `total`. O cohort do mês M é, para chamado principal do Marketing:
--     resolvido_em em M                      → a demanda ENTREGUE no mês
--   ∪ created_at em M e resolvido_em IS NULL → a demanda que ENTROU no mês e
--                                              ainda está em pé
-- (cada chamado aparece em exatamente um mês; um chamado aberto em julho e
-- entregue em agosto pertence a agosto, que é o que o gráfico de status já
-- mostrava desde a `0041` na barra "Concluídas"). Isso vira a CTE `cohort`,
-- de onde saem `total`, `concluidas`, `abertas`, `em_andamento`, `volume`,
-- `mkt_orig`, `sol_orig` — e a quebra por setor da outra view.
--
-- Conferido em produção antes de aplicar:
--   AGO/26 → origem 5+11=16 vira 7+14=21 · setor RH 9, Marketing 7, SIG 3,
--            Dpto Químico 1, Brigadistas 1 = 21 (= `total`)
--   JUL/26 → origem 48+27=75 vira 48+29=77 · soma por setor = 77
--
-- Coluna NOVA `aberturas` = quantas demandas foram ABERTAS no mês
-- (`created_at`, o que era o `total` até a `0081`). Ela existe porque
-- `atrasos` e `tempo_medio` continuam ancorados em `created_at` — a lista de
-- atrasos da aba 5 (`AdminRepo.mkt_dashboard_data`, campo `atrasosData`) é
-- montada por mês de abertura, e o card "% atrasos" precisa dividir pelo mesmo
-- conjunto que ele conta. Sem `aberturas` esse percentual passaria a dividir
-- por um cohort que não é o dele. Nos meses de baseline
-- (`marketing_volume_baseline`) não existe essa distinção — lá a planilha do
-- time já traz `total = concluidas + abertas + em_andamento = mkt_orig +
-- sol_orig` — e o backend usa `total` como `aberturas`.
--
-- NÃO muda nesta entrega: `atrasos` e `tempo_medio` (seguem em `created_at`,
-- ver acima) e a aba 7 (operadores), que tem âncora própria por métrica
-- (`atendidos` em `created_at`, `resolvidos` em `resolvido_em`, changelog
-- 2026-08-03).
--
-- Regra (changelog `0065`): redefinir a partir da definição VIGENTE no banco
-- (a da `0081` para o volume e a da `0065` para o setor, ambas conferidas com
-- `pg_get_viewdef` antes desta entrega), nunca de uma migration anterior.

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
    count(*) FILTER (WHERE (COALESCE(m.resolvido_em, now()) - m.created_at) > interval '5 days') AS atrasos,
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
  -- Coluna nova entra no FIM: `CREATE OR REPLACE VIEW` não deixa inserir
  -- coluna no meio nem renomear as existentes.
  COALESCE(a.aberturas, 0)    AS aberturas
  FROM do_mes t
  FULL OUTER JOIN abertos a ON a.mes = t.mes;

-- Aba "4. Solicitantes" — mesmo cohort, quebrado por setor solicitante.
CREATE OR REPLACE VIEW vw_marketing_setor_mensal AS
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
)
SELECT
  mes,
  COALESCE(NULLIF(setor, ''), 'Outros') AS setor,
  count(*) AS total
  FROM cohort
 GROUP BY 1, 2;

ALTER VIEW vw_marketing_volume_mensal SET (security_invoker = true);
ALTER VIEW vw_marketing_setor_mensal  SET (security_invoker = true);

COMMIT;
