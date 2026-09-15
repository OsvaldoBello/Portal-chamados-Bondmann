-- 0091_automacao_jobs.sql
-- Automação de criação/desligamento de usuários (RH → TI) — F2 do
-- plano_md_mestre_automacao_acessos.md (Seção 5). Fila de jobs que o worker
-- externo (Railway + Tailscale, D1) consome por HTTP (`/api/automacao/*`).
--
-- Um job só nasce quando o TI clica "Executar automação" no atendimento
-- (gate humano, D2) — a etapa "aguardando aprovação" NÃO é uma linha: é o
-- chamado com `dados_formulario` das subcategorias de acesso e sem job ativo.
-- Isso deixa a RLS mínima: só o staff do departamento de destino (TI) cria e
-- cancela jobs; o worker escreve exclusivamente pela conexão administrativa
-- (claim, heartbeat, resultado), como a IA de triagem faz em `ia_triagens`.
--
-- `payload` é o contrato com o worker (docs/automacao_api.md) — nunca leva
-- segredo. `resultado` guarda a lista de etapas com senhas mascaradas pelo
-- portal antes de gravar; as credenciais geradas vão só para a mensagem de
-- encerramento ao RH (D4), nunca para esta tabela.

BEGIN;

DO $$ BEGIN
  CREATE TYPE automacao_tipo AS ENUM ('CRIACAO', 'DESLIGAMENTO', 'REVOGAR_LICENCA');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
  CREATE TYPE automacao_status AS ENUM (
    'NA_FILA', 'EXECUTANDO', 'CONCLUIDO', 'CONCLUIDO_COM_PENDENCIAS', 'FALHOU', 'CANCELADO'
  );
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

CREATE TABLE IF NOT EXISTS automacao_jobs (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  chamado_id      uuid NOT NULL REFERENCES chamados(id) ON DELETE CASCADE,
  tipo            automacao_tipo NOT NULL,
  status          automacao_status NOT NULL DEFAULT 'NA_FILA',
  dry_run         boolean NOT NULL DEFAULT false,
  payload         jsonb NOT NULL,                       -- contrato com o worker, SEM segredos
  resultado       jsonb NOT NULL DEFAULT '[]'::jsonb,   -- lista de etapas (senhas mascaradas)
  executar_apos   timestamptz NOT NULL DEFAULT now(),   -- o worker só pega quando <= now()
  aprovado_por    uuid NOT NULL REFERENCES perfis(id),  -- quem clicou (remetente das mensagens)
  aprovado_em     timestamptz NOT NULL DEFAULT now(),
  worker_id       text,
  claimed_at      timestamptz,
  heartbeat_at    timestamptz,
  etapa_atual     text,
  finalizado_em   timestamptz,
  tentativas      integer NOT NULL DEFAULT 0,
  reexecucao_de   uuid REFERENCES automacao_jobs(id),
  erro            text,
  created_at      timestamptz NOT NULL DEFAULT now(),
  updated_at      timestamptz NOT NULL DEFAULT now()
);

COMMENT ON TABLE automacao_jobs IS
  'Fila da automação de criação/desligamento de usuários (plano_md_mestre_automacao_acessos.md, Seção 5). Criada pelo TI no atendimento; consumida pelo worker via /api/automacao (admin_connection). Nunca contém segredos.';

-- Um job ATIVO por chamado e tipo: reexecução (só das etapas pendentes) é um
-- job novo apontando para o anterior em `reexecucao_de`.
CREATE UNIQUE INDEX IF NOT EXISTS ux_automacao_jobs_ativo
  ON automacao_jobs(chamado_id, tipo)
  WHERE status IN ('NA_FILA', 'EXECUTANDO');
CREATE INDEX IF NOT EXISTS idx_automacao_jobs_fila
  ON automacao_jobs(executar_apos) WHERE status = 'NA_FILA';
CREATE INDEX IF NOT EXISTS idx_automacao_jobs_chamado ON automacao_jobs(chamado_id);

DROP TRIGGER IF EXISTS set_timestamp_automacao_jobs ON automacao_jobs;
CREATE TRIGGER set_timestamp_automacao_jobs
  BEFORE UPDATE ON automacao_jobs
  FOR EACH ROW EXECUTE FUNCTION trigger_set_timestamp();

ALTER TABLE automacao_jobs ENABLE ROW LEVEL SECURITY;
GRANT SELECT, INSERT, UPDATE ON automacao_jobs TO authenticated;

-- Leitura/escrita do staff: só o departamento de destino do chamado (mesmo
-- escopo de `ia_triagens`, 0050 — nenhum tratamento especial para TI). Na
-- prática hoje só a TI recebe estas subcategorias.
DROP POLICY IF EXISTS automacao_jobs_select_staff ON automacao_jobs;
CREATE POLICY automacao_jobs_select_staff ON automacao_jobs
  FOR SELECT USING (
    EXISTS (
      SELECT 1 FROM chamados c
      WHERE c.id = automacao_jobs.chamado_id
        AND auth_role() IN ('OPERADOR', 'ADMIN')
        AND c.departamento_id = auth_departamento_id()
    )
  );

-- INSERT: o TI que aprova é o próprio `aprovado_por`; job nasce direto na fila.
DROP POLICY IF EXISTS automacao_jobs_insert_staff ON automacao_jobs;
CREATE POLICY automacao_jobs_insert_staff ON automacao_jobs
  FOR INSERT WITH CHECK (
    aprovado_por = auth.uid()
    AND status = 'NA_FILA'
    AND EXISTS (
      SELECT 1 FROM chamados c
      WHERE c.id = automacao_jobs.chamado_id
        AND auth_role() IN ('OPERADOR', 'ADMIN')
        AND c.departamento_id = auth_departamento_id()
    )
  );

-- UPDATE do staff: só para cancelar um job ainda na fila (o worker nunca
-- passa por aqui — claim/heartbeat/resultado são da conexão administrativa).
DROP POLICY IF EXISTS automacao_jobs_update_staff ON automacao_jobs;
CREATE POLICY automacao_jobs_update_staff ON automacao_jobs
  FOR UPDATE USING (
    status = 'NA_FILA'
    AND EXISTS (
      SELECT 1 FROM chamados c
      WHERE c.id = automacao_jobs.chamado_id
        AND auth_role() IN ('OPERADOR', 'ADMIN')
        AND c.departamento_id = auth_departamento_id()
    )
  ) WITH CHECK (status = 'CANCELADO');

-- Sem policy de DELETE: histórico de execução é auditoria (cascata só com o chamado).

COMMIT;
