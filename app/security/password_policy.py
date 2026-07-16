"""Política de senha (Sprint 2 / item 2.8, B6).

Fonte única do mínimo de caracteres — antes duplicada como uma constante `8`
independente em `app/auth/routes.py` (redefinição por OTP) e
`app/routes/admin.py` (criação de conta por TI), a mesma classe de duplicação
que já causou drift noutros pontos do projeto (ver `AtendimentoService`,
`PortalService`).

**Hashing** da senha em si é delegado ao GoTrue (Supabase Auth) — bcrypt
gerenciado pela plataforma, sem parâmetro exposto para ajustar aqui; não há
decisão de código a tomar além de garantir que o parâmetro do projeto
hospedado (mínimo de caracteres, no painel Supabase) acompanhe este valor —
ver Seção 3.4.1 do plano mestre.

**DECISÃO (2026-07-16):** mínimo de 8 caracteres, sem exigência de
complexidade (maiúscula/símbolo/dígito obrigatórios) — segue NIST 800-63B,
que recomenda comprimento em vez de regras de composição arbitrárias
(empiricamente levam a padrões previsíveis tipo "Senha123!").
"""

from __future__ import annotations

SENHA_MIN_CHARS = 8
