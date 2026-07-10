-- 0038_marketing_autoatendimento.sql
-- Decisão de produto 2026-07-10: a segregação de função da 0029 ("autor nunca
-- atende o próprio chamado") não se aplica ao Marketing. Diferente de TI/RH
-- (suporte: alguém de fora abre, o setor atende), o Marketing usa o
-- Kanban/fila como um quadro estilo Trello — o próprio time cria e gerencia as
-- demandas, então o autor PODE ser o responsável pela própria. Reforça em
-- app/repositories/chamados.py (iniciar_atendimento/atribuir) e
-- app/routes/workspace.py (pode_atender/pode_reivindicar) — as policies abaixo
-- são defesa em profundidade no banco, mesmo padrão da 0029.

BEGIN;

-- 1) UPDATE de chamados: operador_id só pode igualar cliente_id (autoatendimento)
--    quando o destino é o Marketing.
DROP POLICY chamados_update_staff ON chamados;
CREATE POLICY chamados_update_staff ON chamados
  FOR UPDATE TO authenticated
  USING (
    auth_departamento_id() IS NOT NULL AND departamento_id = auth_departamento_id()
  )
  WITH CHECK (
    (
      operador_id IS NULL
      OR operador_id <> cliente_id
      OR EXISTS (SELECT 1 FROM departamentos d WHERE d.id = departamento_id AND d.nome = 'Marketing')
    )
    AND (
      auth_is_ti()
      OR (auth_departamento_id() IS NOT NULL AND departamento_id = auth_departamento_id())
    )
  );

-- 2) INSERT de mensagens pelo staff: o ramo "operador_id preenchido, remetente
--    != autor" ganha uma segunda saída no Marketing (remetente pode ser o
--    autor) — continua exigindo que ALGUÉM já tenha iniciado o atendimento
--    (operador_id IS NOT NULL), inclusive o próprio autor via autoatendimento.
DROP POLICY mensagens_insert ON mensagens;
CREATE POLICY mensagens_insert ON mensagens
  FOR INSERT TO authenticated
  WITH CHECK (
    remetente_id = auth.uid()
    AND EXISTS (
      SELECT 1 FROM chamados c
      JOIN departamentos d ON d.id = c.departamento_id
      WHERE c.id = mensagens.chamado_id
        AND (
          (
            auth_departamento_id() IS NOT NULL
            AND c.departamento_id = auth_departamento_id()
            AND c.operador_id IS NOT NULL
            AND (c.cliente_id <> mensagens.remetente_id OR d.nome = 'Marketing')
          )
          OR (c.cliente_id = auth.uid() AND mensagens.is_interna = false)
        )
    )
  );

COMMIT;
