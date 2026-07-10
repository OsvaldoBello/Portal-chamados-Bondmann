-- 0039_novos_setores.sql
-- Novos setores solicitantes (só abrem chamado, não recebem — mesmo padrão da 0027).

BEGIN;

INSERT INTO departamentos (nome, recebe_chamados) VALUES
  ('Produção', false),
  ('CIPA', false),
  ('Compras', false),
  ('Representantes', false),
  ('Gerentes de vendas', false),
  ('Supervisão de Vendas', false)
ON CONFLICT (nome) DO NOTHING;

COMMIT;
