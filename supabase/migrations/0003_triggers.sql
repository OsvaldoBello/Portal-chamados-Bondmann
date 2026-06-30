-- Fase 2 / Seção 5.3 — Functions e Triggers

-- ============================================================
-- 1. trigger_set_timestamp — seta updated_at = now() em BEFORE UPDATE
--    Aplicado nas tabelas que possuem updated_at:
--    planos_sla, empresas, perfis, chamados
-- ============================================================
CREATE OR REPLACE FUNCTION trigger_set_timestamp()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = public
AS $$
BEGIN
  NEW.updated_at = now();
  RETURN NEW;
END;
$$;

CREATE TRIGGER set_timestamp_planos_sla
  BEFORE UPDATE ON planos_sla
  FOR EACH ROW EXECUTE FUNCTION trigger_set_timestamp();

CREATE TRIGGER set_timestamp_empresas
  BEFORE UPDATE ON empresas
  FOR EACH ROW EXECUTE FUNCTION trigger_set_timestamp();

CREATE TRIGGER set_timestamp_perfis
  BEFORE UPDATE ON perfis
  FOR EACH ROW EXECUTE FUNCTION trigger_set_timestamp();

CREATE TRIGGER set_timestamp_chamados
  BEFORE UPDATE ON chamados
  FOR EACH ROW EXECUTE FUNCTION trigger_set_timestamp();

-- ============================================================
-- 2. gerar_codigo_chamado — BOND-YYYY-NNNNN via contador anual atômico
--    (Seção 5.3: sem MAX()+1; reset anual; padding 5; overflow -> 6+)
-- ============================================================
CREATE TABLE contador_chamados (
  ano    integer PRIMARY KEY,
  ultimo integer NOT NULL DEFAULT 0
);

-- SECURITY DEFINER: o INSERT no contador roda como dono, independente do RLS
-- do papel que está criando o chamado (CLIENTE/OPERADOR).
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

  -- LPAD não trunca: ao passar de 99999, o código vira BOND-YYYY-NNNNNN
  IF v_seq > 99999 THEN
    RAISE WARNING 'gerar_codigo_chamado: overflow de padding no ano % (seq=%), expandindo dígitos', v_ano, v_seq;
  END IF;

  NEW.codigo := 'BOND-' || v_ano::text || '-' || LPAD(v_seq::text, 5, '0');
  RETURN NEW;
END;
$$;

CREATE TRIGGER gerar_codigo
  BEFORE INSERT ON chamados
  FOR EACH ROW EXECUTE FUNCTION gerar_codigo_chamado();

-- ============================================================
-- 3. calcular_sla_chamado — limite_resposta/limite_resolucao
--    Escada de fallback C1: URGENTE(50% ALTA) -> prioridade do plano
--    -> default do plano -> fallback global (resposta 12h / resolução 24h)
--    Tempos do plano em MINUTOS. Relógio inicia na criação (created_at).
--    SECURITY DEFINER: lê empresas/planos_sla independentemente do RLS do caller.
-- ============================================================
CREATE OR REPLACE FUNCTION calcular_sla_chamado()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
DECLARE
  p       planos_sla%ROWTYPE;
  v_base  timestamptz := COALESCE(NEW.created_at, now());
  v_resp  integer;   -- minutos p/ resposta
  v_resol integer;   -- minutos p/ resolução
  v_tem_plano boolean := false;
BEGIN
  SELECT ps.* INTO p
  FROM empresas e
  JOIN planos_sla ps ON ps.id = e.plano_sla_id
  WHERE e.id = NEW.empresa_id;

  IF FOUND THEN
    v_tem_plano := true;
  END IF;

  IF NOT v_tem_plano THEN
    -- passo 4: empresa sem plano configurado -> fallback global
    v_resp  := 12 * 60;
    v_resol := 24 * 60;
  ELSE
    -- ---------- RESPOSTA ----------
    IF NEW.prioridade = 'URGENTE' THEN
      IF p.resposta_alta_min IS NOT NULL THEN
        v_resp := CEIL(p.resposta_alta_min / 2.0);            -- passo 1: 50% de ALTA
      ELSE
        v_resp := p.resposta_default_min;                    -- passo 3: ALTA inexistente
      END IF;
    ELSE
      v_resp := CASE NEW.prioridade
        WHEN 'BAIXA' THEN p.resposta_baixa_min
        WHEN 'MEDIA' THEN p.resposta_media_min
        WHEN 'ALTA'  THEN p.resposta_alta_min
      END;
      IF v_resp IS NULL THEN
        v_resp := p.resposta_default_min;                    -- passo 3: prioridade não configurada
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

    -- defesa: se até o default do plano for nulo, cai no fallback global por métrica
    IF v_resp  IS NULL THEN v_resp  := 12 * 60; END IF;
    IF v_resol IS NULL THEN v_resol := 24 * 60; END IF;
  END IF;

  NEW.limite_resposta  := v_base + make_interval(mins => v_resp);
  NEW.limite_resolucao := v_base + make_interval(mins => v_resol);
  RETURN NEW;
END;
$$;

-- BEFORE INSERT e em mudança de prioridade (relógio sempre relativo a created_at)
CREATE TRIGGER calcular_sla
  BEFORE INSERT OR UPDATE OF prioridade ON chamados
  FOR EACH ROW EXECUTE FUNCTION calcular_sla_chamado();

-- ============================================================
-- 4. handle_new_user — cria perfil CLIENTE ao surgir auth.users
-- ============================================================
CREATE OR REPLACE FUNCTION handle_new_user()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public
AS $$
BEGIN
  INSERT INTO public.perfis (id, nome, role)
  VALUES (NEW.id, NEW.raw_user_meta_data->>'nome', 'CLIENTE')
  ON CONFLICT (id) DO NOTHING;
  RETURN NEW;
END;
$$;

CREATE TRIGGER on_auth_user_created
  AFTER INSERT ON auth.users
  FOR EACH ROW EXECUTE FUNCTION handle_new_user();
