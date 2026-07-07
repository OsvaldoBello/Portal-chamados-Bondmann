-- 0024_marketing_kanban_fields.sql
-- Adiciona suporte a status A_FAZER e colunas volume e origem_demanda para o Marketing.

ALTER TYPE status_chamado ADD VALUE IF NOT EXISTS 'A_FAZER';

ALTER TABLE chamados ADD COLUMN IF NOT EXISTS volume integer;
ALTER TABLE chamados ADD COLUMN IF NOT EXISTS origem_demanda text;
ALTER TABLE chamados ADD COLUMN IF NOT EXISTS causa_atraso text;

CREATE TABLE IF NOT EXISTS marketing_midia_regional (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    mes text NOT NULL UNIQUE,
    investimento numeric(12, 2) NOT NULL DEFAULT 0.00,
    regioes integer NOT NULL DEFAULT 0,
    descontinuidades integer NOT NULL DEFAULT 0,
    aderencias integer NOT NULL DEFAULT 0,
    created_at timestamptz NOT NULL DEFAULT now()
);

INSERT INTO marketing_midia_regional (mes, investimento, regioes, descontinuidades, aderencias)
VALUES 
    ('JAN/26', 4605.02, 51, 7, 2),
    ('FEV/26', 4057.02, 44, 5, 0),
    ('MAR/26', 3756.27, 40, 2, 1),
    ('ABR/26', 4132.86, 45, 1, 7),
    ('MAI/26', 3958.90, 48, 6, 4)
ON CONFLICT (mes) DO UPDATE
SET investimento = EXCLUDED.investimento,
    regioes = EXCLUDED.regioes,
    descontinuidades = EXCLUDED.descontinuidades,
    aderencias = EXCLUDED.aderencias;
