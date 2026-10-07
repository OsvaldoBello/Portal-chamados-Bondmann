-- 0094_departamento_manutencao.sql
-- Setor de Manutenção no Portal de Chamados como departamento de destino.
--
-- Contexto:
-- Qualquer colaborador com necessidade de manutenção predial/estrutural/geral
-- agora pode abrir chamados para o setor de Manutenção.
-- Proprietários / Gestores do setor:
--   - elias@bondmann.com.br
--   - manutencao@bondmann.com.br
--
-- O catálogo inicial conta com categorias e subcategorias genéricas cobrindo
-- problemas comuns em instalações corporativas e industriais (goteira, forro quebrado,
-- problemas elétricos, tomadas, hidráulica, ar-condicionado, mobiliário e áreas externas),
-- permitindo validação e refinamento pelo gestor Elias.
--
-- Idempotente (reexecução segura sem duplicar registros).

BEGIN;

-- ============================================================
-- 1. Departamento Manutenção (recebe_chamados = true, autoatendimento = true)
-- ============================================================
INSERT INTO departamentos (nome, recebe_chamados, autoatendimento)
VALUES ('Manutenção', true, true)
ON CONFLICT (nome) DO UPDATE
   SET recebe_chamados = true,
       ativo = true;

-- ============================================================
-- 2. Categorias da Manutenção
-- ============================================================
INSERT INTO categorias (nome, departamento_id, publico_alvo)
SELECT v.nome, (SELECT id FROM departamentos WHERE nome = 'Manutenção'), 'AMBOS'
FROM (VALUES
  ('Predial e Estrutural'),
  ('Elétrica'),
  ('Hidráulica'),
  ('Climatização e Ar-Condicionado'),
  ('Mobiliário e Marcenaria'),
  ('Área Externa e Pátio'),
  ('Outros Reparos')
) AS v(nome)
WHERE NOT EXISTS (
  SELECT 1 FROM categorias c
  WHERE c.nome = v.nome
    AND c.departamento_id = (SELECT id FROM departamentos WHERE nome = 'Manutenção')
);

-- ============================================================
-- 3. Subcategorias por categoria (genéricas para validação do gestor)
-- ============================================================
INSERT INTO subcategorias (categoria_id, nome)
SELECT c.id, v.sub
FROM categorias c
JOIN (VALUES
  -- Predial e Estrutural
  ('Predial e Estrutural', 'Goteira / Infiltração'),
  ('Predial e Estrutural', 'Forro / Teto danificado ou quebrado'),
  ('Predial e Estrutural', 'Telhado / Calhas'),
  ('Predial e Estrutural', 'Piso / Azulejo / Revestimento quebrado'),
  ('Predial e Estrutural', 'Portas / Fechaduras / Janelas / Vidros'),
  ('Predial e Estrutural', 'Paredes / Pintura / Alvenaria / Rachaduras'),

  -- Elétrica
  ('Elétrica', 'Problema na iluminação / Lâmpada queimada / Falha de luz'),
  ('Elétrica', 'Tomada sem energia / Danificada / Frouxa'),
  ('Elétrica', 'Disjuntor desarmando / Queda parcial de energia'),
  ('Elétrica', 'Interruptor com defeito'),
  ('Elétrica', 'Fiação aparente / Canaletas / Extensões'),
  ('Elétrica', 'Quadro elétrico geral / Disjuntores'),

  -- Hidráulica
  ('Hidráulica', 'Vazamento em torneira / encanamento / registro'),
  ('Hidráulica', 'Pia / Ralo / Vaso sanitário entupido'),
  ('Hidráulica', 'Descarga com defeito / vazando água'),
  ('Hidráulica', 'Caixa d''água / Falta de pressão na água'),
  ('Hidráulica', 'Bebedouro / Filtro de água'),

  -- Climatização e Ar-Condicionado
  ('Climatização e Ar-Condicionado', 'Ar-condicionado não gela / Não liga'),
  ('Climatização e Ar-Condicionado', 'Ar-condicionado pingando / Vazamento de água'),
  ('Climatização e Ar-Condicionado', 'Barulho excessivo no ar-condicionado'),
  ('Climatização e Ar-Condicionado', 'Limpeza de filtros / Higienização preventiva'),
  ('Climatização e Ar-Condicionado', 'Ventilador / Exaustor com defeito'),

  -- Mobiliário e Marcenaria
  ('Mobiliário e Marcenaria', 'Cadeira quebrada ou com defeito'),
  ('Mobiliário e Marcenaria', 'Mesa / Gaveteiro travado / Fechadura'),
  ('Mobiliário e Marcenaria', 'Armário / Prateleira / Estante solta ou desnivelada'),
  ('Mobiliário e Marcenaria', 'Fixação de quadros / Suportes / Painéis'),
  ('Mobiliário e Marcenaria', 'Persianas / Cortinas com defeito'),

  -- Área Externa e Pátio
  ('Área Externa e Pátio', 'Portão de acesso / Fechamento eletrônico'),
  ('Área Externa e Pátio', 'Iluminação externa / Pátio / Estacionamento'),
  ('Área Externa e Pátio', 'Calçadas / Pavimentação externa'),
  ('Área Externa e Pátio', 'Ralos e canaletas pluviais externas'),

  -- Outros Reparos
  ('Outros Reparos', 'Pequenos reparos gerais'),
  ('Outros Reparos', 'Apoio para movimentação de mobiliário / peso'),
  ('Outros Reparos', 'Outras solicitações de manutenção')
) AS v(cat, sub)
  ON v.cat = c.nome
 AND c.departamento_id = (SELECT id FROM departamentos WHERE nome = 'Manutenção')
WHERE NOT EXISTS (
  SELECT 1 FROM subcategorias sx
  WHERE sx.categoria_id = c.id
    AND sx.nome = v.sub
);

-- ============================================================
-- 4. Ajuste da guarda de perfis para scripts administrativos
--    Quando a escrita vem de migração / sistema (auth.uid() IS NULL),
--    não deve disparar a trava de autoedição de avatar/telefone.
-- ============================================================
CREATE OR REPLACE FUNCTION enforce_perfil_self_so_avatar()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = public
AS $$
BEGIN
  IF auth.uid() IS NOT NULL AND NOT auth_is_ti() THEN
    IF ROW(NEW.nome, NEW.role, NEW.empresa_id, NEW.departamento_id, NEW.ativo, NEW.created_at)
       IS DISTINCT FROM
       ROW(OLD.nome, OLD.role, OLD.empresa_id, OLD.departamento_id, OLD.ativo, OLD.created_at)
    THEN
      RAISE EXCEPTION 'Você só pode alterar o próprio avatar.';
    END IF;
    IF NEW.telefone IS DISTINCT FROM OLD.telefone AND NEW.id <> auth.uid() THEN
      RAISE EXCEPTION 'Você só pode alterar o próprio telefone.';
    END IF;
  END IF;
  RETURN NEW;
END;
$$;

-- ============================================================
-- 5. Associação dos proprietários (Elias e Manutenção) como ADMINs do setor
-- ============================================================
UPDATE perfis
   SET role = 'ADMIN',
       departamento_id = (SELECT id FROM departamentos WHERE nome = 'Manutenção')
 WHERE id IN (
   SELECT id FROM auth.users
   WHERE lower(email) IN ('elias@bondmann.com.br', 'manutencao@bondmann.com.br')
 );

UPDATE auth.users
   SET raw_app_meta_data = COALESCE(raw_app_meta_data, '{}'::jsonb) || jsonb_build_object('role', 'ADMIN')
 WHERE lower(email) IN ('elias@bondmann.com.br', 'manutencao@bondmann.com.br');

COMMIT;
