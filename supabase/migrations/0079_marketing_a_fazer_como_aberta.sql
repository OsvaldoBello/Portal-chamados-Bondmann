-- 0079_marketing_a_fazer_como_aberta.sql
-- Bug reportado pelo usuário (2026-08-10): "Em andamento" do Dashboard de
-- Marketing conta chamado em `A_FAZER` como se já estivesse em atendimento.
-- Em todo o resto do sistema `A_FAZER` é tratado como equivalente a `NOVO`
-- ("ainda não iniciado" — ver `AtendimentoRepo.iniciar_atendimento`,
-- `app/routes/workspace.py::mudar_status`: "Saindo de NOVO/A_FAZER pra
-- QUALQUER outro status, isso é o iniciar atendimento"). A view
-- `vw_marketing_volume_mensal` (`0032`, redefinida em `0041`/`0065`) nunca
-- acompanhou essa regra: `abertas` só olhava `status = 'NOVO'` e
-- `em_andamento` pegava "tudo que não é RESOLVIDO nem NOVO" — incluindo
-- `A_FAZER`, a coluna do Kanban do Marketing que existe exatamente para
-- separar "ainda não peguei" de "estou fazendo".
--
-- Conferido em produção antes da correção (chamados abertos em cada mês,
-- por status atual): JUL/26 tinha 8 em `A_FAZER` contados como "Em
-- andamento" (13) quando deveriam estar em "Abertas" (14 → 22); AGO/26
-- tinha 4 (em_andamento 9 → 5, abertas 0 → 4). Nenhuma mudança em `total`,
-- `concluidas` ou `volume` — só a fronteira entre `abertas`/`em_andamento`.
--
-- Regra (Seção 5.1 do plano mestre, changelog `0065`): redefinir a partir da
-- definição VIGENTE no banco (a da `0065`, com o filtro
-- `chamado_principal_id IS NULL`), nunca da migration que criou a view.

BEGIN;

CREATE OR REPLACE VIEW vw_marketing_volume_mensal AS
WITH abertos AS (
  SELECT
    date_trunc('month', c.created_at AT TIME ZONE 'America/Sao_Paulo')::date AS mes,
    count(*) AS total,
    count(*) FILTER (WHERE c.status IN ('NOVO', 'A_FAZER')) AS abertas,
    count(*) FILTER (WHERE c.status NOT IN ('RESOLVIDO', 'NOVO', 'A_FAZER')) AS em_andamento,
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
     AND c.chamado_principal_id IS NULL
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
     AND c.chamado_principal_id IS NULL
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
