-- 0076_rh_categorias_clt_pj.sql
-- RH — novo catálogo de categorias segmentado por público (decisão de produto
-- 2026-08-05): parte das categorias do RH só faz sentido para colaboradores
-- CLT, parte só para representantes PJ, parte para os dois. "PJ" = perfil cujo
-- departamento é 'Representantes' (0039 — setor só-solicitante, sem fila); todo
-- o resto (inclusive quem não tem departamento) é "CLT" para este filtro.
--
-- O catálogo antigo do RH (0021: Folha de Pagamento, Benefícios, Férias, Ponto
-- e Jornada, Afastamentos e Licenças, Admissão e Desligamento, Documentos e
-- Declarações, Treinamento e Desenvolvimento, Recrutamento e Seleção, Dúvidas
-- Gerais) é substituído por este — desativado (não apagado: chamados antigos
-- continuam referenciando as categorias antigas normalmente).

BEGIN;

-- ============================================================
-- 1. categorias.publico_alvo — quem pode ver/abrir com essa categoria.
--    Default 'AMBOS' preserva o comportamento atual de todo o catálogo
--    pré-existente (TI, Marketing, etc.) — só o RH usa CLT/PJ por enquanto.
-- ============================================================
ALTER TABLE categorias
  ADD COLUMN IF NOT EXISTS publico_alvo text NOT NULL DEFAULT 'AMBOS'
    CHECK (publico_alvo IN ('CLT', 'PJ', 'AMBOS'));

-- ============================================================
-- 2. Desativa o catálogo antigo do RH (migration 0021) — substituído abaixo.
-- ============================================================
UPDATE categorias
   SET ativo = false
 WHERE departamento_id = (SELECT id FROM departamentos WHERE nome = 'RH')
   AND nome IN (
     'Folha de Pagamento', 'Benefícios', 'Férias', 'Ponto e Jornada',
     'Afastamentos e Licenças', 'Admissão e Desligamento',
     'Documentos e Declarações', 'Treinamento e Desenvolvimento',
     'Recrutamento e Seleção', 'Dúvidas Gerais'
   );

-- ============================================================
-- 3. Novas categorias do RH, com público-alvo.
-- ============================================================
INSERT INTO categorias (nome, departamento_id, publico_alvo)
SELECT v.nome, (SELECT id FROM departamentos WHERE nome = 'RH'), v.publico
FROM (VALUES
  ('Documentos', 'CLT'),
  ('Folha de Pagamento', 'CLT'),
  ('Alteração de Jornada', 'CLT'),
  ('Férias', 'CLT'),
  ('Benefícios', 'CLT'),
  ('Movimentação de Colaboradores', 'CLT'),
  ('Alterações Cadastrais', 'AMBOS'),
  ('Ajuda de Custo', 'AMBOS'),
  ('Treinamentos e Desenvolvimento', 'AMBOS'),
  ('Dúvidas Gerais', 'AMBOS'),
  ('Descritivos de Descontos', 'PJ')
) AS v(nome, publico)
WHERE NOT EXISTS (
  SELECT 1 FROM categorias c
  WHERE c.nome = v.nome
    AND c.departamento_id = (SELECT id FROM departamentos WHERE nome = 'RH')
    AND c.ativo = true
);

-- ============================================================
-- 4. Subcategorias das novas categorias do RH.
-- ============================================================
INSERT INTO subcategorias (categoria_id, nome)
SELECT c.id, v.sub
FROM categorias c
JOIN (VALUES
  ('Documentos', 'Solicitação de declaração/atestados'),
  ('Documentos', 'Cópia de recibos de pagamento'),
  ('Documentos', 'Segunda via de documentos'),
  ('Documentos', 'Outros'),

  ('Folha de Pagamento', 'Dúvidas sobre salário e descontos'),
  ('Folha de Pagamento', 'Adiantamento salarial'),
  ('Folha de Pagamento', 'Outros'),

  ('Alteração de Jornada', 'Horas extras'),
  ('Alteração de Jornada', 'Horas faltas'),
  ('Alteração de Jornada', 'Banco de horas'),
  ('Alteração de Jornada', 'Solicitação de folga para compensar banco de horas'),
  ('Alteração de Jornada', 'Solicitação excepcional de Home Office'),
  ('Alteração de Jornada', 'Atestados'),
  ('Alteração de Jornada', 'Licenças'),
  ('Alteração de Jornada', 'Afastamentos'),
  ('Alteração de Jornada', 'Outros'),

  ('Férias', 'Solicitação de período de férias'),
  ('Férias', 'Dúvidas sobre período de férias'),
  ('Férias', 'Alteração de período de férias'),
  ('Férias', 'Outros'),

  ('Benefícios', 'Programa de Incentivo à Educação'),
  ('Benefícios', 'Plano de Saúde'),
  ('Benefícios', 'Vale-Transporte/Combustível'),
  ('Benefícios', 'Vale-Alimentação/Refeição'),
  ('Benefícios', 'Convênio Farmácia'),
  ('Benefícios', 'Wellhub'),
  ('Benefícios', '2ª via de cartão'),
  ('Benefícios', 'Alteração de benefícios'),
  ('Benefícios', 'Outros'),

  ('Movimentação de Colaboradores', 'Aumento de Quadro'),
  ('Movimentação de Colaboradores', 'Alteração de Função'),
  ('Movimentação de Colaboradores', 'Encerramento de Contrato'),
  ('Movimentação de Colaboradores', 'Troca de Setor'),
  ('Movimentação de Colaboradores', 'Adendo Contratual de Representação (Regiões)'),
  ('Movimentação de Colaboradores', 'Assuntos relacionados ao Recrutamento e Seleção'),

  ('Alterações Cadastrais', 'Alteração de endereço, telefone, estado civil, nascimento de filhos, alteração de nome, outros'),
  ('Alterações Cadastrais', 'Alteração de dados bancários'),
  ('Alterações Cadastrais', 'Outros'),

  ('Ajuda de Custo', 'Solicitação de ajuda de custo'),
  ('Ajuda de Custo', 'Dúvidas sobre o auxílio'),

  ('Treinamentos e Desenvolvimento', 'Registros de Treinamentos'),
  ('Treinamentos e Desenvolvimento', 'Certificados'),

  ('Dúvidas Gerais', 'Qualquer assunto que não se encaixe nas demais categorias'),

  ('Descritivos de Descontos', 'Assuntos relacionados a brindes, diluidores e afins')
) AS v(cat, sub)
  ON v.cat = c.nome
 AND c.departamento_id = (SELECT id FROM departamentos WHERE nome = 'RH')
 AND c.ativo = true
WHERE NOT EXISTS (
  SELECT 1 FROM subcategorias sx WHERE sx.categoria_id = c.id AND sx.nome = v.sub
);

COMMIT;
