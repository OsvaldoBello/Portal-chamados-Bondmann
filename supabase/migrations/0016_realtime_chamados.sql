-- 0016_realtime_chamados — Realtime do sino de notificações (Fase 4)
--
-- O sino em tempo real (notificacoes.js) assina `postgres_changes` de `chamados`
-- e `mensagens` para acender/tocar quando há mudança significativa (novo chamado,
-- troca de status/prioridade, atribuição, nova mensagem). `mensagens` já estava
-- na publicação (0011); aqui adicionamos `chamados`.
--
-- A entrega respeita a RLS de SELECT de cada tabela: o funcionário só recebe
-- eventos dos próprios chamados; o staff, os do seu setor; o TI, todos. Nenhum
-- dado sensível vaza — o cliente apenas recebe o "ping" e recarrega o fragmento
-- renderizado no servidor (também sob RLS).
--
-- Idempotente: só adiciona se ainda não estiver na publicação.

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_publication_tables
     WHERE pubname = 'supabase_realtime'
       AND schemaname = 'public'
       AND tablename = 'chamados'
  ) THEN
    ALTER PUBLICATION supabase_realtime ADD TABLE public.chamados;
  END IF;
END $$;
