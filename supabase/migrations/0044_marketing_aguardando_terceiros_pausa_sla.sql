-- 0044_marketing_aguardando_terceiros_pausa_sla.sql
-- Decisão de produto 2026-07-13: "Aguardando terceiros" pausa o prazo de
-- entrega igual o "Aguardando" já faz (0017) — o setor não está parado por
-- culpa própria, está esperando um fornecedor/parceiro externo. Generaliza
-- a trigger de pausa para tratar os dois status como "pausados": transitar
-- diretamente entre eles (terceiros -> validação ou vice-versa) mantém o
-- relógio pausado sem resetar `aguardando_desde`.

CREATE OR REPLACE FUNCTION sla_pausa_aguardando()
RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path TO 'public' AS $$
DECLARE
  mins_pausa int;
  estava_pausado boolean;
  esta_pausado  boolean;
BEGIN
  estava_pausado := OLD.status IN ('AGUARDANDO', 'AGUARDANDO_TERCEIROS');
  esta_pausado  := NEW.status IN ('AGUARDANDO', 'AGUARDANDO_TERCEIROS');

  IF esta_pausado AND NOT estava_pausado THEN
    NEW.aguardando_desde := now();
  ELSIF estava_pausado AND NOT esta_pausado THEN
    IF OLD.aguardando_desde IS NOT NULL AND NEW.limite_resolucao IS NOT NULL THEN
      mins_pausa := sla_minutos_uteis_entre(OLD.aguardando_desde, now());
      IF mins_pausa > 0 THEN
        NEW.limite_resolucao := sla_add_minutos_uteis(NEW.limite_resolucao, mins_pausa);
      END IF;
    END IF;
    NEW.aguardando_desde := NULL;
  END IF;
  RETURN NEW;
END;
$$;

REVOKE EXECUTE ON FUNCTION sla_pausa_aguardando() FROM public, anon, authenticated;
