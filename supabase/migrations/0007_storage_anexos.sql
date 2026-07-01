-- 0007_storage_anexos.sql
-- Fase 3 / Seção 3.9 — bucket privado de anexos + RLS path-scoped por tenant.
-- Convenção de path: {empresa_id}/{chamado_id}/{arquivo}.
-- CLIENTE só acessa objetos sob o prefixo da própria empresa; OPERADOR/ADMIN todos.
-- Reusa os helpers auth_role()/auth_empresa_id() (migration 0004).

-- Bucket PRIVADO (public=false — exigência da spec, risco Crítico).
INSERT INTO storage.buckets (id, name, public)
VALUES ('chamados-anexos', 'chamados-anexos', false)
ON CONFLICT (id) DO NOTHING;

-- O primeiro segmento do path é o empresa_id: (storage.foldername(name))[1].
CREATE POLICY anexos_select ON storage.objects
  FOR SELECT USING (
    bucket_id = 'chamados-anexos'
    AND (
      auth_role() IN ('OPERADOR', 'ADMIN')
      OR (storage.foldername(name))[1] = auth_empresa_id()::text
    )
  );

CREATE POLICY anexos_insert ON storage.objects
  FOR INSERT WITH CHECK (
    bucket_id = 'chamados-anexos'
    AND (
      auth_role() IN ('OPERADOR', 'ADMIN')
      OR (storage.foldername(name))[1] = auth_empresa_id()::text
    )
  );

-- Exclusão restrita a OPERADOR/ADMIN (CLIENTE não remove anexos).
CREATE POLICY anexos_delete_staff ON storage.objects
  FOR DELETE USING (
    bucket_id = 'chamados-anexos'
    AND auth_role() IN ('OPERADOR', 'ADMIN')
  );
