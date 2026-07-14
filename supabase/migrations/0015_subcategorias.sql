-- 0015_subcategorias — Fase 2 / Seção 5.1 / 3.9 — subcategorias + anexos na abertura
--
-- Nova tabela `subcategorias` (catálogo global gerido pelo TI via `auth_is_ti()`),
-- coluna `chamados.subcategoria_id` e trava de coluna do CLIENTE estendida.
--
-- ⚠️ RECONSTRUÇÃO (2026-07-14, Sprint 0 / item 0.1 do plano de melhorias): o
-- arquivo original desta migration nunca chegou a ser commitado no git (a
-- cadeia local pulava de 0014 direto para 0016), embora ela estivesse de fato
-- aplicada em produção (`iurlzlhbnoemkzgexcfk`, registrada em
-- `supabase_migrations.schema_migrations` como `20260703224156`). O DDL abaixo
-- foi reconstruído por introspecção da produção (colunas, FKs, índices,
-- policies e o corpo atual de `enforce_cliente_so_avaliacao`), portanto é
-- estruturalmente idêntico ao estado real. A ÚNICA parte não recuperável é o
-- **conteúdo exato do seed original** (categorias "Acessos/Dúvida/Equipamento/
-- Financeiro" com 5/3/5/4 subcategorias, citadas no changelog do plano mestre
-- de 2026-07-03): esses dados foram substituídos em produção pela migration
-- `0018_reseed_categorias_subcategorias` antes desta reconstrução, e são
-- irrelevantes para o estado final do banco (0018 apaga e reinsere tudo). O
-- seed abaixo usa nomes de subcategoria placeholder só para manter a cadeia
-- 0001→0044 aplicável do zero sem violar constraints; não representa dado
-- real de produção.

-- ============================================================
-- 1. Tabela subcategorias
-- ============================================================
CREATE TABLE subcategorias (
  id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  categoria_id uuid NOT NULL REFERENCES categorias(id) ON DELETE CASCADE,
  nome         text NOT NULL,
  ativo        boolean NOT NULL DEFAULT true,
  created_at   timestamptz NOT NULL DEFAULT now(),
  UNIQUE (categoria_id, nome)
);

CREATE INDEX idx_subcategorias_categoria ON subcategorias(categoria_id) WHERE ativo;

-- ============================================================
-- 2. RLS — catálogo global: todo autenticado lê; só o TI gerencia
-- ============================================================
ALTER TABLE subcategorias ENABLE ROW LEVEL SECURITY;

CREATE POLICY subcategorias_select ON subcategorias
  FOR SELECT TO authenticated
  USING (true);

CREATE POLICY subcategorias_admin_all ON subcategorias
  FOR ALL TO authenticated
  USING (auth_is_ti())
  WITH CHECK (auth_is_ti());

REVOKE ALL ON subcategorias FROM anon;
GRANT SELECT, INSERT, UPDATE, DELETE ON subcategorias TO authenticated;

-- ============================================================
-- 3. chamados.subcategoria_id — NULLABLE no banco (preserva chamados
--    antigos sem subcategoria); obrigatória na rota de abertura.
-- ============================================================
ALTER TABLE chamados
  ADD COLUMN subcategoria_id uuid REFERENCES subcategorias(id) ON DELETE RESTRICT;

CREATE INDEX idx_chamados_subcategoria ON chamados(subcategoria_id);

-- ============================================================
-- 4. Trava de coluna do CLIENTE — estende a lista imutável com
--    subcategoria_id (autor não pode reclassificar o próprio chamado).
-- ============================================================
CREATE OR REPLACE FUNCTION enforce_cliente_so_avaliacao()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = public
AS $$
BEGIN
  IF auth_role() = 'CLIENTE' THEN
    IF ROW(NEW.codigo, NEW.empresa_id, NEW.departamento_id, NEW.cliente_id, NEW.operador_id,
           NEW.categoria_id, NEW.subcategoria_id, NEW.titulo, NEW.descricao, NEW.status, NEW.prioridade,
           NEW.limite_resposta, NEW.limite_resolucao, NEW.respondido_em,
           NEW.resolvido_em, NEW.created_at)
       IS DISTINCT FROM
       ROW(OLD.codigo, OLD.empresa_id, OLD.departamento_id, OLD.cliente_id, OLD.operador_id,
           OLD.categoria_id, OLD.subcategoria_id, OLD.titulo, OLD.descricao, OLD.status, OLD.prioridade,
           OLD.limite_resposta, OLD.limite_resolucao, OLD.respondido_em,
           OLD.resolvido_em, OLD.created_at)
    THEN
      RAISE EXCEPTION 'CLIENTE só pode alterar a avaliação do chamado (nota/comentário).';
    END IF;
  END IF;
  RETURN NEW;
END;
$$;
REVOKE ALL ON FUNCTION enforce_cliente_so_avaliacao() FROM PUBLIC, anon, authenticated;

-- ============================================================
-- 5. Seed placeholder (ver nota de reconstrução no topo do arquivo) — só
--    para manter a cadeia de migrations aplicável do zero; substituído
--    integralmente pela migration 0018.
-- ============================================================
INSERT INTO categorias (nome)
SELECT v.nome FROM (VALUES ('Acessos'), ('Dúvida'), ('Equipamento'), ('Financeiro')) AS v(nome)
WHERE NOT EXISTS (SELECT 1 FROM categorias c WHERE c.nome = v.nome);

INSERT INTO subcategorias (categoria_id, nome)
SELECT c.id, v.sub
FROM categorias c
JOIN (VALUES
  ('Acessos', 'Liberação de acesso'),
  ('Acessos', 'Bloqueio de acesso'),
  ('Acessos', 'Reset de senha'),
  ('Acessos', 'Acesso a pasta/sistema'),
  ('Acessos', 'Acesso remoto'),
  ('Dúvida', 'Dúvida de uso'),
  ('Dúvida', 'Dúvida sobre processo'),
  ('Dúvida', 'Orientação geral'),
  ('Equipamento', 'Solicitação de equipamento'),
  ('Equipamento', 'Troca de equipamento'),
  ('Equipamento', 'Manutenção de equipamento'),
  ('Equipamento', 'Instalação de equipamento'),
  ('Equipamento', 'Devolução de equipamento'),
  ('Financeiro', 'Cobrança'),
  ('Financeiro', 'Reembolso'),
  ('Financeiro', 'Nota fiscal'),
  ('Financeiro', 'Pagamento')
) AS v(cat, sub) ON v.cat = c.nome
WHERE NOT EXISTS (
  SELECT 1 FROM subcategorias sx WHERE sx.categoria_id = c.id AND sx.nome = v.sub
);
