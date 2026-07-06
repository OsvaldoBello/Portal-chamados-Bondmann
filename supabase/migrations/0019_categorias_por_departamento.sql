-- 0019_categorias_por_departamento.sql
-- Categorias passam a pertencer a um DEPARTAMENTO (decisão de produto 2026-07-06):
-- ao abrir chamado, o solicitante vê só as categorias do departamento escolhido.
--   * As categorias já existentes (seed 0018, extraídas do relatório do TI) → TI.
--   * Novas categorias/subcategorias do MARKETING inseridas aqui.
-- (RH ainda não tem catálogo próprio — definir depois pela Gestão de catálogos.)

BEGIN;

-- 1. Coluna departamento_id no catálogo de categorias (idempotente).
ALTER TABLE categorias
  ADD COLUMN IF NOT EXISTS departamento_id uuid REFERENCES departamentos(id) ON DELETE CASCADE;
CREATE INDEX IF NOT EXISTS idx_categorias_departamento ON categorias(departamento_id);

-- 2. Categorias sem dono (o seed do relatório) passam a ser do departamento TI.
UPDATE categorias
   SET departamento_id = (SELECT id FROM departamentos WHERE nome = 'TI')
 WHERE departamento_id IS NULL;

-- 3. Categorias do Marketing (idempotente: não duplica se a migration reexecutar).
INSERT INTO categorias (nome, departamento_id)
SELECT v.nome, (SELECT id FROM departamentos WHERE nome = 'Marketing')
FROM (VALUES
  ('Mailing'),
  ('Card'),
  ('Site'),
  ('Identidade Visual'),
  ('UBD'),
  ('Material impresso'),
  ('Apresentação'),
  ('Foto'),
  ('Vídeo'),
  ('Material digital/PDF')
) AS v(nome)
WHERE NOT EXISTS (
  SELECT 1 FROM categorias c
  WHERE c.nome = v.nome
    AND c.departamento_id = (SELECT id FROM departamentos WHERE nome = 'Marketing')
);

-- 4. Subcategorias do Marketing (vinculadas pela categoria-mãe do próprio setor).
INSERT INTO subcategorias (categoria_id, nome)
SELECT c.id, v.sub
FROM categorias c
JOIN (VALUES
  ('Site', 'Editar o site'),
  ('Site', 'Adicionar no site'),
  ('Site', 'Remover do site'),
  ('Foto', 'Editar foto'),
  ('Foto', 'Captura de foto'),
  ('Vídeo', 'Edição de vídeo'),
  ('Vídeo', 'Gravação de vídeo'),
  ('Mailing', 'Criar campanha de e-mail'),
  ('Mailing', 'Disparar campanha de e-mail'),
  ('Card', 'Criar card'),
  ('Card', 'Editar card'),
  ('UBD', 'Editar vídeo para UBD'),
  ('UBD', 'Publicar vídeo para UBD'),
  ('UBD', 'Criar capa para vídeo da UBD'),
  ('UBD', 'Remover vídeo da UBD'),
  ('Material impresso', 'Criar material para impressão'),
  ('Material impresso', 'Editar material para impressão'),
  ('Apresentação', 'Criar apresentação'),
  ('Apresentação', 'Editar apresentação'),
  ('Apresentação', 'Criar template de apresentação'),
  ('Material digital/PDF', 'Criar material'),
  ('Material digital/PDF', 'Editar material')
) AS v(cat, sub)
  ON v.cat = c.nome
 AND c.departamento_id = (SELECT id FROM departamentos WHERE nome = 'Marketing')
WHERE NOT EXISTS (
  SELECT 1 FROM subcategorias sx WHERE sx.categoria_id = c.id AND sx.nome = v.sub
);

COMMIT;
