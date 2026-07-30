-- 0067_fix_enforce_combinacao_bloqueia_abertura.sql
-- Hotfix (2026-07-30): a 0065 quebrou a ABERTURA DE CHAMADO inteira para
-- qualquer funcionário (role CLIENTE), em qualquer departamento.
--
-- `enforce_combinacao_chamados()` dispara em `BEFORE INSERT OR UPDATE OF
-- chamado_principal_id`. No ramo de INSERT (chamado novo, sem passar por
-- combinação nenhuma) o `IF TG_OP = 'UPDATE' ...` de cima não devolve cedo,
-- então o código caía direto no:
--
--     IF auth.uid() IS NOT NULL AND auth_role() = 'CLIENTE' THEN
--       RAISE EXCEPTION 'Somente o staff do setor de destino pode combinar chamados.';
--     END IF;
--
-- ...que bloqueava TODO INSERT feito por um CLIENTE, mesmo com
-- `chamado_principal_id` NULL (o caso normal de "abrir um chamado"). A
-- intenção do autor (clara pelo bloco seguinte, que zera `combinado_em`/
-- `combinado_por` quando `chamado_principal_id IS NULL`) sempre foi barrar só
-- quem está de fato tentando SETAR uma combinação — a checagem de papel só
-- precisa rodar depois de saber que `chamado_principal_id` não é NULL.
--
-- Confirmado ao vivo (script de diagnóstico contra produção, chamado de teste
-- no TI, rollback automático): o INSERT falha com exatamente essa mensagem
-- antes de qualquer coisa em `chamados` ser gravada — nenhum chamado é criado,
-- para nenhum departamento, por nenhum funcionário, desde o deploy da 0065.

BEGIN;

CREATE OR REPLACE FUNCTION enforce_combinacao_chamados()
RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path TO 'public' AS $$
DECLARE
  v_dep_principal  uuid;
  v_principal_de   uuid;
  v_encontrado     boolean := false;
BEGIN
  -- UPDATE que não mexeu na coluna (o trigger dispara por `UPDATE OF`, mas o
  -- mesmo comando pode estar alterando várias colunas de uma vez). IFs
  -- ANINHADOS de propósito: o Postgres não garante curto-circuito num `AND`, e
  -- referenciar OLD no ramo de INSERT levantaria "record old is not assigned yet".
  IF TG_OP = 'UPDATE' THEN
    IF NEW.chamado_principal_id IS NOT DISTINCT FROM OLD.chamado_principal_id THEN
      RETURN NEW;
    END IF;
  END IF;

  -- Abertura normal (INSERT) e qualquer UPDATE que LIMPE a combinação: não é
  -- "combinar", então não passa pela trava de papel — senão nenhum CLIENTE
  -- conseguiria abrir chamado (0067, bug da 0065: a trava rodava ANTES desta
  -- checagem e vetava todo INSERT, com ou sem combinação envolvida).
  IF NEW.chamado_principal_id IS NULL THEN
    NEW.combinado_em  := NULL;
    NEW.combinado_por := NULL;
    RETURN NEW;
  END IF;

  -- Daqui pra baixo é sempre uma tentativa de SETAR chamado_principal_id —
  -- `auth.uid() IS NOT NULL` para não travar `admin_connection()`/migrations
  -- (sem claims, `auth_role()` é NULL) — o alvo aqui é o usuário autenticado.
  IF auth.uid() IS NOT NULL AND auth_role() = 'CLIENTE' THEN
    RAISE EXCEPTION 'Somente o staff do setor de destino pode combinar chamados.';
  END IF;

  SELECT c.departamento_id, c.chamado_principal_id, true
    INTO v_dep_principal, v_principal_de, v_encontrado
    FROM chamados c WHERE c.id = NEW.chamado_principal_id;

  IF NOT v_encontrado THEN
    RAISE EXCEPTION 'Chamado principal % não existe.', NEW.chamado_principal_id;
  END IF;
  IF v_principal_de IS NOT NULL THEN
    RAISE EXCEPTION 'O chamado principal já é um duplicado de outro — combine com o chamado original.';
  END IF;
  IF v_dep_principal IS DISTINCT FROM NEW.departamento_id THEN
    RAISE EXCEPTION 'Só é possível combinar chamados do mesmo departamento de destino.';
  END IF;
  IF EXISTS (SELECT 1 FROM chamados f WHERE f.chamado_principal_id = NEW.id) THEN
    RAISE EXCEPTION 'Este chamado já é o principal de outros — desfaça as combinações antes.';
  END IF;

  NEW.combinado_em  := now();
  NEW.combinado_por := auth.uid();
  RETURN NEW;
END;
$$;

REVOKE EXECUTE ON FUNCTION enforce_combinacao_chamados() FROM public, anon, authenticated;

COMMIT;
