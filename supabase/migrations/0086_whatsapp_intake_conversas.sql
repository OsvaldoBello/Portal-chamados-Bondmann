-- 0086_whatsapp_intake_conversas.sql
-- Estado de conversa e âncora durável de mensagem recebida para o intake de
-- chamados via WhatsApp (plano de intake WhatsApp, 2026-08-18).
--
-- `whatsapp_mensagens_recebidas` existe para o mesmo motivo que a
-- reconciliação de triagens órfãs existe (ver `app/ia/triagem.py`,
-- incidente BOND-2026-00593): o processamento é disparado por
-- `asyncio.create_task` (não durável — um restart entre o webhook e o
-- processamento perde a tarefa sem rastro). Sem uma linha gravada no banco
-- ANTES de disparar a task, não haveria nada pra reconciliar depois. `wamid`
-- é único porque a Meta pode reentregar o mesmo webhook mais de uma vez
-- (at-least-once) — o `ON CONFLICT DO NOTHING` no INSERT torna o
-- processamento idempotente.
--
-- `whatsapp_conversas` acumula o histórico da conversa em andamento (a IA
-- pode precisar de mais de uma mensagem pra ter dado suficiente pra abrir o
-- chamado) até virar um chamado, ser encerrada sem chamado, ou expirar.
--
-- RLS: habilitada em ambas SEM NENHUMA policy — leitura/escrita exclusivas de
-- `app/db.py::admin_connection()` (mesmo padrão de `ia_triagens`, 0050).
-- Nenhum papel `authenticated` toca aqui; não há dado de cliente exposto por
-- essas tabelas fora do backend.

BEGIN;

CREATE TABLE IF NOT EXISTS whatsapp_conversas (
  id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  perfil_id            uuid NOT NULL REFERENCES perfis(id) ON DELETE CASCADE,
  telefone             text NOT NULL,
  status               text NOT NULL DEFAULT 'COLETANDO'
                         CHECK (status IN ('COLETANDO', 'PROCESSANDO', 'CONCLUIDA', 'FALHOU', 'EXPIRADA')),
  rodada               smallint NOT NULL DEFAULT 0,
  mensagens_acumuladas jsonb NOT NULL DEFAULT '[]'::jsonb,
  chamado_id           uuid REFERENCES chamados(id) ON DELETE SET NULL,
  criada_em            timestamptz NOT NULL DEFAULT now(),
  atualizada_em        timestamptz NOT NULL DEFAULT now()
);

-- Só uma conversa "aberta" por telefone por vez — upsert na chegada de
-- mensagem depende dessa garantia (ver resolver/abrir conversa em
-- app/ia/whatsapp_intake.py).
CREATE UNIQUE INDEX IF NOT EXISTS idx_whatsapp_conversas_telefone_aberta
  ON whatsapp_conversas (telefone)
  WHERE status IN ('COLETANDO', 'PROCESSANDO');

CREATE INDEX IF NOT EXISTS idx_whatsapp_conversas_perfil ON whatsapp_conversas (perfil_id);

COMMENT ON TABLE whatsapp_conversas IS
  'Estado da conversa de intake de chamado via WhatsApp (coleta de dados pela IA até ter informação suficiente). Escrita só via admin_connection().';

CREATE TABLE IF NOT EXISTS whatsapp_mensagens_recebidas (
  id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  wamid         text UNIQUE NOT NULL,
  telefone      text NOT NULL,
  tipo          text NOT NULL,
  corpo         text,
  midia_id      text,
  conversa_id   uuid REFERENCES whatsapp_conversas(id) ON DELETE SET NULL,
  status        text NOT NULL DEFAULT 'PENDENTE'
                  CHECK (status IN ('PENDENTE', 'PROCESSANDO', 'PROCESSADA', 'SEM_PERFIL', 'ERRO')),
  created_at    timestamptz NOT NULL DEFAULT now(),
  processado_em timestamptz
);

CREATE INDEX IF NOT EXISTS idx_whatsapp_msgs_pendentes
  ON whatsapp_mensagens_recebidas (status, created_at)
  WHERE status IN ('PENDENTE', 'PROCESSANDO');

COMMENT ON TABLE whatsapp_mensagens_recebidas IS
  'Âncora durável de cada mensagem recebida no webhook do WhatsApp (idempotência por wamid + base da reconciliação de intake perdido). Escrita só via admin_connection().';

ALTER TABLE whatsapp_conversas ENABLE ROW LEVEL SECURITY;
ALTER TABLE whatsapp_mensagens_recebidas ENABLE ROW LEVEL SECURITY;

-- Sem policies: intencional, mesmo padrão de ia_triagens (0050).

COMMIT;
