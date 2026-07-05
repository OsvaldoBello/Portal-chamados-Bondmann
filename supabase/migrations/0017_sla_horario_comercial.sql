-- 0017_sla_horario_comercial.sql
-- ---------------------------------------------------------------------------
-- SLA em HORÁRIO COMERCIAL (decisão do gestor, 2026-07-05):
--   * Expediente: segunda a sexta, 08:00–18:00 (America/Sao_Paulo).
--   * Para em FERIADOS (tabela `feriados`, editável pelo TI).
--   * O status AGUARDANDO PAUSA o relógio de RESOLUÇÃO (aguardando o cliente);
--     ao sair de AGUARDANDO, o prazo é empurrado pelos minutos úteis pausados.
-- Vale para os chamados criados/repriorizados A PARTIR DAQUI (o trigger recalcula).
-- Timestamps continuam em UTC (timestamptz); a janela é avaliada no fuso local.
-- ---------------------------------------------------------------------------

-- 1) Tabela de feriados (editável). Leitura por autenticado; escrita só TI.
CREATE TABLE IF NOT EXISTS feriados (
  data       date PRIMARY KEY,
  descricao  text NOT NULL DEFAULT '',
  created_at timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE feriados ENABLE ROW LEVEL SECURITY;

GRANT SELECT, INSERT, UPDATE, DELETE ON feriados TO authenticated;

DROP POLICY IF EXISTS feriados_select ON feriados;
CREATE POLICY feriados_select ON feriados
  FOR SELECT TO authenticated USING (true);

DROP POLICY IF EXISTS feriados_admin ON feriados;
CREATE POLICY feriados_admin ON feriados
  FOR ALL TO authenticated USING (auth_is_ti()) WITH CHECK (auth_is_ti());

-- Seed: feriados NACIONAIS 2026–2028 (fixos + Sexta-feira Santa). Carnaval e
-- Corpus Christi são pontos facultativos — adicione-os aqui se a empresa observar.
INSERT INTO feriados (data, descricao) VALUES
  ('2026-01-01','Confraternização Universal'),
  ('2026-04-03','Sexta-feira Santa'),
  ('2026-04-21','Tiradentes'),
  ('2026-05-01','Dia do Trabalho'),
  ('2026-09-07','Independência'),
  ('2026-10-12','Nossa Senhora Aparecida'),
  ('2026-11-02','Finados'),
  ('2026-11-15','Proclamação da República'),
  ('2026-11-20','Consciência Negra'),
  ('2026-12-25','Natal'),
  ('2027-01-01','Confraternização Universal'),
  ('2027-03-26','Sexta-feira Santa'),
  ('2027-04-21','Tiradentes'),
  ('2027-05-01','Dia do Trabalho'),
  ('2027-09-07','Independência'),
  ('2027-10-12','Nossa Senhora Aparecida'),
  ('2027-11-02','Finados'),
  ('2027-11-15','Proclamação da República'),
  ('2027-11-20','Consciência Negra'),
  ('2027-12-25','Natal'),
  ('2028-01-01','Confraternização Universal'),
  ('2028-04-14','Sexta-feira Santa'),
  ('2028-04-21','Tiradentes'),
  ('2028-05-01','Dia do Trabalho'),
  ('2028-09-07','Independência'),
  ('2028-10-12','Nossa Senhora Aparecida'),
  ('2028-11-02','Finados'),
  ('2028-11-15','Proclamação da República'),
  ('2028-11-20','Consciência Negra'),
  ('2028-12-25','Natal')
ON CONFLICT (data) DO NOTHING;

-- 2) Marca do início da pausa (quando o chamado entra em AGUARDANDO).
ALTER TABLE chamados ADD COLUMN IF NOT EXISTS aguardando_desde timestamptz;

-- 3) Minutos ÚTEIS decorridos entre dois instantes (soma a sobreposição com a
--    janela 08–18 dos dias úteis, pulando fim de semana e feriados).
CREATE OR REPLACE FUNCTION sla_minutos_uteis_entre(p_ini timestamptz, p_fim timestamptz)
RETURNS integer
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path TO 'public' AS $$
DECLARE
  tz  text := 'America/Sao_Paulo';
  a   timestamp := p_ini AT TIME ZONE tz;
  b   timestamp := p_fim AT TIME ZONE tz;
  total int := 0;
  d   date;
  seg_ini timestamp;
  seg_fim timestamp;
  guard int := 0;
BEGIN
  IF b <= a THEN RETURN 0; END IF;
  d := a::date;
  WHILE d <= b::date LOOP
    guard := guard + 1;
    EXIT WHEN guard > 4000;
    IF EXTRACT(isodow FROM d) < 6
       AND NOT EXISTS (SELECT 1 FROM feriados f WHERE f.data = d) THEN
      seg_ini := GREATEST(a, d + time '08:00');
      seg_fim := LEAST(b, d + time '18:00');
      IF seg_fim > seg_ini THEN
        total := total + floor(EXTRACT(EPOCH FROM (seg_fim - seg_ini)) / 60)::int;
      END IF;
    END IF;
    d := d + 1;
  END LOOP;
  RETURN total;
END;
$$;

-- 4) Adiciona `p_minutos` ÚTEIS a um instante, devolvendo o novo prazo.
CREATE OR REPLACE FUNCTION sla_add_minutos_uteis(p_inicio timestamptz, p_minutos integer)
RETURNS timestamptz
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path TO 'public' AS $$
DECLARE
  tz  text := 'America/Sao_Paulo';
  cur timestamp := p_inicio AT TIME ZONE tz;
  restante int := GREATEST(COALESCE(p_minutos, 0), 0);
  ini time := time '08:00';
  fim time := time '18:00';
  d   date;
  disp int;
  guard int := 0;
BEGIN
  LOOP
    guard := guard + 1;
    EXIT WHEN guard > 4000;
    d := cur::date;
    -- pula fim de semana / feriado -> próximo dia às 08:00
    IF EXTRACT(isodow FROM d) >= 6
       OR EXISTS (SELECT 1 FROM feriados f WHERE f.data = d) THEN
      cur := (d + 1) + ini;
      CONTINUE;
    END IF;
    -- normaliza para dentro da janela útil do dia
    IF cur::time < ini THEN
      cur := d + ini;
    ELSIF cur::time >= fim THEN
      cur := (d + 1) + ini;
      CONTINUE;
    END IF;
    -- minutos disponíveis até as 18:00 deste dia
    disp := floor(EXTRACT(EPOCH FROM (fim - cur::time)) / 60)::int;
    IF restante <= disp THEN
      cur := cur + make_interval(mins => restante);
      restante := 0;
      EXIT;
    ELSE
      restante := restante - disp;
      cur := (d + 1) + ini;
    END IF;
  END LOOP;
  RETURN cur AT TIME ZONE tz;
END;
$$;

-- 5) Pausa/retomada do relógio de RESOLUÇÃO ao entrar/sair de AGUARDANDO.
CREATE OR REPLACE FUNCTION sla_pausa_aguardando()
RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path TO 'public' AS $$
DECLARE
  mins_pausa int;
BEGIN
  IF NEW.status = 'AGUARDANDO' AND OLD.status IS DISTINCT FROM 'AGUARDANDO' THEN
    NEW.aguardando_desde := now();
  ELSIF OLD.status = 'AGUARDANDO' AND NEW.status IS DISTINCT FROM 'AGUARDANDO' THEN
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

DROP TRIGGER IF EXISTS sla_pausa_aguardando ON chamados;
CREATE TRIGGER sla_pausa_aguardando
  BEFORE UPDATE OF status ON chamados
  FOR EACH ROW EXECUTE FUNCTION sla_pausa_aguardando();

-- 6) Recalcula os prazos em MINUTOS ÚTEIS (idêntico ao anterior, exceto as duas
--    últimas atribuições, que passam a usar `sla_add_minutos_uteis`).
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

  -- Prazos em HORÁRIO COMERCIAL (antes eram horas corridas).
  NEW.limite_resposta  := sla_add_minutos_uteis(v_base, v_resp);
  NEW.limite_resolucao := sla_add_minutos_uteis(v_base, v_resol);
  RETURN NEW;
END;
$$;

-- 7) Hardening: estas funções não são RPCs — revoga EXECUTE dos papéis expostos
--    (os triggers rodam como owner; as helpers são chamadas internamente).
REVOKE EXECUTE ON FUNCTION sla_minutos_uteis_entre(timestamptz, timestamptz) FROM public, anon, authenticated;
REVOKE EXECUTE ON FUNCTION sla_add_minutos_uteis(timestamptz, integer)       FROM public, anon, authenticated;
REVOKE EXECUTE ON FUNCTION sla_pausa_aguardando()                            FROM public, anon, authenticated;
REVOKE EXECUTE ON FUNCTION calcular_sla_chamado()                            FROM public, anon, authenticated;
