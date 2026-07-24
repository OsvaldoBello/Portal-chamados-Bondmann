-- 0061_status_resposta_cliente_trigger.sql
-- Trigger que materializa a coluna "Última Interação do Usuário" (0060) sem
-- depender de nenhuma rota Python: qualquer INSERT de mensagem PÚBLICA (nota
-- interna nunca conta — o autor nem enxerga) dispara a checagem.
--
-- Regra (pedido do usuário, 2026-07-24): se a última mensagem do chat foi de
-- quem ABRIU o chamado (mensagens.remetente_id = chamados.cliente_id), o
-- chamado vai automaticamente para RESPOSTA_CLIENTE; quando o setor responde
-- (remetente ≠ autor), volta para EM_ATENDIMENTO. Só se aplica a TI/RH (nome
-- hardcoded, mesmo padrão da 0038/0042/0044 para exceções por setor) e só
-- sai de EM_ATENDIMENTO/AGUARDANDO — NOVO (ainda não olhado) e RESOLVIDO/
-- PROJETOS (fora do fluxo reativo clássico, 0057) ficam de fora.
--
-- Por quê trigger (e não código Python nos repositórios de mensagem): a RLS
-- do CLIENTE não permite UPDATE de `status` fora da avaliação (0006) e da
-- reabertura (0059) — a mudança automática aqui é uma ação do SISTEMA, não
-- uma permissão do papel CLIENTE, então precisa rodar com privilégio próprio.
-- SECURITY DEFINER (dono = role de migration, a mesma que já bypassa RLS por
-- ser dona das tabelas, sem FORCE ROW LEVEL SECURITY em lugar nenhum do
-- projeto) é o mesmo mecanismo de `auth_is_ti()`/`auth_departamento_autoatendimento()`.
-- A trigger `sla_pausa_aguardando` (0017/0044) já cuida de retomar o relógio
-- de SLA quando o status sai de AGUARDANDO/AGUARDANDO_TERCEIROS — como
-- RESPOSTA_CLIENTE não entra nessa lista, a transição AGUARDANDO ->
-- RESPOSTA_CLIENTE já dispara a retomada normalmente, sem mexer em 0017/0044.
CREATE OR REPLACE FUNCTION mover_status_resposta_cliente()
RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path TO 'public' AS $$
DECLARE
  v_cliente_id uuid;
  v_status     status_chamado;
  v_dep_nome   text;
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

  IF NEW.remetente_id = v_cliente_id THEN
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

REVOKE EXECUTE ON FUNCTION mover_status_resposta_cliente() FROM public, anon, authenticated;

DROP TRIGGER IF EXISTS trg_mover_status_resposta_cliente ON mensagens;
CREATE TRIGGER trg_mover_status_resposta_cliente
  AFTER INSERT ON mensagens
  FOR EACH ROW
  WHEN (NEW.is_interna = false)
  EXECUTE FUNCTION mover_status_resposta_cliente();
