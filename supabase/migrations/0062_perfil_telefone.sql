-- 0062_perfil_telefone.sql
-- Telefone de contato guardado no PERFIL (pedido do usuário, 2026-07-29): o
-- campo obrigatório da abertura (`chamados.telefone_contato`, migration 0058)
-- passa a vir pré-preenchido do perfil, para ninguém precisar digitar o mesmo
-- número a cada chamado. Quem ainda não tem número cadastrado continua vendo o
-- campo vazio e obrigatório no formulário — e o primeiro número informado numa
-- abertura é gravado aqui, então a exigência só aparece uma vez.
--
-- `NOT NULL DEFAULT ''` (mesmo padrão da 0058): backfill sem quebrar as linhas
-- existentes, sem CHECK de não-vazio — "vazio" é justamente o estado "ainda não
-- cadastrou". A validação de formato (mín. de dígitos) fica no servidor
-- (`app/repositories/chamados.py::validar_telefone_contato`, a mesma da
-- abertura). O DEFAULT permanece: diferente de `chamados`, aqui o perfil é
-- criado pelo trigger `handle_new_user` (que não conhece esta coluna).
--
-- `chamados.telefone_contato` NÃO vira derivado deste campo: o chamado guarda o
-- número informado NAQUELA abertura (histórico imutável de contato), o perfil
-- guarda o número atual. Trocar o telefone do perfil não reescreve chamados
-- antigos.

BEGIN;

ALTER TABLE perfis ADD COLUMN IF NOT EXISTS telefone text NOT NULL DEFAULT '';

-- O trigger da 0033 restringia a escrita de não-TI a `avatar_path`/`updated_at`
-- por LISTA de colunas congeladas (`nome`, `role`, `empresa_id`, ...). Como
-- `telefone` é coluna nova, ela ficaria fora dessa lista e, portanto, liberada
-- não só para o dono (policy `perfis_update_self`, 0033) mas também para quem a
-- `perfis_update_avatar_staff` (0052) deixa alcançar a linha de OUTRA pessoa —
-- ADMIN de qualquer setor e OPERADOR do Marketing. Telefone é dado do próprio
-- usuário: só o dono (e o TI) altera. Daí a checagem explícita abaixo.
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
    IF NEW.telefone IS DISTINCT FROM OLD.telefone AND NEW.id <> auth.uid() THEN
      RAISE EXCEPTION 'Você só pode alterar o próprio telefone.';
    END IF;
  END IF;
  RETURN NEW;
END;
$$;

REVOKE ALL ON FUNCTION enforce_perfil_self_so_avatar() FROM PUBLIC, anon, authenticated;

COMMIT;
