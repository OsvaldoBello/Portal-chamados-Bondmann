-- 0093_automacao_sync_lideranca.sql
-- Sincronização de liderança na UBD (v2, F5). A sync não pertence a um chamado:
-- nasce da vigilância (06h) ou do resultado de um job de supervisor/gerente, e
-- só a conexão administrativa a cria. As policies da 0091 exigem um chamado do
-- setor, então a sync fica invisível para o staff e a tela não consegue criar
-- uma — sem policy nova. O relatório vai por nota/e-mail.
BEGIN;

ALTER TABLE automacao_jobs ALTER COLUMN chamado_id DROP NOT NULL;
ALTER TABLE automacao_jobs ALTER COLUMN aprovado_por DROP NOT NULL;

ALTER TABLE automacao_jobs DROP CONSTRAINT IF EXISTS ck_automacao_jobs_origem;
ALTER TABLE automacao_jobs ADD CONSTRAINT ck_automacao_jobs_origem CHECK (
  tipo = 'SINCRONIZAR_LIDERANCA' OR (chamado_id IS NOT NULL AND aprovado_por IS NOT NULL)
);

-- Uma sincronização na fila ou rodando por vez (a de evento e a diária se somam nela).
CREATE UNIQUE INDEX IF NOT EXISTS ux_automacao_jobs_sync_ativo
  ON automacao_jobs (tipo)
  WHERE tipo = 'SINCRONIZAR_LIDERANCA' AND status IN ('NA_FILA', 'EXECUTANDO');

-- Supervisores/gerentes que a automação já viu no SAP: a sync só remove da UBD
-- líderes desta lista (espelho com proteção, V4) — os de hoje E os de antes,
-- para um supervisor desligado sair dos representantes.
CREATE TABLE IF NOT EXISTS automacao_lideranca_gerenciada (
  email          text PRIMARY KEY CHECK (email = lower(email)),
  primeiro_visto timestamptz NOT NULL DEFAULT now(),
  ultimo_visto   timestamptz NOT NULL DEFAULT now()
);
COMMENT ON TABLE automacao_lideranca_gerenciada IS
  'Líderes comerciais (supervisor/gerente) já vistos pela sincronização de liderança (plano v2, F5). Só admin_connection().';
ALTER TABLE automacao_lideranca_gerenciada ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON automacao_lideranca_gerenciada FROM anon, authenticated;

COMMIT;
