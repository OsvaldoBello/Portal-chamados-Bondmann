-- 0040_marketing_sem_prazo.sql
-- Decisão de produto 2026-07-10: o Marketing pode abrir uma demanda SEM data de
-- entrega — sem urgência, sem prazo determinado, feita "quando sobrar tempo".
-- Diferente de simplesmente deixar `data_entrega` em branco (que hoje cai no
-- cálculo de SLA por prioridade/plano, migration 0022), o chamado "sem prazo"
-- não deve ter NENHUM prazo de resolução: sem contagem, sem virar "atrasado".
-- A UI já trata `limite_resolucao IS NULL` como estado "indefinido"/"Sem prazo"
-- (app/domain/sla_visual.py), então basta a trigger não calcular nada.

BEGIN;

-- 1. Marcador explícito de "sem prazo" (só usado pelo fluxo do Marketing).
ALTER TABLE chamados ADD COLUMN IF NOT EXISTS sem_prazo boolean NOT NULL DEFAULT false;

-- Coerência: um chamado não pode ser "sem prazo" e ter uma data de entrega ao
-- mesmo tempo (mesmo padrão de constraint de coerência da 0006).
ALTER TABLE chamados DROP CONSTRAINT IF EXISTS chamados_sem_prazo_coerente;
ALTER TABLE chamados ADD CONSTRAINT chamados_sem_prazo_coerente
  CHECK (NOT (sem_prazo AND data_entrega IS NOT NULL));

-- 2. Trigger de SLA: sem_prazo tem prioridade sobre data_entrega/plano — não
--    calcula limite nenhum. Mantém o restante igual à 0022.
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
  -- Fluxo "sem prazo" (Marketing): sem urgência, sem data — não há SLA.
  IF NEW.sem_prazo THEN
    NEW.limite_resposta  := NULL;
    NEW.limite_resolucao := NULL;
    RETURN NEW;
  END IF;

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

-- 3. O trigger passa a recalcular também quando sem_prazo muda.
DROP TRIGGER IF EXISTS calcular_sla ON chamados;
CREATE TRIGGER calcular_sla
  BEFORE INSERT OR UPDATE OF prioridade, data_entrega, sem_prazo ON chamados
  FOR EACH ROW EXECUTE FUNCTION calcular_sla_chamado();

COMMIT;
