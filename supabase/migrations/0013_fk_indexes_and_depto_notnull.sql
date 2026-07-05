-- 0013_fk_indexes_and_depto_notnull.sql
-- Revisão do schema (2026-07-03): índices de cobertura para FKs sem índice
-- (advisor 0001_unindexed_foreign_keys) + integridade do eixo de roteamento.

-- FKs sem índice → joins/cascade mais rápidos (relevante à medida que cresce).
CREATE INDEX IF NOT EXISTS idx_chamados_cliente    ON chamados(cliente_id);
CREATE INDEX IF NOT EXISTS idx_chamados_categoria  ON chamados(categoria_id);
CREATE INDEX IF NOT EXISTS idx_mensagens_remetente ON mensagens(remetente_id);
CREATE INDEX IF NOT EXISTS idx_historico_ator      ON historico_chamados(ator_id);

-- departamento_id é o eixo de roteamento e sempre setado (a policy de INSERT já
-- exige NOT NULL; o backfill da 0008 preencheu). Formaliza a garantia no schema.
ALTER TABLE chamados ALTER COLUMN departamento_id SET NOT NULL;
