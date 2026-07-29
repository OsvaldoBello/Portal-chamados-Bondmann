-- 0063_backfill_perfil_telefone.sql
-- Backfill de `perfis.telefone` (0062) a partir do telefone que a pessoa já
-- informou na abertura de chamado (`chamados.telefone_contato`, 0058): quem já
-- digitou o número alguma vez não é perguntado nem na primeira abertura depois
-- do deploy. Pedido do usuário, 2026-07-29.
--
-- Regras do backfill (deliberadamente conservadoras):
--   - só perfis com `telefone = ''` (não sobrescreve nada — a migration é
--     idempotente e pode ser reaplicada sem efeito);
--   - só o chamado MAIS RECENTE de cada autor (número atual, não histórico);
--   - só números que passariam na validação do servidor (`validar_telefone_
--     contato`: ao menos 8 dígitos ignorando formatação) — lixo digitado numa
--     abertura antiga não vira o telefone do perfil;
--   - `DISTINCT ON` em vez de `MAX`: precisa do valor da linha mais recente,
--     não do maior valor.
--
-- Sem risco para o trigger `enforce_perfil_self_so_avatar` (0062): esta
-- migration roda sem JWT, então `auth.uid()` é NULL e a guarda de "telefone só
-- do dono" não dispara (comparação com NULL não é verdadeira). As colunas
-- congeladas (nome/role/...) não são tocadas.
--
-- Efeito colateral conhecido e aceito: `updated_at` das linhas atingidas sobe
-- (trigger `trigger_set_timestamp`), e ele é o cache-buster da URL do avatar
-- (`perfil.avatar_atualizado_em`) — na prática, essas pessoas rebaixam a foto
-- do Storage uma vez.

BEGIN;

WITH ultimo_telefone AS (
  SELECT DISTINCT ON (c.cliente_id)
         c.cliente_id,
         btrim(c.telefone_contato) AS telefone
    FROM chamados c
   WHERE length(regexp_replace(c.telefone_contato, '\D', '', 'g')) >= 8
   ORDER BY c.cliente_id, c.created_at DESC
)
UPDATE perfis p
   SET telefone = u.telefone
  FROM ultimo_telefone u
 WHERE p.id = u.cliente_id
   AND p.telefone = '';

COMMIT;
