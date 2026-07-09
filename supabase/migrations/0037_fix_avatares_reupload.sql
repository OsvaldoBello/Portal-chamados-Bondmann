-- 0037_fix_avatares_reupload.sql
-- Causa raiz do "não consigo trocar a foto de perfil" (upload falha só na 2ª
-- vez em diante, a 1ª sempre funcionava): a `0035` removeu `avatares_select`
-- (achando que um bucket público não precisa de policy de SELECT, já que a
-- leitura pública passa por `/storage/v1/object/public/...` e não usa RLS).
-- Isso é verdade para LEITURA pública, mas o path do avatar é FIXO por usuário
-- (`{user_id}/avatar.png`, upload com `x-upsert: true` — Fase 7) para que
-- reenviar SUBSTITUA a foto anterior em vez de acumular arquivo. Reenviar um
-- objeto que já existe roda como UPDATE por baixo do Storage, e o Postgres só
-- permite UPDATE/DELETE em linhas que o role também conseguiria SELECT — sem
-- NENHUMA policy de SELECT, `authenticated` não enxerga nenhuma linha do
-- bucket (nem a própria), então o UPDATE silenciosamente afeta 0 linhas e o
-- Storage responde erro. Confirmado ao vivo (simulação de claims, projeto
-- iurlzlhbnoemkzgexcfk): sem a policy, `UPDATE ... WHERE bucket_id='avatares'
-- AND name='<uid>/avatar.png'` para o próprio dono retorna 0 linhas; com ela,
-- retorna a linha normalmente.
--
-- Fix: policy de SELECT restrita ao PRÓPRIO path (não a listagem geral de
-- todos os avatares que a `0035` removeu com razão) — o suficiente pro
-- Postgres localizar a própria linha ao atualizar, sem reabrir a listagem
-- ampla que o advisor apontou.

BEGIN;

CREATE POLICY avatares_select_own ON storage.objects
  FOR SELECT
  USING (bucket_id = 'avatares' AND (storage.foldername(name))[1] = auth.uid()::text);

COMMIT;
