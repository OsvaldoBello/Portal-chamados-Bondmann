-- 0080_marketing_volume_por_conclusao.sql
-- Bug reportado pelo usuário (2026-08-10): "Volume produzido" de agosto
-- estava puxado por um chamado ("Crachás Atualizados - Internos", volume
-- 81) ainda EM_ATENDIMENTO, sem `resolvido_em` — nada foi produzido/entregue
-- ainda, mas o volume já entrava na conta do mês porque `vw_marketing_volume_mensal`
-- somava `volume` na CTE `abertos` (ancorada em `created_at`, junto com
-- `total`/`abertas`/`em_andamento`). Peça em produção conta como peça
-- ENTREGUE, não como peça pedida — o indicador tem que refletir o que foi
-- de fato concluído no mês, não o que foi só aberto.
--
-- Mudança: `volume` migra da CTE `abertos` para a `concluidos` (mesma âncora
-- de `concluidas`, `resolvido_em`) — chamado sem `resolvido_em` não soma
-- volume em mês nenhum até ser de fato resolvido, e um chamado aberto em
-- julho mas só entregue em agosto agora conta o volume dele em agosto, não
-- em julho. `total`/`abertas`/`em_andamento`/`mkt_orig`/`sol_orig`/`atrasos`/
-- `tempo_medio` continuam ancorados em `created_at` — essa parte não foi
-- pedida nesta entrega (cohort diferente de `concluidas`, já documentado e
-- sinalizado ao usuário na entrega da `0079`).
--
-- Regra (changelog `0065`): redefinir a partir da definição VIGENTE no
-- banco (a da `0079`, aplicada nesta mesma sessão), nunca de uma migration
-- anterior.

BEGIN;

CREATE OR REPLACE VIEW vw_marketing_volume_mensal AS
WITH abertos AS (
  SELECT
    date_trunc('month', c.created_at AT TIME ZONE 'America/Sao_Paulo')::date AS mes,
    count(*) AS total,
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
  COALESCE(a.total, 0)             AS total,
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
