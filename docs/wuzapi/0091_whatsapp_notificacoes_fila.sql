-- 0091_whatsapp_notificacoes_fila.sql  (PROPOSTA — docs/wuzapi/)
-- Outbox das notificações ativas de chamado no WhatsApp (migração para
-- WUZAPI/whatsmeow, 2026-09-01).
--
-- Por que fila em tabela e não `asyncio.Queue`: as notificações passam a sair
-- por um número comum (não mais pela Cloud API), e disparar em rajada é o
-- caminho mais curto para o bloqueio. Um único worker consome esta tabela
-- respeitando intervalo mínimo + jitter, então a cadência não depende de
-- quantos operadores clicaram "responder" no mesmo segundo. Em tabela, e não
-- em memória, pelo mesmo motivo que `whatsapp_mensagens_recebidas` existe
-- (0086): task em memória some num restart sem deixar rastro — e aqui sumir
-- significa o solicitante nunca saber que o chamado dele foi respondido.
--
-- `dedup_key` com índice único PARCIAL (só enquanto PENDENTE): dois updates
-- no mesmo chamado antes do envio viram uma notificação só; depois de
-- enviada, o mesmo chamado pode notificar de novo.
--
-- RLS: habilitada SEM policy — leitura/escrita exclusivas de
-- `app/db.py::admin_connection()` (mesmo padrão de 0086).

BEGIN;

CREATE TABLE IF NOT EXISTS whatsapp_notificacoes_fila (
  id             bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  telefone       text NOT NULL,
  corpo          text NOT NULL,
  perfil_id      uuid REFERENCES perfis(id) ON DELETE CASCADE,
  chamado_id     uuid REFERENCES chamados(id) ON DELETE CASCADE,
  dedup_key      text,
  status         text NOT NULL DEFAULT 'PENDENTE'
                   CHECK (status IN ('PENDENTE', 'ENVIANDO', 'ENVIADA', 'FALHOU', 'DESCARTADA')),
  tentativas     smallint NOT NULL DEFAULT 0,
  agendada_para  timestamptz NOT NULL DEFAULT now(),
  criada_em      timestamptz NOT NULL DEFAULT now(),
  enviada_em     timestamptz,
  erro           text
);

-- Varredura do worker: só as pendentes vencidas, na ordem de agendamento.
CREATE INDEX IF NOT EXISTS idx_wa_notif_pendentes
  ON whatsapp_notificacoes_fila (agendada_para, id)
  WHERE status = 'PENDENTE';

-- Coalescência: uma pendente por chave (ex.: 'chamado:<uuid>:resposta').
CREATE UNIQUE INDEX IF NOT EXISTS idx_wa_notif_dedup_pendente
  ON whatsapp_notificacoes_fila (dedup_key)
  WHERE status = 'PENDENTE' AND dedup_key IS NOT NULL;

-- Throttle por destinatário: "quando foi a última que saiu para este número".
CREATE INDEX IF NOT EXISTS idx_wa_notif_telefone_enviada
  ON whatsapp_notificacoes_fila (telefone, enviada_em DESC)
  WHERE status = 'ENVIADA';

COMMENT ON TABLE whatsapp_notificacoes_fila IS
  'Outbox das notificações ativas de chamado no WhatsApp — consumida por um worker único com rate limit e jitter (anti-flood/anti-banimento). Escrita só via admin_connection().';

ALTER TABLE whatsapp_notificacoes_fila ENABLE ROW LEVEL SECURITY;
-- Sem policies: intencional, mesmo padrão de whatsapp_conversas (0086).

COMMIT;
