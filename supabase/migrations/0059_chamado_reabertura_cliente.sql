-- 0059_chamado_reabertura_cliente.sql
-- Reabertura pelo autor (pedido do usuário, 2026-07-24): se o CLIENTE não
-- ficou satisfeito com a solução, pode devolver o chamado RESOLVIDO para
-- EM_ATENDIMENTO (reabre o chat e a fila do setor) em vez de só avaliar mal.
--
-- Mesmo padrão de defesa em profundidade da `0006` (avaliação): a RLS libera
-- a LINHA (USING/WITH CHECK), e o trigger `enforce_cliente_so_avaliacao`
-- restringe as COLUNAS que o CLIENTE pode tocar num UPDATE próprio — agora
-- com um segundo caminho permitido (a transição RESOLVIDO -> EM_ATENDIMENTO),
-- além da avaliação (nota/comentário/timestamp, já livres desde a `0006`).

-- ============================================================
-- 1. RLS — CLIENTE autor pode UPDATE (RESOLVIDO -> EM_ATENDIMENTO) no próprio
--    chamado. Múltiplas policies permissivas de UPDATE são combinadas com OR
--    tanto no USING quanto no WITH CHECK (mesmo comando) — a combinação com
--    `chamados_update_cliente_avaliacao` (0006) é intencional: o autor de um
--    RESOLVIDO pode ou manter o status (avaliar) ou movê-lo para
--    EM_ATENDIMENTO (reabrir); o trigger abaixo é quem trava as demais
--    colunas em ambos os casos.
-- ============================================================
CREATE POLICY chamados_update_cliente_reabertura ON chamados
  FOR UPDATE TO authenticated
  USING (auth_role() = 'CLIENTE' AND cliente_id = auth.uid() AND status = 'RESOLVIDO')
  WITH CHECK (auth_role() = 'CLIENTE' AND cliente_id = auth.uid() AND status = 'EM_ATENDIMENTO');

-- ============================================================
-- 2. Trava de coluna: além de nota/comentário/avaliacao_em (0006), o CLIENTE
--    agora também pode mudar `status` — só na transição RESOLVIDO ->
--    EM_ATENDIMENTO — e `resolvido_em`, que precisa voltar a NULL junto.
--    Qualquer outra coluna (operador, prioridade, categoria, etc.) continua
--    bloqueada, exatamente como antes.
-- ============================================================
CREATE OR REPLACE FUNCTION enforce_cliente_so_avaliacao()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = public
AS $$
BEGIN
  IF auth_role() = 'CLIENTE' THEN
    IF OLD.status = 'RESOLVIDO' AND NEW.status = 'EM_ATENDIMENTO' THEN
      IF ROW(NEW.codigo, NEW.empresa_id, NEW.cliente_id, NEW.operador_id, NEW.categoria_id,
             NEW.titulo, NEW.descricao, NEW.prioridade,
             NEW.limite_resposta, NEW.limite_resolucao, NEW.respondido_em,
             NEW.created_at)
         IS DISTINCT FROM
         ROW(OLD.codigo, OLD.empresa_id, OLD.cliente_id, OLD.operador_id, OLD.categoria_id,
             OLD.titulo, OLD.descricao, OLD.prioridade,
             OLD.limite_resposta, OLD.limite_resolucao, OLD.respondido_em,
             OLD.created_at)
      THEN
        RAISE EXCEPTION 'CLIENTE só pode alterar a avaliação do chamado (nota/comentário) ou reabri-lo.';
      END IF;
      IF NEW.resolvido_em IS NOT NULL THEN
        RAISE EXCEPTION 'Reabertura deve limpar resolvido_em.';
      END IF;
      RETURN NEW;
    END IF;

    IF ROW(NEW.codigo, NEW.empresa_id, NEW.cliente_id, NEW.operador_id, NEW.categoria_id,
           NEW.titulo, NEW.descricao, NEW.status, NEW.prioridade,
           NEW.limite_resposta, NEW.limite_resolucao, NEW.respondido_em,
           NEW.resolvido_em, NEW.created_at)
       IS DISTINCT FROM
       ROW(OLD.codigo, OLD.empresa_id, OLD.cliente_id, OLD.operador_id, OLD.categoria_id,
           OLD.titulo, OLD.descricao, OLD.status, OLD.prioridade,
           OLD.limite_resposta, OLD.limite_resolucao, OLD.respondido_em,
           OLD.resolvido_em, OLD.created_at)
    THEN
      RAISE EXCEPTION 'CLIENTE só pode alterar a avaliação do chamado (nota/comentário).';
    END IF;
  END IF;
  RETURN NEW;
END;
$$;
