-- 0055_notificacoes_vistas.sql
-- Controle de "visto" para a bolinha vermelha do sino (2026-07-23).
--
-- Até aqui a bolinha (`data-notif-novo`, ver _notificacoes.html) era 100%
-- derivada do STATUS do chamado (NOVO / RESOLVIDO-sem-avaliação) — abrir o
-- chamado não mudava nada, então um chamado NOVO já atribuído a um operador
-- mas sem "Iniciar atendimento" clicado mantinha a bolinha acesa pra sempre,
-- mesmo depois de o usuário já ter aberto e conferido o chamado. Esta tabela
-- guarda, por usuário, o `updated_at` do chamado no momento em que ele foi
-- aberto — não um timestamp de parede: comparar contra o próprio
-- `chamados.updated_at` (que o trigger `set_timestamp_chamados` já mantém)
-- evita depender de relógio de cliente e recalcula sozinho quando o chamado
-- muda de novo depois de visto (reatribuição, nova mensagem, reabertura etc.).
BEGIN;

CREATE TABLE IF NOT EXISTS chamados_notificacoes_vistas (
  chamado_id          uuid NOT NULL REFERENCES chamados(id) ON DELETE CASCADE,
  perfil_id           uuid NOT NULL REFERENCES perfis(id) ON DELETE CASCADE,
  chamado_updated_em  timestamptz NOT NULL,
  visto_em            timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (chamado_id, perfil_id)
);
CREATE INDEX idx_chamados_notif_vistas_perfil ON chamados_notificacoes_vistas(perfil_id);

ALTER TABLE chamados_notificacoes_vistas ENABLE ROW LEVEL SECURITY;
GRANT SELECT, INSERT, UPDATE ON chamados_notificacoes_vistas TO authenticated;

-- Cada usuário só enxerga/grava a própria marca de "visto" — não precisa
-- checar visibilidade do chamado em si (marcar um chamado_id qualquer como
-- visto não vaza nenhum dado; a query de notificações já é escopada por RLS
-- em `chamados`).
CREATE POLICY chamados_notif_vistas_select ON chamados_notificacoes_vistas
  FOR SELECT TO authenticated
  USING (perfil_id = auth.uid());

CREATE POLICY chamados_notif_vistas_insert ON chamados_notificacoes_vistas
  FOR INSERT TO authenticated
  WITH CHECK (perfil_id = auth.uid());

CREATE POLICY chamados_notif_vistas_update ON chamados_notificacoes_vistas
  FOR UPDATE TO authenticated
  USING (perfil_id = auth.uid())
  WITH CHECK (perfil_id = auth.uid());

COMMIT;
