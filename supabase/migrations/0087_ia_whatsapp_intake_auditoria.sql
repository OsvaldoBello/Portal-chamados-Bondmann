-- 0087_ia_whatsapp_intake_auditoria.sql
-- Auditoria por rodada da extração de IA do intake de chamados via WhatsApp
-- (plano de intake WhatsApp, 2026-08-18). Mesmo shape de `ia_triagens`
-- (0050), mas ancorada em `conversa_id` em vez de `chamado_id`: a extração
-- roda ANTES de o chamado existir (pode terminar em pergunta de
-- esclarecimento ou em encerramento sem chamado nenhum).
--
-- RLS: habilitada, sem nenhuma policy — escrita/leitura exclusivas de
-- `app/db.py::admin_connection()` (mesmo padrão de `ia_triagens`).

BEGIN;

CREATE TABLE IF NOT EXISTS ia_whatsapp_intake (
  id             bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  conversa_id    uuid NOT NULL REFERENCES whatsapp_conversas(id) ON DELETE CASCADE,
  rodada         smallint NOT NULL CHECK (rodada >= 1),
  acao           text NOT NULL CHECK (acao IN ('PERGUNTA', 'CHAMADO_CRIADO', 'ENCERRADO_SEM_CHAMADO', 'ERRO')),
  resultado      jsonb NOT NULL DEFAULT '{}'::jsonb,
  modelo         text NOT NULL,
  tokens_entrada integer,
  tokens_saida   integer,
  custo_usd      numeric(10, 6),
  duracao_ms     integer,
  created_at     timestamptz NOT NULL DEFAULT now(),
  UNIQUE (conversa_id, rodada)
);

COMMENT ON TABLE ia_whatsapp_intake IS
  'Auditoria por rodada da extração de IA do intake de chamados via WhatsApp. Escrita só via admin_connection().';

CREATE INDEX IF NOT EXISTS idx_ia_whatsapp_intake_conversa ON ia_whatsapp_intake (conversa_id);

ALTER TABLE ia_whatsapp_intake ENABLE ROW LEVEL SECURITY;

-- Sem policies: intencional, mesmo padrão de ia_triagens (0050).

COMMIT;
