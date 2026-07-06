-- 0021_categorias_rh.sql
-- Catálogo de categorias/subcategorias do departamento RH (categorias por
-- departamento — migration 0019). Conjunto inicial coerente com um help desk
-- interno de Recursos Humanos. Idempotente (não duplica se reexecutar).

BEGIN;

-- 1. Categorias do RH.
INSERT INTO categorias (nome, departamento_id)
SELECT v.nome, (SELECT id FROM departamentos WHERE nome = 'RH')
FROM (VALUES
  ('Folha de Pagamento'),
  ('Benefícios'),
  ('Férias'),
  ('Ponto e Jornada'),
  ('Afastamentos e Licenças'),
  ('Admissão e Desligamento'),
  ('Documentos e Declarações'),
  ('Treinamento e Desenvolvimento'),
  ('Recrutamento e Seleção'),
  ('Dúvidas Gerais')
) AS v(nome)
WHERE NOT EXISTS (
  SELECT 1 FROM categorias c
  WHERE c.nome = v.nome
    AND c.departamento_id = (SELECT id FROM departamentos WHERE nome = 'RH')
);

-- 2. Subcategorias do RH (vinculadas pela categoria-mãe do próprio setor).
INSERT INTO subcategorias (categoria_id, nome)
SELECT c.id, v.sub
FROM categorias c
JOIN (VALUES
  ('Folha de Pagamento', 'Dúvida sobre holerite'),
  ('Folha de Pagamento', 'Erro no pagamento'),
  ('Folha de Pagamento', 'Desconto indevido'),
  ('Folha de Pagamento', 'Adiantamento salarial'),
  ('Benefícios', 'Vale-transporte'),
  ('Benefícios', 'Vale-refeição / alimentação'),
  ('Benefícios', 'Plano de saúde'),
  ('Benefícios', 'Plano odontológico'),
  ('Férias', 'Solicitação de férias'),
  ('Férias', 'Abono pecuniário'),
  ('Férias', 'Dúvida sobre saldo de férias'),
  ('Ponto e Jornada', 'Ajuste de ponto'),
  ('Ponto e Jornada', 'Banco de horas'),
  ('Ponto e Jornada', 'Justificativa de falta'),
  ('Ponto e Jornada', 'Hora extra'),
  ('Afastamentos e Licenças', 'Licença médica (atestado)'),
  ('Afastamentos e Licenças', 'Licença maternidade/paternidade'),
  ('Afastamentos e Licenças', 'Afastamento INSS'),
  ('Admissão e Desligamento', 'Documentação admissional'),
  ('Admissão e Desligamento', 'Rescisão / desligamento'),
  ('Admissão e Desligamento', 'Aviso prévio'),
  ('Admissão e Desligamento', 'Exame demissional'),
  ('Documentos e Declarações', 'Declaração de vínculo empregatício'),
  ('Documentos e Declarações', 'Informe de rendimentos (IRPF)'),
  ('Documentos e Declarações', 'Atualização cadastral'),
  ('Treinamento e Desenvolvimento', 'Solicitação de treinamento'),
  ('Treinamento e Desenvolvimento', 'Emissão de certificado'),
  ('Recrutamento e Seleção', 'Abertura de vaga'),
  ('Recrutamento e Seleção', 'Indicação de candidato'),
  ('Dúvidas Gerais', 'Dúvida geral de RH'),
  ('Dúvidas Gerais', 'Outros assuntos')
) AS v(cat, sub)
  ON v.cat = c.nome
 AND c.departamento_id = (SELECT id FROM departamentos WHERE nome = 'RH')
WHERE NOT EXISTS (
  SELECT 1 FROM subcategorias sx WHERE sx.categoria_id = c.id AND sx.nome = v.sub
);

COMMIT;
