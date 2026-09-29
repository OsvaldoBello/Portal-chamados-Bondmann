-- 0092_automacao_tipo_sincronizar_lideranca.sql
-- Automação de acessos v2, F5 (plano_md_mestre_automacao_acessos.md, Seção 6).
-- Sozinha de propósito: o valor novo de um enum não pode ser USADO na mesma
-- transação em que é criado — a 0093 (CHECK e índice) depende dele.
ALTER TYPE automacao_tipo ADD VALUE IF NOT EXISTS 'SINCRONIZAR_LIDERANCA';
