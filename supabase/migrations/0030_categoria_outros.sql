-- 0030_categoria_outros.sql
-- Categoria "Outros" (+ subcategoria "Outros") em todo departamento que RECEBE
-- chamado (tem fila — TI/RH/Marketing), para pedidos que não se encaixam em
-- nenhuma categoria específica do catálogo. Ao escolher "Outros" no formulário
-- de abertura, a subcategoria é pré-selecionada automaticamente no client
-- (novo_chamado.js) — funciona porque essa categoria tem SÓ essa subcategoria.
-- Idempotente: roda de novo sem duplicar se algum setor já tiver "Outros".

BEGIN;

INSERT INTO categorias (nome, departamento_id)
SELECT 'Outros', d.id
  FROM departamentos d
 WHERE d.recebe_chamados = true
   AND NOT EXISTS (
     SELECT 1 FROM categorias c WHERE c.nome = 'Outros' AND c.departamento_id = d.id
   );

INSERT INTO subcategorias (categoria_id, nome)
SELECT c.id, 'Outros'
  FROM categorias c
  JOIN departamentos d ON d.id = c.departamento_id
 WHERE c.nome = 'Outros'
   AND d.recebe_chamados = true
   AND NOT EXISTS (
     SELECT 1 FROM subcategorias s WHERE s.categoria_id = c.id AND s.nome = 'Outros'
   );

COMMIT;
