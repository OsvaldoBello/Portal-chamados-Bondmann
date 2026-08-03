-- 0072_fix_resposta_cliente_trava_coluna.sql
-- Corrige o conflito entre a 0061 (status automático RESPOSTA_CLIENTE) e a
-- trava de coluna do CLIENTE (0006/0059): responder no chat quebrava com
-- "Erro interno" (500) para o AUTOR do chamado.
--
-- Cadeia do bug (produção, 2026-08-03, BOND-2026-00645):
--   1. o autor (papel CLIENTE) insere mensagem pública -> OK;
--   2. `trg_mover_status_resposta_cliente` (0061, AFTER INSERT) roda e, por ser
--      chamado de TI/RH em EM_ATENDIMENTO/AGUARDANDO, faz
--      `UPDATE chamados SET status = 'RESPOSTA_CLIENTE'`;
--   3. esse UPDATE dispara `enforce_cliente_so_avaliacao` (BEFORE UPDATE), que
--      vê `status` diferente e levanta exceção -> a transação inteira aborta e
--      a mensagem nem é gravada.
--
-- O SECURITY DEFINER da 0061 resolve RLS (privilégio de tabela), mas NÃO muda
-- `auth_role()`: ele lê `perfis` pelo `request.jwt.claims` da sessão, que
-- continua sendo o do autor. Ou seja: a transição é do SISTEMA, mas chega na
-- trava com a cara do CLIENTE. Efeito colateral: a coluna "Última Interação do
-- Usuário" (0060/0061) nunca funcionou — zero linhas em `historico_chamados`
-- com `motivo = 'mensagem_autor'` desde 2026-07-24.
--
-- Por que liberar a transição aqui é seguro: nenhuma policy de UPDATE do
-- CLIENTE alcança um chamado fora de RESOLVIDO (`chamados_update_cliente_avaliacao`
-- e `chamados_update_cliente_reabertura`, ambas com `status = 'RESOLVIDO'` no
-- USING). Um UPDATE direto do funcionário para RESPOSTA_CLIENTE não encontra
-- linha nenhuma — este caminho só é alcançável pelo trigger da 0061, que roda
-- como dono da tabela.
--
-- A comparação das demais colunas virou uma variável única (era duplicada por
-- ramo): com um terceiro caminho permitido, três listas idênticas de ~17
-- colunas se desencontrariam na próxima coluna nova — e coluna esquecida nessa
-- lista nasce LIBERADA para o autor (ver `tests/e2e/test_rls_combinacao.py`).
CREATE OR REPLACE FUNCTION enforce_cliente_so_avaliacao()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = public
AS $$
DECLARE
  v_outras_colunas_mudaram boolean;
BEGIN
  -- `IS DISTINCT FROM`: com claims ausentes (`admin_connection`, jobs) auth_role()
  -- é NULL, e um `<>` devolveria NULL, caindo nas checagens como se fosse CLIENTE.
  IF auth_role() IS DISTINCT FROM 'CLIENTE' THEN
    RETURN NEW;
  END IF;

  v_outras_colunas_mudaram :=
    ROW(NEW.codigo, NEW.empresa_id, NEW.cliente_id, NEW.operador_id, NEW.categoria_id,
        NEW.titulo, NEW.descricao, NEW.prioridade,
        NEW.limite_resposta, NEW.limite_resolucao, NEW.respondido_em,
        NEW.created_at,
        NEW.chamado_principal_id, NEW.combinado_em, NEW.combinado_por,
        NEW.prazo_projeto_dias, NEW.projeto_em)
    IS DISTINCT FROM
    ROW(OLD.codigo, OLD.empresa_id, OLD.cliente_id, OLD.operador_id, OLD.categoria_id,
        OLD.titulo, OLD.descricao, OLD.prioridade,
        OLD.limite_resposta, OLD.limite_resolucao, OLD.respondido_em,
        OLD.created_at,
        OLD.chamado_principal_id, OLD.combinado_em, OLD.combinado_por,
        OLD.prazo_projeto_dias, OLD.projeto_em);

  -- Reabertura pelo autor (0059): RESOLVIDO -> EM_ATENDIMENTO, zerando resolvido_em.
  IF OLD.status = 'RESOLVIDO' AND NEW.status = 'EM_ATENDIMENTO' THEN
    IF v_outras_colunas_mudaram THEN
      RAISE EXCEPTION 'CLIENTE só pode alterar a avaliação do chamado (nota/comentário) ou reabri-lo.';
    END IF;
    IF NEW.resolvido_em IS NOT NULL THEN
      RAISE EXCEPTION 'Reabertura deve limpar resolvido_em.';
    END IF;
    RETURN NEW;
  END IF;

  -- Transição do SISTEMA (0061): mensagem do autor no chat de TI/RH.
  IF NEW.status = 'RESPOSTA_CLIENTE' AND OLD.status IN ('EM_ATENDIMENTO', 'AGUARDANDO') THEN
    IF v_outras_colunas_mudaram OR NEW.resolvido_em IS DISTINCT FROM OLD.resolvido_em THEN
      RAISE EXCEPTION 'CLIENTE só pode alterar a avaliação do chamado (nota/comentário).';
    END IF;
    RETURN NEW;
  END IF;

  -- Demais UPDATEs do autor: só nota/comentário/avaliacao_em podem mudar.
  IF v_outras_colunas_mudaram
     OR NEW.status IS DISTINCT FROM OLD.status
     OR NEW.resolvido_em IS DISTINCT FROM OLD.resolvido_em
  THEN
    RAISE EXCEPTION 'CLIENTE só pode alterar a avaliação do chamado (nota/comentário).';
  END IF;
  RETURN NEW;
END;
$$;
