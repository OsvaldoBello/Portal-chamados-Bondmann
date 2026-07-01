-- 0011_realtime_mensagens.sql
-- Habilita o Supabase Realtime na tabela `mensagens` para o chat do chamado
-- (Seção 6.1): o browser assina `postgres_changes` (INSERT) filtrando por
-- `chamado_id` via supabase-js, com a RLS de `mensagens` aplicada na entrega
-- (nota interna NÃO chega ao funcionário autor). A escrita continua pelo
-- FastAPI (POST); o Realtime apenas entrega o evento de nova linha.

-- Idempotente: só adiciona se ainda não estiver na publicação.
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_publication_tables
    WHERE pubname = 'supabase_realtime'
      AND schemaname = 'public'
      AND tablename = 'mensagens'
  ) THEN
    ALTER PUBLICATION supabase_realtime ADD TABLE public.mensagens;
  END IF;
END $$;
