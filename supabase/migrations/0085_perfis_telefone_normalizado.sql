-- 0085_perfis_telefone_normalizado.sql
-- Suporte à identificação de usuário por telefone na abertura de chamado via
-- WhatsApp (plano de intake WhatsApp, 2026-08-18).
--
-- O problema: `perfis.telefone` (0062) é texto livre digitado pelo usuário e,
-- em produção, está quase todo em formato NACIONAL de 11 dígitos
-- (DDD + 9 + 8 dígitos, ex.: "51994105691"), enquanto o webhook da Meta manda
-- o remetente em formato internacional — e, para o Brasil, a Meta devolve o
-- `wa_id` CANÔNICO, que inclui o "55" e **não** inclui o nono dígito
-- (ex.: "555194105691"). Verificado ao vivo: enviar para "5551994105691"
-- retorna `wa_id: "555194105691"`.
--
-- Comparar os dígitos crus casaria ZERO usuários. Esta função reduz os dois
-- formatos à mesma forma canônica: **DDD + os 8 dígitos finais**.
--
--   "51994105691"       -> "5194105691"   (perfil, nacional com 9)
--   "555194105691"      -> "5194105691"   (wa_id da Meta, sem o 9)
--   "5551994105691"     -> "5194105691"   (internacional com 9)
--   "(51) 99410-5691"   -> "5194105691"   (formatado)
--   "51 3333-4444"      -> "5133334444"   (fixo, sem nono dígito)
--
-- Descartar o nono dígito pode, em teoria, colidir um celular com um fixo de
-- mesmos 8 dígitos finais no mesmo DDD. Isso é tolerável porque a resolução
-- (`app/ia/whatsapp_intake.py::resolver_perfil_por_telefone`) trata 2+ perfis
-- como NÃO identificado — colisão degrada para "não abre chamado", nunca para
-- "abre em nome da pessoa errada".
--
-- SEM `UNIQUE` de propósito: a verificação em produção (2026-08-18) achou 2
-- grupos de telefone repetido (5 perfis) — provavelmente placeholders. Uma
-- migration com `UNIQUE` falharia na aplicação. Esses perfis simplesmente não
-- usam o intake até a duplicidade ser resolvida.

BEGIN;

CREATE OR REPLACE FUNCTION normalizar_telefone_br(raw text)
RETURNS text
LANGUAGE sql
IMMUTABLE
AS $$
  WITH digitos AS (
    SELECT nullif(regexp_replace(coalesce(raw, ''), '[^0-9]', '', 'g'), '') AS d
  ),
  sem_pais AS (
    -- Tira o código do país só quando o número é longo o bastante para tê-lo
    -- (12+ dígitos): um fixo nacional de 10 dígitos iniciado em "55" — DDD 55,
    -- Mato Grosso do Sul — não pode perder os dois primeiros.
    SELECT CASE
             WHEN length(d) >= 12 AND left(d, 2) = '55' THEN substr(d, 3)
             ELSE d
           END AS d
      FROM digitos
  )
  SELECT CASE
           WHEN d IS NULL THEN NULL
           -- Celular nacional com nono dígito: DDD + 9 + 8 -> descarta o 9.
           WHEN length(d) = 11 AND substr(d, 3, 1) = '9' THEN left(d, 2) || right(d, 8)
           ELSE d
         END
    FROM sem_pais;
$$;

COMMENT ON FUNCTION normalizar_telefone_br(text) IS
  'Forma canônica de telefone brasileiro (DDD + 8 dígitos finais), tolerante a código do país e ao nono dígito. Fonte única de normalização — usada pela coluna gerada perfis.telefone_normalizado e por app/ia/whatsapp_intake.py::resolver_perfil_por_telefone.';

ALTER TABLE perfis
  ADD COLUMN IF NOT EXISTS telefone_normalizado text
  GENERATED ALWAYS AS (normalizar_telefone_br(telefone)) STORED;

CREATE INDEX IF NOT EXISTS idx_perfis_telefone_normalizado
  ON perfis (telefone_normalizado)
  WHERE telefone_normalizado IS NOT NULL;

COMMENT ON COLUMN perfis.telefone_normalizado IS
  'Forma canônica de perfis.telefone (gerada). Casa o número que manda mensagem no WhatsApp com um perfil cadastrado — sem garantia de unicidade, ver nota da migration.';

COMMIT;
