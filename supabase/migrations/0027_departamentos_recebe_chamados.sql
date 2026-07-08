-- 0027_departamentos_recebe_chamados.sql
-- Unifica "Setor" (afiliação de quem abre o chamado, antes hardcoded em SETORES
-- no Python) com "Departamento" (destino do chamado, já era uma tabela gerenciável).
-- Um único catálogo `departamentos` passa a cobrir os dois papéis: todo setor da
-- empresa tem uma linha aqui; `recebe_chamados` marca os que têm staff/fila de
-- atendimento (hoje TI/RH/Marketing) e por isso podem ser DESTINO de chamado.
-- Setores como Comercial/Financeiro/Diretoria só identificam quem abriu o chamado.

BEGIN;

ALTER TABLE departamentos ADD COLUMN IF NOT EXISTS recebe_chamados boolean NOT NULL DEFAULT false;
COMMENT ON COLUMN departamentos.recebe_chamados IS
  'true = departamento tem staff e pode ser destino de chamado (aparece em "Departamento de destino", tem fila/RLS de atendimento). false = setor só usado para identificar quem abriu o chamado (aparece em "Setor").';

UPDATE departamentos SET recebe_chamados = true WHERE nome IN ('TI', 'RH', 'Marketing');

-- Setores que hoje só ABREM chamado (lista antes hardcoded em SETORES, app/routes/portal.py).
INSERT INTO departamentos (nome, recebe_chamados) VALUES
  ('Brigadistas', false),
  ('Comercial', false),
  ('Controladoria', false),
  ('Diretoria', false),
  ('Dpto Químico', false),
  ('Financeiro', false),
  ('SIG', false)
ON CONFLICT (nome) DO NOTHING;

-- ============================================================
-- Guarda-corpo: staff (perfis.departamento_id) e o destino de um chamado
-- (chamados.departamento_id) só podem apontar para departamentos que de fato
-- recebem chamado. Sem isso seria possível, por SQL direto, promover um
-- OPERADOR para um setor sem fila (ficaria sem ver chamado nenhum, silenciosamente).
-- ============================================================
CREATE OR REPLACE FUNCTION enforce_departamento_recebe_chamados()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = public
AS $$
BEGIN
  IF NEW.departamento_id IS NOT NULL AND NOT EXISTS (
    SELECT 1 FROM departamentos WHERE id = NEW.departamento_id AND recebe_chamados = true
  ) THEN
    RAISE EXCEPTION 'departamento_id % não recebe chamados (setor sem fila de atendimento)', NEW.departamento_id;
  END IF;
  RETURN NEW;
END;
$$;
REVOKE ALL ON FUNCTION enforce_departamento_recebe_chamados() FROM PUBLIC, anon, authenticated;

DROP TRIGGER IF EXISTS trg_perfis_departamento_recebe ON perfis;
CREATE TRIGGER trg_perfis_departamento_recebe
  BEFORE INSERT OR UPDATE OF departamento_id ON perfis
  FOR EACH ROW EXECUTE FUNCTION enforce_departamento_recebe_chamados();

DROP TRIGGER IF EXISTS trg_chamados_departamento_recebe ON chamados;
CREATE TRIGGER trg_chamados_departamento_recebe
  BEFORE INSERT OR UPDATE OF departamento_id ON chamados
  FOR EACH ROW EXECUTE FUNCTION enforce_departamento_recebe_chamados();

COMMIT;
