-- 0029_segregacao_autor_atendimento.sql
-- Decisão de produto 2026-07-09 (segregação de função, vale para TODOS os
-- departamentos — TI/RH/Marketing — não é regra específica de setor):
--   * O AUTOR de um chamado nunca pode ser o operador responsável pelo próprio
--     chamado, mesmo sendo staff do departamento de destino (ex.: alguém de TI
--     abre um chamado para TI — só um COLEGA de TI pode assumir).
--   * O chamado fica só leitura para TODO o setor (inclusive pra quem não é o
--     autor) até alguém iniciar o atendimento (`operador_id` preenchido). A
--     partir daí, qualquer pessoa do setor (exceto o autor) pode responder.
--   * O autor continua podendo responder ao próprio chamado como SOLICITANTE
--     (mensagem pública, ramo `cliente_id = auth.uid()` — inalterado).
--
-- Reforça em app/repositories/chamados.py (iniciar_atendimento/atribuir) — as
-- policies abaixo são defesa em profundidade no banco.

BEGIN;

-- 0) Corrige estado pré-existente que já violaria a regra (chamado assumido
--    pelo próprio autor antes desta migration) — devolve para "não assumido"
--    (e para NOVO, se estava EM_ATENDIMENTO) para que o setor possa reivindicar.
UPDATE chamados
   SET operador_id = NULL,
       status = CASE WHEN status = 'EM_ATENDIMENTO' THEN 'NOVO'::status_chamado ELSE status END
 WHERE operador_id IS NOT NULL AND operador_id = cliente_id;

-- 1) UPDATE de chamados: reforça no banco que operador_id nunca pode ficar
--    igual ao cliente_id (claim/atribuição do próprio autor).
DROP POLICY chamados_update_staff ON chamados;
CREATE POLICY chamados_update_staff ON chamados
  FOR UPDATE TO authenticated
  USING (
    auth_departamento_id() IS NOT NULL AND departamento_id = auth_departamento_id()
  )
  WITH CHECK (
    (operador_id IS NULL OR operador_id <> cliente_id)
    AND (
      auth_is_ti()
      OR (auth_departamento_id() IS NOT NULL AND departamento_id = auth_departamento_id())
    )
  );

-- 2) INSERT de mensagens pelo staff do setor: só depois que ALGUÉM (que não o
--    autor) iniciar o atendimento (`operador_id IS NOT NULL`), e nunca pelo
--    próprio autor por este ramo. O ramo do solicitante (autor respondendo
--    como cliente, mensagem pública) continua inalterado.
DROP POLICY mensagens_insert ON mensagens;
CREATE POLICY mensagens_insert ON mensagens
  FOR INSERT TO authenticated
  WITH CHECK (
    remetente_id = auth.uid()
    AND EXISTS (
      SELECT 1 FROM chamados c
      WHERE c.id = mensagens.chamado_id
        AND (
          (
            auth_departamento_id() IS NOT NULL
            AND c.departamento_id = auth_departamento_id()
            AND c.operador_id IS NOT NULL
            AND c.cliente_id <> mensagens.remetente_id
          )
          OR (c.cliente_id = auth.uid() AND mensagens.is_interna = false)
        )
    )
  );

COMMIT;
