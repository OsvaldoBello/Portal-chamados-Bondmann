-- 0066_sla_projetos_prazo_configuravel.sql
-- Decisão de produto 2026-07-30 (pedido do gestor): o prazo da coluna "Projetos"
-- (status PROJETOS, exclusivo do TI, 0057) deixa de ser o **1 mês fixo** da 0064
-- e passa a ser **configurável pelo operador de TI**, projeto a projeto: 2 meses,
-- 70 dias, 4 meses, o que aquele trabalho exigir. Cada projeto é um escopo
-- diferente — o mês fixo resolveu o problema anterior (card nascer vermelho com
-- as 24h do suporte reativo), mas continuava sendo um número só para todos.
--
-- Dois níveis, porque as duas perguntas são diferentes:
--   1. **Padrão do setor** — `planos_sla.projeto_dias` (30 = o mês da 0064).
--      Editável em /admin/gestao, junto dos demais tempos de SLA. Vale para todo
--      projeto que não tiver prazo próprio.
--      É o mesmo lugar onde já mora todo tempo de SLA da organização; um projeto
--      sem prazo definido continua nascendo com um número sensato.
--   2. **Por chamado** — `chamados.prazo_projeto_dias` (NULL = usa o padrão).
--      O operador digita os dias na tela de atendimento. É o pedido em si.
--
-- UNIDADE: **dias corridos** na interface (é como o gestor pensa: "dois meses"),
-- convertidos para MINUTOS ÚTEIS no banco (a moeda de todo o SLA desde a 0017,
-- que pula fim de semana/feriado e faz o prazo cair sempre em expediente). A
-- conversão é a mesma proporção que a 0064 fixou no literal `22 * 10 * 60`:
-- 22 dias úteis para 30 corridos. Logo `dias_uteis = round(dias * 22/30)` e
-- `minutos = dias_uteis * 10h`. Para 30 dias isso dá exatamente os 13.200
-- minutos da 0064 — quem já está na coluna não muda de prazo por causa desta
-- migration.
--
-- BASE do prazo continua sendo a ENTRADA na coluna (regra da 0064), agora
-- MATERIALIZADA em `chamados.projeto_em`: sem ela, mudar os dias de um projeto
-- que entrou há três semanas recontaria tudo a partir de hoje, esticando o
-- projeto em silêncio. Com ela, "70 dias" significa 70 dias desde que a demanda
-- foi classificada como projeto, seja a troca feita hoje ou daqui a um mês.
--
-- SEGURANÇA (a mesma armadilha da 0065, e pelo mesmo motivo):
-- `enforce_cliente_so_avaliacao` é uma **lista de colunas**, então toda coluna
-- nova em `chamados` nasce LIBERADA para o autor num UPDATE do próprio chamado
-- (as policies `chamados_update_cliente_*` já permitem a linha). Sem o reforço
-- abaixo, um funcionário poderia dar a si mesmo o prazo que quisesse. As duas
-- colunas novas entram na lista.

BEGIN;

-- ============================================================
-- 1. Padrão do setor: dias de projeto no plano de SLA
-- ============================================================
ALTER TABLE planos_sla
  ADD COLUMN IF NOT EXISTS projeto_dias integer NOT NULL DEFAULT 30;

ALTER TABLE planos_sla DROP CONSTRAINT IF EXISTS planos_sla_projeto_dias_faixa;
ALTER TABLE planos_sla ADD CONSTRAINT planos_sla_projeto_dias_faixa
  CHECK (projeto_dias BETWEEN 1 AND 730);

COMMENT ON COLUMN planos_sla.projeto_dias IS
  'Prazo PADRÃO (dias corridos) de um chamado na coluna "Projetos" (0057/0064/0066). '
  'Usado quando o chamado não tem prazo próprio em chamados.prazo_projeto_dias.';

-- ============================================================
-- 2. Prazo por chamado
-- ============================================================
ALTER TABLE chamados
  ADD COLUMN IF NOT EXISTS prazo_projeto_dias integer,
  ADD COLUMN IF NOT EXISTS projeto_em         timestamptz;

ALTER TABLE chamados DROP CONSTRAINT IF EXISTS chamados_prazo_projeto_faixa;
ALTER TABLE chamados ADD CONSTRAINT chamados_prazo_projeto_faixa
  CHECK (prazo_projeto_dias IS NULL OR prazo_projeto_dias BETWEEN 1 AND 730);

COMMENT ON COLUMN chamados.prazo_projeto_dias IS
  'Prazo (dias corridos) DESTE projeto, definido pelo operador. NULL = usa o '
  'padrão do plano (planos_sla.projeto_dias).';
COMMENT ON COLUMN chamados.projeto_em IS
  'Quando o chamado ENTROU na coluna "Projetos" — base do prazo, para que mudar '
  'os dias não recomece a contagem de hoje.';

-- ============================================================
-- 3. Funções do prazo de projeto
-- ============================================================

-- Padrão do plano da empresa; 30 dias quando não há plano configurado (mesmo
-- espírito do fallback global 12h/24h da C1: nunca ficar sem número).
CREATE OR REPLACE FUNCTION sla_projeto_dias_padrao(p_empresa uuid)
RETURNS integer
LANGUAGE sql STABLE SECURITY DEFINER SET search_path TO 'public' AS $$
  SELECT COALESCE(
    (SELECT ps.projeto_dias
       FROM empresas e
       JOIN planos_sla ps ON ps.id = e.plano_sla_id
      WHERE e.id = p_empresa),
    30);
$$;

-- Prazo de um projeto de `p_dias` dias corridos a partir de `p_base`, em
-- horário comercial. Substitui a versão de 1 argumento da 0064 (que embutia o
-- mês); o literal `22 * 10 * 60` de lá é o caso `p_dias = 30` deste.
CREATE OR REPLACE FUNCTION sla_prazo_projeto(p_base timestamptz, p_dias integer)
RETURNS timestamptz
LANGUAGE sql STABLE SECURITY DEFINER SET search_path TO 'public' AS $$
  SELECT sla_add_minutos_uteis(
           p_base,
           GREATEST(round(COALESCE(p_dias, 30) * 22.0 / 30.0), 1)::int * 10 * 60
         );
$$;

-- ============================================================
-- 4. Entrada na coluna + troca do prazo (trigger)
--    Sucede `sla_projetos_um_mes` (0064). O nome muda porque o mês virou
--    parâmetro — e continua ordenando DEPOIS de `sla_pausa_aguardando`
--    (`sla_pa…` < `sla_pr…`), que é o que garante que a retomada da pausa
--    (0017/0044) não sobrescreva o prazo do projeto.
-- ============================================================
CREATE OR REPLACE FUNCTION sla_projetos_prazo()
RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path TO 'public' AS $$
DECLARE
  v_entrando   boolean;
  v_mudou_dias boolean := false;
  v_base       timestamptz;
BEGIN
  IF TG_OP = 'INSERT' THEN
    -- Ninguém abre chamado direto em PROJETOS pelo portal; fica coberto só para
    -- carga/migração de dados não nascer com prazo de suporte.
    v_entrando := NEW.status = 'PROJETOS';
    v_base     := COALESCE(NEW.created_at, now());
  ELSE
    v_entrando   := NEW.status = 'PROJETOS' AND OLD.status IS DISTINCT FROM 'PROJETOS';
    v_mudou_dias := NEW.prazo_projeto_dias IS DISTINCT FROM OLD.prazo_projeto_dias;
    v_base       := now();
  END IF;

  IF v_entrando THEN
    NEW.projeto_em := v_base;
  END IF;

  -- `sem_prazo` (0040) é escolha explícita de "não existe prazo" e continua
  -- ganhando de tudo: um projeto marcado assim segue sem contagem nenhuma.
  IF (v_entrando OR (v_mudou_dias AND NEW.status = 'PROJETOS')) AND NOT NEW.sem_prazo THEN
    NEW.limite_resolucao := sla_prazo_projeto(
      COALESCE(NEW.projeto_em, NEW.created_at, now()),
      COALESCE(NEW.prazo_projeto_dias, sla_projeto_dias_padrao(NEW.empresa_id))
    );
    -- Os dois prazos andam juntos em todos os ramos da `calcular_sla_chamado`;
    -- deixar o `limite_resposta` de suporte (12h) vencido embaixo de um projeto
    -- de meses só sujaria a exportação do Admin.
    NEW.limite_resposta := NEW.limite_resolucao;
  END IF;

  RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS sla_projetos_um_mes ON chamados;
DROP TRIGGER IF EXISTS sla_projetos_prazo  ON chamados;
CREATE TRIGGER sla_projetos_prazo
  BEFORE INSERT OR UPDATE OF status, prazo_projeto_dias ON chamados
  FOR EACH ROW EXECUTE FUNCTION sla_projetos_prazo();

-- ============================================================
-- 5. Recalculo por prioridade/data_entrega/sem_prazo
--    Idêntica à 0064, exceto o ramo de PROJETOS, que passa a usar o prazo
--    configurado e a base da entrada na coluna.
-- ============================================================
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

  -- Coluna "Projetos" (TI, 0057): o prazo é o que foi configurado para o
  -- projeto (0066) e NÃO é função da prioridade — mudar a prioridade de um
  -- projeto não pode reduzi-lo às 24h do suporte. Só calcula quando ainda não
  -- há prazo (INSERT já em PROJETOS, ou `sem_prazo` desmarcado depois).
  IF NEW.status = 'PROJETOS' THEN
    IF NEW.limite_resolucao IS NULL THEN
      NEW.limite_resolucao := sla_prazo_projeto(
        COALESCE(NEW.projeto_em, v_base),
        COALESCE(NEW.prazo_projeto_dias, sla_projeto_dias_padrao(NEW.empresa_id))
      );
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

-- ============================================================
-- 6. Trava de coluna do CLIENTE — acrescenta as 2 colunas novas
--    (idêntica à 0065 fora isso; ver o cabeçalho desta migration para o porquê)
-- ============================================================
CREATE OR REPLACE FUNCTION enforce_cliente_so_avaliacao()
RETURNS trigger
LANGUAGE plpgsql
SET search_path = public
AS $$
BEGIN
  IF auth_role() = 'CLIENTE' THEN
    IF OLD.status = 'RESOLVIDO' AND NEW.status = 'EM_ATENDIMENTO' THEN
      IF ROW(NEW.codigo, NEW.empresa_id, NEW.cliente_id, NEW.operador_id, NEW.categoria_id,
             NEW.titulo, NEW.descricao, NEW.prioridade,
             NEW.limite_resposta, NEW.limite_resolucao, NEW.respondido_em,
             NEW.created_at,
             NEW.chamado_principal_id, NEW.combinado_em, NEW.combinado_por,
             NEW.prazo_projeto_dias, NEW.projeto_em)
         IS DISTINCT FROM
         ROW(OLD.codigo, OLD.empresa_id, OLD.cliente_id, OLD.operador_id, OLD.categoria_id,
             OLD.titulo, OLD.descricao, OLD.prioridade,
             OLD.limite_resposta, OLD.limite_resolucao, OLD.respondido_em,
             OLD.created_at,
             OLD.chamado_principal_id, OLD.combinado_em, OLD.combinado_por,
             OLD.prazo_projeto_dias, OLD.projeto_em)
      THEN
        RAISE EXCEPTION 'CLIENTE só pode alterar a avaliação do chamado (nota/comentário) ou reabri-lo.';
      END IF;
      IF NEW.resolvido_em IS NOT NULL THEN
        RAISE EXCEPTION 'Reabertura deve limpar resolvido_em.';
      END IF;
      RETURN NEW;
    END IF;

    IF ROW(NEW.codigo, NEW.empresa_id, NEW.cliente_id, NEW.operador_id, NEW.categoria_id,
           NEW.titulo, NEW.descricao, NEW.status, NEW.prioridade,
           NEW.limite_resposta, NEW.limite_resolucao, NEW.respondido_em,
           NEW.resolvido_em, NEW.created_at,
           NEW.chamado_principal_id, NEW.combinado_em, NEW.combinado_por,
           NEW.prazo_projeto_dias, NEW.projeto_em)
       IS DISTINCT FROM
       ROW(OLD.codigo, OLD.empresa_id, OLD.cliente_id, OLD.operador_id, OLD.categoria_id,
           OLD.titulo, OLD.descricao, OLD.status, OLD.prioridade,
           OLD.limite_resposta, OLD.limite_resolucao, OLD.respondido_em,
           OLD.resolvido_em, OLD.created_at,
           OLD.chamado_principal_id, OLD.combinado_em, OLD.combinado_por,
           OLD.prazo_projeto_dias, OLD.projeto_em)
    THEN
      RAISE EXCEPTION 'CLIENTE só pode alterar a avaliação do chamado (nota/comentário).';
    END IF;
  END IF;
  RETURN NEW;
END;
$$;

-- ============================================================
-- 7. Backfill de `projeto_em` para quem JÁ está na coluna
--    O prazo em si NÃO é recalculado (a 0064 já o escreveu, e o padrão de 30
--    dias reproduz o mesmo número): aqui só se recupera a data de entrada, do
--    histórico imutável, para que uma troca futura de dias tenha a base certa.
-- ============================================================
UPDATE chamados c
   SET projeto_em = COALESCE(
         (SELECT max(h.created_at)
            FROM historico_chamados h
           WHERE h.chamado_id = c.id
             AND h.acao = 'STATUS_ALTERADO'
             AND h.detalhes->>'para' = 'PROJETOS'),
         c.created_at)
 WHERE c.status = 'PROJETOS'
   AND c.projeto_em IS NULL;

-- ============================================================
-- 8. A versão de 1 argumento da 0064 sai de cena — dois lugares calculando o
--    prazo de projeto seria exatamente a divergência que esta migration existe
--    para evitar. Depois das reescritas acima, ninguém mais a chama.
-- ============================================================
DROP FUNCTION IF EXISTS sla_prazo_projeto(timestamptz);
DROP FUNCTION IF EXISTS sla_projetos_um_mes();

-- ============================================================
-- 9. Hardening (mesmo padrão das 0005/0017/0064/0065): nada disso é RPC.
-- ============================================================
REVOKE EXECUTE ON FUNCTION sla_prazo_projeto(timestamptz, integer) FROM public, anon, authenticated;
REVOKE EXECUTE ON FUNCTION sla_projeto_dias_padrao(uuid)           FROM public, anon, authenticated;
REVOKE EXECUTE ON FUNCTION sla_projetos_prazo()                    FROM public, anon, authenticated;
REVOKE EXECUTE ON FUNCTION calcular_sla_chamado()                  FROM public, anon, authenticated;
REVOKE EXECUTE ON FUNCTION enforce_cliente_so_avaliacao()          FROM public, anon, authenticated;

COMMIT;
