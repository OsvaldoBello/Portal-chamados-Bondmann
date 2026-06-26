-- Fase 1 / Seção 5.1 — Enums do schema canônico
-- papel_usuario: papéis de acesso (Seção 3.2)
CREATE TYPE papel_usuario AS ENUM ('ADMIN', 'OPERADOR', 'CLIENTE');

-- prioridade_chamado: URGENTE derivado (50% de ALTA) no calcular_sla_chamado
CREATE TYPE prioridade_chamado AS ENUM ('BAIXA', 'MEDIA', 'ALTA', 'URGENTE');

-- status_chamado: badges da Seção 5.1 da spec
CREATE TYPE status_chamado AS ENUM ('NOVO', 'EM_ATENDIMENTO', 'AGUARDANDO', 'RESOLVIDO');
