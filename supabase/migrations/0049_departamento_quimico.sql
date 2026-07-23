-- 0049_departamento_quimico.sql
-- Departamento Químico como DESTINO de chamados (antes só solicitante — 0027)
-- com abertura DINÂMICA por categoria e resumo por IA.
--
-- Contexto: o setor "Dpto Químico" já existe (migration 0027) como setor que só
-- ABRE chamado (recebe_chamados = false). Aqui ele passa a RECEBER chamados,
-- ganhando três categorias com layouts de formulário distintos:
--   1) Registro de Ocorrência
--   2) Solicitação de Visita Técnica
--   3) Solicitação de Análise Laboratorial
-- Os campos de cada layout são definidos em código (app/domain/formularios_quimico.py)
-- e as respostas ficam em `chamados.dados_formulario` (jsonb genérico) — mesma
-- filosofia dos campos ad-hoc do Marketing (0024), porém sem uma coluna por campo.
--
-- A RLS de atendimento é genérica por `departamento_id = auth_departamento_id()`
-- (0008), então basta o staff do Químico ter `perfis.departamento_id` apontando
-- para este setor — nenhuma policy nova é necessária.
--
-- Idempotente (reexecução não duplica).

BEGIN;

-- 1) Químico passa a receber chamados (aparece em "Departamento de destino",
--    ganha fila/kanban de atendimento). O guarda-corpo da 0027
--    (enforce_departamento_recebe_chamados) exige isto antes de qualquer chamado
--    ou staff apontar para ele.
UPDATE departamentos SET recebe_chamados = true WHERE nome = 'Dpto Químico';

-- 2) Categorias do Químico (categorias por departamento — 0019). Sem
--    subcategorias: o layout específico de cada uma vem dos campos dinâmicos.
INSERT INTO categorias (nome, departamento_id)
SELECT v.nome, (SELECT id FROM departamentos WHERE nome = 'Dpto Químico')
FROM (VALUES
  ('Registro de Ocorrência'),
  ('Solicitação de Visita Técnica'),
  ('Solicitação de Análise Laboratorial')
) AS v(nome)
WHERE NOT EXISTS (
  SELECT 1 FROM categorias c
  WHERE c.nome = v.nome
    AND c.departamento_id = (SELECT id FROM departamentos WHERE nome = 'Dpto Químico')
);

-- 3) Respostas dos campos dinâmicos (por categoria) — objeto {name: valor}.
ALTER TABLE chamados
  ADD COLUMN IF NOT EXISTS dados_formulario jsonb NOT NULL DEFAULT '{}'::jsonb;
COMMENT ON COLUMN chamados.dados_formulario IS
  'Respostas dos campos dinâmicos por categoria (ex.: Químico). Objeto {name: valor}; schema em app/domain/formularios_quimico.py.';

-- 4) Nota interna resumida gerada pela IA na abertura (visível só ao staff).
--    Preenchida por uma background task via admin_connection() (app/db.py) —
--    escrita de sistema, fora do request do autor.
ALTER TABLE chamados ADD COLUMN IF NOT EXISTS resumo_ia text;
ALTER TABLE chamados ADD COLUMN IF NOT EXISTS resumo_ia_em timestamptz;
COMMENT ON COLUMN chamados.resumo_ia IS
  'Resumo automático (IA) do chamado para a equipe técnica — nota interna, nunca exibida ao cliente.';

COMMIT;
