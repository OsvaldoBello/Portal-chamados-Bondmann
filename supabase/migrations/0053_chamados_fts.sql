-- 0053_chamados_fts.sql — Busca de chamados semelhantes, F3 (plano_md_mestre_IA.md, Seções 4.4/5)
-- (0052 = avatares_admin_marketing, de outra frente — numeração conferida, regra C1 do plano IA.)
--
-- Índice de full-text search em português sobre título+descrição dos chamados.
-- Uso (Fase A da busca de semelhantes): a triagem por IA extrai `termos_busca`
-- do chamado novo e consulta os RESOLVIDOS do MESMO departamento (filtro
-- obrigatório no SQL — defesa em profundidade, a consulta roda na conexão
-- administrativa) para citar na nota interna "o chamado X teve problema
-- parecido; a solução registrada foi Y" (app/repositories/ia_busca.py).
--
-- A "resolução registrada" não é coluna: é a última mensagem pública de staff
-- do chamado resolvido, obtida no momento da busca (decisão da Seção 4.4 —
-- evita duplicar conteúdo; promover a coluna materializada só se ficar lento).
--
-- Coluna GENERATED: recalculada pelo Postgres a cada INSERT/UPDATE de
-- titulo/descricao — nenhum backfill nem trigger necessário (STORED cobre as
-- linhas existentes na própria migration).
--
-- Idempotente (reexecução não duplica).

BEGIN;

ALTER TABLE chamados ADD COLUMN IF NOT EXISTS fts tsvector
  GENERATED ALWAYS AS (
    to_tsvector('portuguese', coalesce(titulo, '') || ' ' || coalesce(descricao, ''))
  ) STORED;

CREATE INDEX IF NOT EXISTS idx_chamados_fts ON chamados USING gin(fts);

COMMENT ON COLUMN chamados.fts IS
  'FTS português (titulo+descricao) para a busca de chamados semelhantes da triagem por IA (plano_md_mestre_IA.md, Seção 4.4). Fase B (pgvector) fica para quando o volume justificar.';

COMMIT;
