-- 0089_codigo_chamado_prefixo_bd.sql
-- Pedido do usuário (2026-08-27): prefixo do código do chamado passa de
-- `BOND-YYYY-NNNNN` para `BD-YYYY-NNNNN`. Duas partes:
--
--   1. `gerar_codigo_chamado()` (trigger `BEFORE INSERT ON chamados`,
--      `0003_triggers.sql`) — só a literal do prefixo muda; contador anual
--      (`contador_chamados`), padding de 5 dígitos e a lógica de overflow
--      (Seção 5.3 do plano mestre) ficam intactos.
--   2. Chamados já existentes (produção) são renomeados no mesmo `BD-` —
--      pedido explícito do usuário, não só os novos daqui pra frente.
--      `codigo` é `text` opaco em toda a aplicação (busca por `ILIKE`,
--      display, links de e-mail usam o `id` UUID — não o código), então
--      não há parsing de prefixo em código Python a ajustar.

CREATE OR REPLACE FUNCTION gerar_codigo_chamado()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
  v_ano integer := EXTRACT(YEAR FROM (now() AT TIME ZONE 'America/Sao_Paulo'))::int;
  v_seq integer;
BEGIN
  -- respeita código já informado explicitamente (caso raro / importação)
  IF NEW.codigo IS NOT NULL AND NEW.codigo <> '' THEN
    RETURN NEW;
  END IF;

  -- upsert atômico: evita corrida na virada de ano e concorrência
  INSERT INTO contador_chamados (ano, ultimo)
  VALUES (v_ano, 1)
  ON CONFLICT (ano) DO UPDATE SET ultimo = contador_chamados.ultimo + 1
  RETURNING ultimo INTO v_seq;

  -- LPAD não trunca: ao passar de 99999, o código vira BD-YYYY-NNNNNN
  IF v_seq > 99999 THEN
    RAISE WARNING 'gerar_codigo_chamado: overflow de padding no ano % (seq=%), expandindo dígitos', v_ano, v_seq;
  END IF;

  NEW.codigo := 'BD-' || v_ano::text || '-' || LPAD(v_seq::text, 5, '0');
  RETURN NEW;
END;
$$;

-- Renomeia os chamados já existentes: só troca o prefixo, ano/sequência/
-- padding preservados. `LIKE 'BOND-%'` garante idempotência (reaplicar a
-- migration não duplica o `BD-`).
UPDATE chamados
SET codigo = 'BD-' || substring(codigo FROM 6)
WHERE codigo LIKE 'BOND-%';
