-- 0031_prioridade_dinamica_marketing.sql
-- Decisão de produto 2026-07-09: a PRIORIDADE dos chamados do Marketing passa a
-- ser dinâmica, calculada pelos dias restantes até `data_entrega` (mínimo de
-- 48h já garantido na abertura — 0022):
--   > 7 dias        -> BAIXA
--   4 a 7 dias       -> MEDIA
--   2 a 3 dias       -> ALTA
--   <= 1 dia (inclusive vencido) -> URGENTE
-- Não mexe no cálculo de SLA (limite_resolucao continua = data_entrega + 18h,
-- 0022) nem nos demais departamentos (prioridade continua manual lá).
--
-- Caminho automático: job diário via `pg_cron`, se a extensão estiver habilitada
-- no projeto (tentativa best-effort abaixo, não falha a migration se não puder).
-- Fallback: rota `POST /admin/jobs/recalcular-prioridade-marketing` (só TI),
-- chamável manualmente ou por um scheduler externo (Railway cron / GitHub
-- Actions) quando `pg_cron` não está disponível.

BEGIN;

-- 1) Faixa de prioridade a partir dos dias restantes.
CREATE OR REPLACE FUNCTION prioridade_marketing_por_dias(p_dias integer)
RETURNS prioridade_chamado
LANGUAGE sql IMMUTABLE AS $$
  SELECT CASE
    WHEN p_dias > 7 THEN 'BAIXA'::prioridade_chamado
    WHEN p_dias >= 4 THEN 'MEDIA'::prioridade_chamado
    WHEN p_dias >= 2 THEN 'ALTA'::prioridade_chamado
    ELSE 'URGENTE'::prioridade_chamado
  END;
$$;

-- 2) Recalcula e grava a prioridade de todo chamado do Marketing NÃO resolvido
--    com data_entrega definida. Só atualiza (e só registra histórico) quem
--    realmente mudou de faixa — não gera ruído em `historico_chamados` para
--    quem ficou na mesma prioridade. Devolve a quantidade de linhas alteradas.
CREATE OR REPLACE FUNCTION recalcular_prioridade_marketing()
RETURNS integer
LANGUAGE plpgsql SECURITY DEFINER SET search_path TO 'public' AS $$
DECLARE
  v_hoje date := (now() AT TIME ZONE 'America/Sao_Paulo')::date;
  v_n integer;
BEGIN
  WITH alvo AS (
    SELECT c.id, c.prioridade AS de,
           prioridade_marketing_por_dias((c.data_entrega - v_hoje)::int) AS para
      FROM chamados c
      JOIN departamentos d ON d.id = c.departamento_id
     WHERE d.nome = 'Marketing'
       AND c.data_entrega IS NOT NULL
       AND c.status <> 'RESOLVIDO'
  ),
  mudou AS (
    SELECT * FROM alvo WHERE de IS DISTINCT FROM para
  ),
  atualizado AS (
    UPDATE chamados c
       SET prioridade = mudou.para
      FROM mudou
     WHERE c.id = mudou.id
    RETURNING c.id, mudou.de, mudou.para
  )
  INSERT INTO historico_chamados (chamado_id, ator_id, acao, detalhes)
  SELECT id, NULL, 'PRIORIDADE_ALTERADA',
         jsonb_build_object('de', de, 'para', para, 'motivo', 'recalculo_automatico_marketing')
    FROM atualizado;

  GET DIAGNOSTICS v_n = ROW_COUNT;
  RETURN v_n;
END;
$$;

-- Hardening: não são RPCs — ninguém chama via API pública. A rota HTTP passa
-- por `admin_connection` (bypassa RLS, restrita ao TI na camada de app).
REVOKE EXECUTE ON FUNCTION prioridade_marketing_por_dias(integer) FROM public, anon, authenticated;
REVOKE EXECUTE ON FUNCTION recalcular_prioridade_marketing()      FROM public, anon, authenticated;

-- 3) Agendamento diário via pg_cron (best-effort — não derruba a migration se a
--    extensão não estiver disponível/habilitada neste projeto Supabase).
DO $$
BEGIN
  CREATE EXTENSION IF NOT EXISTS pg_cron;
EXCEPTION WHEN OTHERS THEN
  RAISE NOTICE 'pg_cron indisponível neste projeto (%). Use o scheduler externo '
               '(POST /admin/jobs/recalcular-prioridade-marketing).', SQLERRM;
END $$;

DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'pg_cron') THEN
    PERFORM cron.unschedule(jobid) FROM cron.job WHERE jobname = 'recalcular_prioridade_marketing_diario';
    PERFORM cron.schedule(
      'recalcular_prioridade_marketing_diario',
      '0 6 * * *',  -- 06:00 UTC = 03:00 America/Sao_Paulo (fora do horário comercial)
      $sql$SELECT recalcular_prioridade_marketing();$sql$
    );
  END IF;
EXCEPTION WHEN OTHERS THEN
  RAISE NOTICE 'Não foi possível agendar via pg_cron (%). Use o scheduler externo.', SQLERRM;
END $$;

COMMIT;
