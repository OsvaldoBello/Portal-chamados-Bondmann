-- 0023_add_setor_to_chamados.sql
-- Adiciona o campo "setor" na tabela chamados para indicar o setor solicitante/demandante.

BEGIN;

ALTER TABLE chamados ADD COLUMN IF NOT EXISTS setor text;

COMMIT;
