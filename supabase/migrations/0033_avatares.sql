-- 0033_avatares.sql
-- Foto de perfil (Fase 7 — 2026-07-09): coluna de avatar em `perfis` + bucket
-- PÚBLICO de Storage (diferente do bucket privado de anexos, 0007 — avatar não
-- é dado sensível e é renderizado em muitas células de card ao mesmo tempo nas
-- telas de fila/kanban, então uma URL pública estável evita assinar URL por
-- render). Upload continua exigindo o JWT do usuário: a RLS de
-- `storage.objects` só deixa cada um escrever no PRÓPRIO path
-- (`{user_id}/avatar.<ext>`).

BEGIN;

ALTER TABLE perfis ADD COLUMN IF NOT EXISTS avatar_path text;

-- Antes desta migration, `perfis` só tinha `perfis_admin_all` (FOR ALL, só TI —
-- 0012) e `perfis_select` — ninguém conseguia atualizar o PRÓPRIO perfil. Nova
-- policy de self-update + trigger que só deixa mudar `avatar_path` (mesmo
-- padrão de `enforce_cliente_so_avaliacao`, migration 0006): TI continua sem
-- essa restrição (já tem `perfis_admin_all` para tudo).
CREATE POLICY perfis_update_self ON perfis
  FOR UPDATE TO authenticated
  USING (id = auth.uid())
  WITH CHECK (id = auth.uid());

CREATE OR REPLACE FUNCTION enforce_perfil_self_so_avatar()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = public
AS $$
BEGIN
  IF NOT auth_is_ti() THEN
    IF ROW(NEW.nome, NEW.role, NEW.empresa_id, NEW.departamento_id, NEW.ativo, NEW.created_at)
       IS DISTINCT FROM
       ROW(OLD.nome, OLD.role, OLD.empresa_id, OLD.departamento_id, OLD.ativo, OLD.created_at)
    THEN
      RAISE EXCEPTION 'Você só pode alterar o próprio avatar.';
    END IF;
  END IF;
  RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS perfis_self_so_avatar ON perfis;
CREATE TRIGGER perfis_self_so_avatar
  BEFORE UPDATE ON perfis
  FOR EACH ROW EXECUTE FUNCTION enforce_perfil_self_so_avatar();

REVOKE ALL ON FUNCTION enforce_perfil_self_so_avatar() FROM PUBLIC, anon, authenticated;

INSERT INTO storage.buckets (id, name, public)
VALUES ('avatares', 'avatares', true)
ON CONFLICT (id) DO NOTHING;

-- Leitura: o bucket já é público (serve via /storage/v1/object/public/... sem
-- passar pela RLS), esta policy só cobre acesso autenticado via API/dashboard.
CREATE POLICY avatares_select ON storage.objects
  FOR SELECT USING (bucket_id = 'avatares');

-- Escrita: só o dono do avatar (primeiro segmento do path = auth.uid()).
CREATE POLICY avatares_insert ON storage.objects
  FOR INSERT WITH CHECK (
    bucket_id = 'avatares'
    AND (storage.foldername(name))[1] = auth.uid()::text
  );

CREATE POLICY avatares_update ON storage.objects
  FOR UPDATE USING (
    bucket_id = 'avatares' AND (storage.foldername(name))[1] = auth.uid()::text
  ) WITH CHECK (
    bucket_id = 'avatares' AND (storage.foldername(name))[1] = auth.uid()::text
  );

CREATE POLICY avatares_delete ON storage.objects
  FOR DELETE USING (
    bucket_id = 'avatares' AND (storage.foldername(name))[1] = auth.uid()::text
  );

COMMIT;
