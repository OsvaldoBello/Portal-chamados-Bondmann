-- Fase 3 / Seção 5.1 — Avaliação do chamado (CSAT 1–5) pelo CLIENTE autor
--
-- Regra de negócio (aprovação do protótipo): a pessoa que ABRIU o chamado
-- pode avaliá-lo de 1 a 5 estrelas. A avaliação só é permitida quando o
-- chamado está RESOLVIDO e só pelo próprio autor (cliente_id = auth.uid()).
-- Fonte canônica do KPI CSAT (Seção 6 / Fase 5), sem depender de e-mail.

-- ============================================================
-- 1. Colunas de avaliação em chamados
-- ============================================================
ALTER TABLE chamados
  ADD COLUMN avaliacao_nota       smallint
    CONSTRAINT chamados_avaliacao_nota_range CHECK (avaliacao_nota BETWEEN 1 AND 5),
  ADD COLUMN avaliacao_comentario text,
  ADD COLUMN avaliacao_em         timestamptz;

-- Coerência: comentário/timestamp só existem com nota; nota implica timestamp.
ALTER TABLE chamados
  ADD CONSTRAINT chamados_avaliacao_coerente CHECK (
    (avaliacao_nota IS NULL AND avaliacao_em IS NULL AND avaliacao_comentario IS NULL)
    OR (avaliacao_nota IS NOT NULL AND avaliacao_em IS NOT NULL)
  );

-- Índice para o KPI CSAT (média de notas por período), só linhas avaliadas.
CREATE INDEX idx_chamados_avaliacao ON chamados(avaliacao_nota) WHERE avaliacao_nota IS NOT NULL;

-- ============================================================
-- 2. RLS — CLIENTE autor pode UPDATE apenas em chamado RESOLVIDO próprio
--    (políticas permissivas são OR'd; OPERADOR continua via policy própria)
-- ============================================================
CREATE POLICY chamados_update_cliente_avaliacao ON chamados
  FOR UPDATE TO authenticated
  USING (auth_role() = 'CLIENTE' AND cliente_id = auth.uid() AND status = 'RESOLVIDO')
  WITH CHECK (auth_role() = 'CLIENTE' AND cliente_id = auth.uid() AND status = 'RESOLVIDO');

-- ============================================================
-- 3. Trava de coluna: CLIENTE só altera nota/comentário/timestamp da avaliação.
--    RLS não restringe colunas; este trigger garante que o UPDATE do CLIENTE
--    não mude status/prioridade/operador/título/etc. (defesa em profundidade).
-- ============================================================
CREATE OR REPLACE FUNCTION enforce_cliente_so_avaliacao()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = public
AS $$
BEGIN
  IF auth_role() = 'CLIENTE' THEN
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

CREATE TRIGGER chamados_cliente_so_avaliacao
  BEFORE UPDATE ON chamados
  FOR EACH ROW EXECUTE FUNCTION enforce_cliente_so_avaliacao();

-- Hardening (Seção 0005): function de trigger não é chamável como RPC.
REVOKE ALL ON FUNCTION enforce_cliente_so_avaliacao() FROM PUBLIC, anon, authenticated;
