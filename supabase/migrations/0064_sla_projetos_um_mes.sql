-- 0064_sla_projetos_um_mes.sql
-- Decisão de produto 2026-07-29 (pedido do gestor): a coluna "Projetos" (status
-- PROJETOS, exclusivo do TI, migration 0057) tinha o MESMO prazo do atendimento
-- reativo — 24h úteis do plano da empresa, ou o que a prioridade mandasse. Na
-- prática todo card arrastado pra lá nascia vermelho/piscando em um ou dois
-- dias, porque projeto é trabalho de fôlego, não chamado de suporte. O prazo de
-- resolução de quem entra em PROJETOS passa a ser de **1 mês**.
--
-- "1 mês" em MINUTOS ÚTEIS (a mesma moeda de todo o SLA desde a 0017): 22 dias
-- úteis x 10h = 13.200 minutos, que caem ~30 dias corridos à frente e sempre
-- num horário de expediente — em vez de `now() + interval '1 month'`, que
-- poderia vencer num domingo às 3h da manhã.
--
-- Base do prazo é a ENTRADA na coluna (não o `created_at`): o chamado pode ter
-- passado dias em triagem/atendimento antes de virar projeto, e o mês é o
-- compromisso assumido no momento em que a demanda foi classificada como tal.
-- Sair de PROJETOS (voltar pra EM_ATENDIMENTO, resolver) NÃO recalcula nada: o
-- prazo do projeto continua sendo o prazo daquele trabalho. Reentrar na coluna
-- (PROJETOS -> outro -> PROJETOS) recomeça o mês — é uma reclassificação
-- explícita, feita por um operador.
--
-- Duas peças, porque o prazo é atacado por dois caminhos:
--   1. `sla_projetos_um_mes` (novo trigger): dá o mês ao ENTRAR na coluna.
--   2. `calcular_sla_chamado` (0040, reescrita aqui): a troca de PRIORIDADE
--      recalculava o prazo pelo plano da empresa e derrubaria o mês do projeto
--      — agora ela preserva o prazo de quem já está em PROJETOS.
--
-- Ordem de disparo: o Postgres roda os BEFORE triggers em ordem ALFABÉTICA de
-- nome — `calcular_sla` < `sla_pausa_aguardando` < `sla_projetos_um_mes`. Logo,
-- ao entrar em PROJETOS vindo de AGUARDANDO, a retomada da pausa (0017/0044)
-- roda antes e o mês escrito aqui é a palavra final, como deve ser.

BEGIN;

-- Prazo de um projeto a partir de um instante qualquer, em horário comercial.
-- Função própria (e não o literal espalhado) porque os dois pontos abaixo
-- precisam do MESMO número — e para o dia em que "1 mês" virar parâmetro.
CREATE OR REPLACE FUNCTION sla_prazo_projeto(p_base timestamptz)
RETURNS timestamptz
LANGUAGE sql STABLE SECURITY DEFINER SET search_path TO 'public' AS $$
  SELECT sla_add_minutos_uteis(p_base, 22 * 10 * 60);  -- 22 dias úteis ~ 1 mês
$$;

-- 1) Entrada na coluna "Projetos" => prazo de 1 mês a partir de agora.
CREATE OR REPLACE FUNCTION sla_projetos_um_mes()
RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path TO 'public' AS $$
DECLARE
  v_entrando boolean;
  v_base     timestamptz;
BEGIN
  IF TG_OP = 'INSERT' THEN
    -- Ninguém abre chamado direto em PROJETOS pelo portal; fica coberto só para
    -- carga/migração de dados não nascer com prazo de suporte.
    v_entrando := NEW.status = 'PROJETOS';
    v_base     := COALESCE(NEW.created_at, now());
  ELSE
    v_entrando := NEW.status = 'PROJETOS' AND OLD.status IS DISTINCT FROM 'PROJETOS';
    v_base     := now();
  END IF;

  -- `sem_prazo` (0040) é escolha explícita de "não existe prazo" e continua
  -- ganhando de tudo: um projeto marcado assim segue sem contagem nenhuma.
  IF v_entrando AND NOT NEW.sem_prazo THEN
    NEW.limite_resolucao := sla_prazo_projeto(v_base);
    -- Os dois prazos andam juntos em todos os ramos da `calcular_sla_chamado`;
    -- deixar o `limite_resposta` de suporte (12h) vencido embaixo de um projeto
    -- de um mês só sujaria a exportação do Admin.
    NEW.limite_resposta := NEW.limite_resolucao;
  END IF;

  RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS sla_projetos_um_mes ON chamados;
CREATE TRIGGER sla_projetos_um_mes
  BEFORE INSERT OR UPDATE OF status ON chamados
  FOR EACH ROW EXECUTE FUNCTION sla_projetos_um_mes();

-- 2) Recalculo por prioridade/data_entrega/sem_prazo: idêntica à 0040, exceto o
--    ramo novo de PROJETOS logo abaixo do `sem_prazo`.
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

  -- Coluna "Projetos" (TI, 0057): o prazo é o mês dado na entrada da coluna
  -- (trigger `sla_projetos_um_mes`) e NÃO é função da prioridade — mudar a
  -- prioridade de um projeto não pode reduzi-lo às 24h do suporte. Só calcula
  -- quando ainda não há prazo (INSERT já em PROJETOS, sem passar pela entrada).
  IF NEW.status = 'PROJETOS' THEN
    IF NEW.limite_resolucao IS NULL THEN
      NEW.limite_resolucao := sla_prazo_projeto(v_base);
    END IF;
    NEW.limite_resposta := COALESCE(NEW.limite_resposta, NEW.limite_resolucao);
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

-- 3) Backfill dos projetos que já estão na coluna com o prazo antigo de suporte
--    (o TI usa PROJETOS desde 2026-07-24): os não resolvidos ganham o mês a
--    partir de AGORA. Quem já foi resolvido fica como está — reescrever prazo
--    de chamado fechado falsearia o "SLA cumprido" histórico do Admin.
UPDATE chamados
   SET limite_resolucao = sla_prazo_projeto(now()),
       limite_resposta  = sla_prazo_projeto(now())
 WHERE status = 'PROJETOS'
   AND resolvido_em IS NULL
   AND NOT sem_prazo;

-- 4) Hardening (mesmo padrão da 0017): nada disso é RPC.
REVOKE EXECUTE ON FUNCTION sla_prazo_projeto(timestamptz) FROM public, anon, authenticated;
REVOKE EXECUTE ON FUNCTION sla_projetos_um_mes()          FROM public, anon, authenticated;
REVOKE EXECUTE ON FUNCTION calcular_sla_chamado()         FROM public, anon, authenticated;

COMMIT;
