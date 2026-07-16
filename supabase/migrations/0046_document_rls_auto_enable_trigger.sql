-- 0046_document_rls_auto_enable_trigger.sql — fecha a pendência do item 0.1
-- (Sprint 0 / plano_melhorias_auditoria.md)
--
-- Achado durante a reconstrução da 0015 (ver notas de execução do item 0.1):
-- em produção (`iurlzlhbnoemkzgexcfk`) existe uma função `rls_auto_enable()` +
-- um event trigger `ensure_rls` (ddl_command_end em CREATE TABLE / CREATE
-- TABLE AS / SELECT INTO) que habilita RLS automaticamente em qualquer tabela
-- nova criada no schema `public`. Nenhuma migration deste repositório jamais
-- criou esse mecanismo — mesmo tipo de drift (alterado direto no SQL Editor
-- do dashboard) que deixou a 0015 sem arquivo e a 0045 sem registro do
-- `ENABLE ROW LEVEL SECURITY` em `marketing_midia_regional`.
--
-- Introspecção via MCP do Supabase confirmou o mecanismo exato:
--   - Função: public.rls_auto_enable() — event trigger function, SECURITY
--     DEFINER, owner `postgres`, roda `ALTER TABLE ... ENABLE ROW LEVEL
--     SECURITY` para cada tabela nova em `public` (ignora falhas
--     individualmente, só loga).
--   - Event trigger: `ensure_rls`, ON ddl_command_end, tags
--     ('CREATE TABLE', 'CREATE TABLE AS', 'SELECT INTO'), enabled = 'O'
--     (dispara em sessões normais).
--
-- Esta migration não muda comportamento de produção (o mecanismo já existe
-- lá) — só o formaliza no histórico de migrations, para que uma base aplicada
-- do zero (0001→0046) reproduza a mesma rede de segurança: RLS habilitada por
-- padrão em toda tabela nova de `public`, mesmo que uma migration futura
-- esqueça o `ENABLE ROW LEVEL SECURITY` explícito.
--
-- Idempotente: CREATE OR REPLACE FUNCTION substitui sem erro; o event trigger
-- só é criado se ainda não existir (CREATE EVENT TRIGGER não aceita
-- IF NOT EXISTS).

CREATE OR REPLACE FUNCTION public.rls_auto_enable()
 RETURNS event_trigger
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
DECLARE
  cmd record;
BEGIN
  FOR cmd IN
    SELECT *
    FROM pg_event_trigger_ddl_commands()
    WHERE command_tag IN ('CREATE TABLE', 'CREATE TABLE AS', 'SELECT INTO')
      AND object_type IN ('table','partitioned table')
  LOOP
     IF cmd.schema_name IS NOT NULL AND cmd.schema_name IN ('public') AND cmd.schema_name NOT IN ('pg_catalog','information_schema') AND cmd.schema_name NOT LIKE 'pg_toast%' AND cmd.schema_name NOT LIKE 'pg_temp%' THEN
      BEGIN
        EXECUTE format('alter table if exists %s enable row level security', cmd.object_identity);
        RAISE LOG 'rls_auto_enable: enabled RLS on %', cmd.object_identity;
      EXCEPTION
        WHEN OTHERS THEN
          RAISE LOG 'rls_auto_enable: failed to enable RLS on %', cmd.object_identity;
      END;
     ELSE
        RAISE LOG 'rls_auto_enable: skip % (either system schema or not in enforced list: %.)', cmd.object_identity, cmd.schema_name;
     END IF;
  END LOOP;
END;
$function$;

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_event_trigger WHERE evtname = 'ensure_rls') THEN
    CREATE EVENT TRIGGER ensure_rls ON ddl_command_end
      WHEN TAG IN ('CREATE TABLE', 'CREATE TABLE AS', 'SELECT INTO')
      EXECUTE FUNCTION public.rls_auto_enable();
  END IF;
END;
$$;
