-- 0078_rh_jornada_de_trabalho.sql
-- RH — categoria "Alteração de Jornada" (0076) renomeada para "Jornada de
-- Trabalho" e ganha a subcategoria "Alteração de Jornada" (antes só existia
-- como nome da própria categoria; agora vira um item dentro dela, ao lado de
-- Horas extras/Horas faltas/Banco de horas/etc).
--
-- Idempotente (reexecução não duplica nem falha).

BEGIN;

-- ============================================================
-- 1. Renomeia a categoria.
-- ============================================================
UPDATE categorias
   SET nome = 'Jornada de Trabalho'
 WHERE nome = 'Alteração de Jornada'
   AND departamento_id = (SELECT id FROM departamentos WHERE nome = 'RH')
   AND ativo = true;

-- ============================================================
-- 2. Nova subcategoria "Alteração de Jornada" dentro de "Jornada de Trabalho".
-- ============================================================
INSERT INTO subcategorias (categoria_id, nome)
SELECT c.id, 'Alteração de Jornada'
FROM categorias c
WHERE c.nome = 'Jornada de Trabalho'
  AND c.departamento_id = (SELECT id FROM departamentos WHERE nome = 'RH')
  AND c.ativo = true
  AND NOT EXISTS (
    SELECT 1 FROM subcategorias sx
    WHERE sx.categoria_id = c.id AND sx.nome = 'Alteração de Jornada'
  );

COMMIT;
