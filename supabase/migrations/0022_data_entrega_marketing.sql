-- 0022_data_entrega_marketing.sql
-- Marketing trabalha por DEMANDA: em vez de prioridade, o solicitante define uma
-- DATA DE ENTREGA (mínimo de 48h / 2 dias — validado na aplicação). Quando o
-- chamado tem `data_entrega`, o prazo de SLA (limite_resolucao) passa a ser essa
-- data (18h, horário de Brasília), ignorando o cálculo por prioridade.

BEGIN;

-- 1. Coluna de data de entrega desejada (só usada pelo fluxo do Marketing).
ALTER TABLE chamados ADD COLUMN IF NOT EXISTS data_entrega date;

-- 2. Trigger de SLA: se houver data_entrega, o prazo é ela; senão, mantém o
--    cálculo por prioridade em horário comercial (0017).
CREATE OR REPLACE FUNCTION calcular_sla_chamado()
RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path TO 'public' AS $$
DECLARE
  p       planos_sla%ROWTYPE;
  v_base  timestamptz := COALESCE(NEW.created_at, now());
  v_resp  integer;
  v_resol integer;
  v_tem_plano boolean := false;
BEGIN
  -- Fluxo por DEMANDA (Marketing): o prazo é a data de entrega (18h Brasília).
  IF NEW.data_entrega IS NOT NULL THEN
    NEW.limite_resolucao := (NEW.data_entrega + time '18:00') AT TIME ZONE 'America/Sao_Paulo';
    NEW.limite_resposta  := NEW.limite_resolucao;
    RETURN NEW;
  END IF;

  SELECT ps.* INTO p
  FROM empresas e
  JOIN planos_sla ps ON ps.id = e.plano_sla_id
  WHERE e.id = NEW.empresa_id;

  IF FOUND THEN
    v_tem_plano := true;
  END IF;

  IF NOT v_tem_plano THEN
    v_resp  := 12 * 60;
    v_resol := 24 * 60;
  ELSE
    -- ---------- RESPOSTA ----------
    IF NEW.prioridade = 'URGENTE' THEN
      IF p.resposta_alta_min IS NOT NULL THEN
        v_resp := CEIL(p.resposta_alta_min / 2.0);
      ELSE
        v_resp := p.resposta_default_min;
      END IF;
    ELSE
      v_resp := CASE NEW.prioridade
        WHEN 'BAIXA' THEN p.resposta_baixa_min
        WHEN 'MEDIA' THEN p.resposta_media_min
        WHEN 'ALTA'  THEN p.resposta_alta_min
      END;
      IF v_resp IS NULL THEN
        v_resp := p.resposta_default_min;
      END IF;
    END IF;

    -- ---------- RESOLUÇÃO ----------
    IF NEW.prioridade = 'URGENTE' THEN
      IF p.resolucao_alta_min IS NOT NULL THEN
        v_resol := CEIL(p.resolucao_alta_min / 2.0);
      ELSE
        v_resol := p.resolucao_default_min;
      END IF;
    ELSE
      v_resol := CASE NEW.prioridade
        WHEN 'BAIXA' THEN p.resolucao_baixa_min
        WHEN 'MEDIA' THEN p.resolucao_media_min
        WHEN 'ALTA'  THEN p.resolucao_alta_min
      END;
      IF v_resol IS NULL THEN
        v_resol := p.resolucao_default_min;
      END IF;
    END IF;

    IF v_resp  IS NULL THEN v_resp  := 12 * 60; END IF;
    IF v_resol IS NULL THEN v_resol := 24 * 60; END IF;
  END IF;

  NEW.limite_resposta  := sla_add_minutos_uteis(v_base, v_resp);
  NEW.limite_resolucao := sla_add_minutos_uteis(v_base, v_resol);
  RETURN NEW;
END;
$$;

-- 3. O trigger passa a recalcular também quando a data_entrega muda.
DROP TRIGGER IF EXISTS calcular_sla ON chamados;
CREATE TRIGGER calcular_sla
  BEFORE INSERT OR UPDATE OF prioridade, data_entrega ON chamados
  FOR EACH ROW EXECUTE FUNCTION calcular_sla_chamado();

COMMIT;
