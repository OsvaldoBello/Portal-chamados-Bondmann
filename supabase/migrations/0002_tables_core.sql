-- Fase 1 / Seção 5.1 — 7 tabelas do schema canônico + índices
-- Ordem: planos_sla -> empresas -> perfis -> categorias -> chamados -> mensagens -> historico_chamados

-- 1. planos_sla
CREATE TABLE planos_sla (
  id                     uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  nome                   text NOT NULL,                 -- Bronze, Ouro, etc.
  -- tempos em MINUTOS, por prioridade (ALTA é base; URGENTE = 50% de ALTA, não armazenado)
  resposta_baixa_min     integer,
  resposta_media_min     integer,
  resposta_alta_min      integer,
  resolucao_baixa_min    integer,
  resolucao_media_min    integer,
  resolucao_alta_min     integer,
  -- defaults do plano (escada de fallback, contradição C1, passo 3)
  resposta_default_min   integer,
  resolucao_default_min  integer,
  ativo                  boolean NOT NULL DEFAULT true,
  created_at             timestamptz NOT NULL DEFAULT now(),
  updated_at             timestamptz NOT NULL DEFAULT now()
);

-- 2. empresas
CREATE TABLE empresas (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  nome_fantasia   text NOT NULL,
  cnpj            text UNIQUE,
  plano_sla_id    uuid REFERENCES planos_sla(id) ON DELETE RESTRICT,
  ativo           boolean NOT NULL DEFAULT true,
  created_at      timestamptz NOT NULL DEFAULT now(),
  updated_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_empresas_plano ON empresas(plano_sla_id);

-- 3. perfis (extensão de auth.users)
CREATE TABLE perfis (
  id          uuid PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
  nome        text,
  role        papel_usuario NOT NULL DEFAULT 'CLIENTE',
  empresa_id  uuid REFERENCES empresas(id) ON DELETE RESTRICT,
  ativo       boolean NOT NULL DEFAULT true,
  created_at  timestamptz NOT NULL DEFAULT now(),
  updated_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_perfis_empresa ON perfis(empresa_id);
CREATE INDEX idx_perfis_role ON perfis(role);

-- 4. categorias (catálogo global gerido por ADMIN)
CREATE TABLE categorias (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  nome        text NOT NULL,
  descricao   text,
  ativo       boolean NOT NULL DEFAULT true,
  created_at  timestamptz NOT NULL DEFAULT now()
);

-- 5. chamados (entidade central)
CREATE TABLE chamados (
  id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  codigo           text UNIQUE NOT NULL,            -- BOND-YYYY-NNNNN (gerar_codigo_chamado)
  empresa_id       uuid NOT NULL REFERENCES empresas(id) ON DELETE RESTRICT,
  cliente_id       uuid NOT NULL REFERENCES perfis(id) ON DELETE RESTRICT,
  operador_id      uuid REFERENCES perfis(id) ON DELETE SET NULL,
  categoria_id     uuid REFERENCES categorias(id) ON DELETE RESTRICT,
  titulo           text NOT NULL,
  descricao        text NOT NULL,
  status           status_chamado NOT NULL DEFAULT 'NOVO',
  prioridade       prioridade_chamado NOT NULL DEFAULT 'MEDIA',
  limite_resposta  timestamptz,
  limite_resolucao timestamptz,
  respondido_em    timestamptz,
  resolvido_em     timestamptz,
  created_at       timestamptz NOT NULL DEFAULT now(),
  updated_at       timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_chamados_empresa        ON chamados(empresa_id);
CREATE INDEX idx_chamados_status         ON chamados(status);
CREATE INDEX idx_chamados_operador       ON chamados(operador_id);
CREATE INDEX idx_chamados_prioridade     ON chamados(prioridade);
CREATE INDEX idx_chamados_limite_resol   ON chamados(limite_resolucao);
CREATE INDEX idx_chamados_empresa_status ON chamados(empresa_id, status);

-- 6. mensagens
CREATE TABLE mensagens (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  chamado_id    uuid NOT NULL REFERENCES chamados(id) ON DELETE CASCADE,
  remetente_id  uuid NOT NULL REFERENCES perfis(id) ON DELETE RESTRICT,
  conteudo      text NOT NULL,
  is_interna    boolean NOT NULL DEFAULT false,      -- true = invisível ao CLIENTE
  anexos        jsonb NOT NULL DEFAULT '[]'::jsonb,  -- [{path, nome, mime, tamanho}] no bucket privado
  created_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_mensagens_chamado ON mensagens(chamado_id, created_at);
CREATE INDEX idx_mensagens_interna ON mensagens(chamado_id) WHERE is_interna = false;

-- 7. historico_chamados (auditoria imutável)
CREATE TABLE historico_chamados (
  id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  chamado_id  uuid NOT NULL REFERENCES chamados(id) ON DELETE CASCADE,
  ator_id     uuid REFERENCES perfis(id) ON DELETE SET NULL,
  acao        text NOT NULL,
  detalhes    jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_historico_chamado ON historico_chamados(chamado_id, created_at);
