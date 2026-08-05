-- 0077_quimico_solicitacao_desenvolvimento.sql
-- Nova categoria do Dpto Químico: "Solicitação de Desenvolvimento".
--
-- Pedido de novo produto avaliado pela gestão de P&D, migrado do formulário
-- Word do setor (anexado pelo usuário em 2026-08-05). Segue o mesmo padrão da
-- migration 0049: a categoria só existe aqui como registro em `categorias`; o
-- layout de campos (Objetivo do Desenvolvimento, Justificativa, Mercado-alvo,
-- Concorrência, Principais diferenciais) fica em código, em
-- app/domain/formularios_quimico.py (CAT_DESENVOLVIMENTO).
--
-- Idempotente (reexecução não duplica).

BEGIN;

INSERT INTO categorias (nome, departamento_id)
SELECT v.nome, (SELECT id FROM departamentos WHERE nome = 'Dpto Químico')
FROM (VALUES
  ('Solicitação de Desenvolvimento')
) AS v(nome)
WHERE NOT EXISTS (
  SELECT 1 FROM categorias c
  WHERE c.nome = v.nome
    AND c.departamento_id = (SELECT id FROM departamentos WHERE nome = 'Dpto Químico')
);

COMMIT;
