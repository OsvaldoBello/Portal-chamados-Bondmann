-- Migration 0098: Tabela de cache durável de regiões comerciais SAP
-- Permite ao portal exibir a região comercial do autor diretamente pelo banco em <5ms,
-- sem depender de túnel Tailscale no container web de produção.

CREATE TABLE IF NOT EXISTS public.sap_regioes_autores (
    email TEXT PRIMARY KEY,
    card_code TEXT NOT NULL,
    card_name TEXT,
    tipo TEXT NOT NULL CHECK (tipo IN ('representante', 'supervisor', 'gerente')),
    rotulo TEXT NOT NULL,
    regioes TEXT[] NOT NULL DEFAULT '{}',
    detalhe TEXT NOT NULL DEFAULT '',
    eh_multiplo BOOLEAN NOT NULL DEFAULT false,
    atualizado_em TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_sap_regioes_autores_card_code
    ON public.sap_regioes_autores(card_code);

ALTER TABLE public.sap_regioes_autores ENABLE ROW LEVEL SECURITY;

-- Leitura: qualquer usuário autenticado (atendente ou solicitante)
CREATE POLICY "sap_regioes_autores_select"
    ON public.sap_regioes_autores
    FOR SELECT
    TO authenticated
    USING (true);

-- Escrita: apenas ADMIN ou tarefas administrativas internas
CREATE POLICY "sap_regioes_autores_admin_write"
    ON public.sap_regioes_autores
    FOR ALL
    TO authenticated
    USING (public.auth_role() = 'ADMIN')
    WITH CHECK (public.auth_role() = 'ADMIN');

COMMENT ON TABLE public.sap_regioes_autores IS
    'Espelho sincronizado de regiões comerciais (IB_CO_REGIAO / OCRD) do SAP Business One para exibição imediata no Portal.';
