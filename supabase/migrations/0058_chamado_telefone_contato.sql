-- 0058_chamado_telefone_contato.sql
-- Telefone de contato obrigatório na abertura do chamado (pedido do usuário,
-- 2026-07-24): quem abre precisa informar um número (celular/ramal) para o
-- setor responsável conseguir falar diretamente, quando o chat não é ágil o
-- bastante. `NOT NULL DEFAULT ''` só para não quebrar o backfill das linhas
-- já existentes (sem telefone histórico) — a validação de "obrigatório e com
-- dígitos suficientes" fica no servidor (`app/repositories/chamados.py::
-- validar_telefone_contato`), a coluna em si não tem CHECK de não-vazio pelo
-- mesmo motivo. `DROP DEFAULT` depois do backfill: toda abertura NOVA passa a
-- exigir o valor explícito no INSERT (`AtendimentoRepo.criar`), sem default
-- silencioso mascarando um esquecimento futuro.
ALTER TABLE chamados
  ADD COLUMN telefone_contato text NOT NULL DEFAULT '';

ALTER TABLE chamados
  ALTER COLUMN telefone_contato DROP DEFAULT;
