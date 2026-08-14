-- 0083_mfa_email_otp.sql
-- MFA por e-mail (alternativa ao TOTP): código de 6 dígitos, guardado só como
-- hash. GoTrue não tem fator "email" nativo (só totp/phone/webauthn), então a
-- verificação é toda nossa — ver app/auth/mfa_email.py.
--
-- RLS ligada sem policy (mesmo padrão de `contador_chamados` na 0004): só o
-- backend, via admin_connection(), acessa esta tabela.
--
-- Idempotente (reexecução não falha).

BEGIN;

CREATE TABLE IF NOT EXISTS mfa_email_codes (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id     uuid NOT NULL REFERENCES perfis(id) ON DELETE CASCADE,
  codigo_hash text NOT NULL,
  tentativas  integer NOT NULL DEFAULT 0,
  expira_em   timestamptz NOT NULL,
  created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_mfa_email_codes_user ON mfa_email_codes(user_id);

ALTER TABLE mfa_email_codes ENABLE ROW LEVEL SECURITY;
-- sem policy: só admin_connection() (backend) acessa

COMMIT;
