-- 0097_observadores_podem_comentar.sql
-- Permite que usuários em cópia (observadores) enviem mensagens públicas no chamado.
-- Atualiza a policy de inserção em mensagens e a trigger de status RESPOSTA_CLIENTE.

BEGIN;

DROP POLICY IF EXISTS mensagens_insert ON mensagens;
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
            AND (c.cliente_id <> mensagens.remetente_id OR d.autoatendimento)
          )
          OR (
            (c.cliente_id = auth.uid() OR auth_eh_observador(c.id))
            AND mensagens.is_interna = false
          )
        )
    )
  );

-- Atualiza a função da trigger de transição de status para RESPOSTA_CLIENTE (0061)
-- para considerar mensagens de observadores como do lado cliente/demandante.
CREATE OR REPLACE FUNCTION mover_status_resposta_cliente()
RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path TO 'public' AS $$
DECLARE
  v_cliente_id uuid;
  v_status     status_chamado;
  v_dep_nome   text;
  v_is_demandante boolean;
BEGIN
  SELECT c.cliente_id, c.status, d.nome
    INTO v_cliente_id, v_status, v_dep_nome
    FROM chamados c
    LEFT JOIN departamentos d ON d.id = c.departamento_id
   WHERE c.id = NEW.chamado_id
   FOR UPDATE OF c;

  IF v_dep_nome IS NULL OR v_dep_nome NOT IN ('TI', 'RH') THEN
    RETURN NEW;
  END IF;

  v_is_demandante := (NEW.remetente_id = v_cliente_id) OR EXISTS (
    SELECT 1 FROM chamados_observadores
    WHERE chamado_id = NEW.chamado_id AND perfil_id = NEW.remetente_id
  );

  IF v_is_demandante THEN
    IF v_status IN ('EM_ATENDIMENTO', 'AGUARDANDO') THEN
      UPDATE chamados SET status = 'RESPOSTA_CLIENTE' WHERE id = NEW.chamado_id;
      INSERT INTO historico_chamados (chamado_id, ator_id, acao, detalhes)
      VALUES (
        NEW.chamado_id, NEW.remetente_id, 'STATUS_ALTERADO',
        jsonb_build_object('de', v_status, 'para', 'RESPOSTA_CLIENTE', 'motivo', 'mensagem_autor')
      );
    END IF;
  ELSE
    IF v_status = 'RESPOSTA_CLIENTE' THEN
      UPDATE chamados SET status = 'EM_ATENDIMENTO' WHERE id = NEW.chamado_id;
      INSERT INTO historico_chamados (chamado_id, ator_id, acao, detalhes)
      VALUES (
        NEW.chamado_id, NEW.remetente_id, 'STATUS_ALTERADO',
        jsonb_build_object('de', 'RESPOSTA_CLIENTE', 'para', 'EM_ATENDIMENTO', 'motivo', 'resposta_staff')
      );
    END IF;
  END IF;
  RETURN NEW;
END;
$$;

COMMIT;
