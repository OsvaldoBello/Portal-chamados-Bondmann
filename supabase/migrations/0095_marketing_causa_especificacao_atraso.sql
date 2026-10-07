-- 0095_marketing_causa_especificacao_atraso.sql
-- 1. Adiciona coluna de especificação de causa de atraso (Marketing).
-- 2. Normaliza causas de atraso legadas para a nomenclatura oficial em maiúsculas.
-- 3. Atualiza o trigger enforce_cliente_so_avaliacao para proteger causa_atraso
--    e especificacao_atraso contra alterações por solicitantes (CLIENTE).

BEGIN;

-- 1. Nova coluna para detalhes e especificações da causa do atraso
ALTER TABLE chamados ADD COLUMN IF NOT EXISTS especificacao_atraso text;

-- 2. Normalização dos dados históricos existentes
UPDATE chamados
   SET causa_atraso = 'SEM CAUSA REGISTRADA'
 WHERE causa_atraso ILIKE '%Sem causa%';

UPDATE chamados
   SET causa_atraso = 'AGUARDANDO DEFINIÇÃO INTERNA'
 WHERE causa_atraso ILIKE '%Aguardando definição%';

UPDATE chamados
   SET causa_atraso = 'DEPENDÊNCIA DE EXECUÇÃO INTERNA'
 WHERE causa_atraso ILIKE '%Dependência de execução%';

UPDATE chamados
   SET causa_atraso = 'DEPENDÊNCIA DE TERCEIROS'
 WHERE causa_atraso ILIKE '%Dependência de terceiros%';

-- 3. Atualização do trigger de integridade de autoria
CREATE OR REPLACE FUNCTION enforce_cliente_so_avaliacao()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = public
AS $$
DECLARE
  v_como_autor             boolean;
  v_outras_colunas_mudaram boolean;
BEGIN
  v_como_autor := COALESCE(
    auth_role() = 'CLIENTE'
    OR (
      OLD.cliente_id = auth.uid()
      AND NOT (auth_departamento_id() IS NOT NULL
               AND OLD.departamento_id = auth_departamento_id())
    ),
    false);

  IF NOT v_como_autor THEN
    RETURN NEW;
  END IF;

  v_outras_colunas_mudaram :=
    ROW(NEW.codigo, NEW.empresa_id, NEW.cliente_id, NEW.operador_id, NEW.categoria_id,
        NEW.titulo, NEW.descricao, NEW.prioridade,
        NEW.limite_resposta, NEW.limite_resolucao, NEW.respondido_em,
        NEW.created_at,
        NEW.chamado_principal_id, NEW.combinado_em, NEW.combinado_por,
        NEW.prazo_projeto_dias, NEW.projeto_em,
        NEW.causa_atraso, NEW.especificacao_atraso)
    IS DISTINCT FROM
    ROW(OLD.codigo, OLD.empresa_id, OLD.cliente_id, OLD.operador_id, OLD.categoria_id,
        OLD.titulo, OLD.descricao, OLD.prioridade,
        OLD.limite_resposta, OLD.limite_resolucao, OLD.respondido_em,
        OLD.created_at,
        OLD.chamado_principal_id, OLD.combinado_em, OLD.combinado_por,
        OLD.prazo_projeto_dias, OLD.projeto_em,
        OLD.causa_atraso, OLD.especificacao_atraso);

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

REVOKE EXECUTE ON FUNCTION enforce_cliente_so_avaliacao() FROM public, anon, authenticated;

COMMIT;
