-- registro_usuarios.sql  (REFERÊNCIA — NÃO é migration; não roda no deploy)
-- ---------------------------------------------------------------------------
-- Cadastro de colaboradores é feito DIRETO no Supabase (não há signup público).
-- Fluxo:
--   1) Criar o usuário em Authentication > Users (Add user) no painel do Supabase,
--      ou via Admin API. O trigger `handle_new_user` cria automaticamente um
--      `perfis` com papel CLIENTE, vinculado à org interna (Bondmann).
--   2) Se o colaborador for STAFF (responde chamados), promover o papel e definir
--      o DEPARTAMENTO com os comandos abaixo (rodar no SQL Editor como serviço).
--
-- Papéis: CLIENTE (funcionário que abre chamado) · OPERADOR/ADMIN (respondem).
-- Departamentos: TI, RH, Marketing (tabela `departamentos`, gerenciável).
-- Regra de acesso (migration 0010):
--   * TI            -> ACESSO TOTAL (vê/atende todos os chamados; gere catálogos;
--                      painel /admin de TODOS os setores).
--   * RH / Marketing -> veem os chamados do SEU setor + os que ELES abriram;
--                       podem abrir chamados para QUALQUER departamento.
--   * Funcionário    -> cria chamados e vê APENAS os que criou (CLIENTE, sem depto).
-- "Staff" = role OPERADOR/ADMIN + um departamento. Acesso total = staff em 'TI'.
--
-- NÍVEIS DENTRO DE UM DEPARTAMENTO (Fase 4 — papéis por setor):
--   * OPERADOR  -> atende os chamados do setor (fila/workspace). NÃO vê o painel
--                  de indicadores (/admin).
--   * ADMIN     -> gestor do setor: além de atender, acessa o painel /admin
--                  ESCOPADO ao seu departamento (CSAT, conformidade de SLA,
--                  rapidez de resposta, produtividade e as avaliações dadas pelos
--                  funcionários aos chamados do setor). NÃO gere catálogos (isso é
--                  do TI). Ex.: um "Admin do RH" vê os resultados só do RH.
--   A RLS escopa as queries do painel ao departamento automaticamente; a única
--   diferença entre OPERADOR e ADMIN é o acesso à tela de relatórios.
--
-- ⚠️ IMPORTANTE — DUAS FONTES DE VERDADE DE AUTORIZAÇÃO (auditoria Etapa 3):
--   * A RLS do banco lê o papel/depto da tabela `perfis` (auth_role/auth_is_ti).
--   * O gate da APLICAÇÃO (app/auth/dependencies.py) lê o papel de
--     `app_metadata.role`, que o Supabase injeta no JWT a partir de
--     `auth.users.raw_app_meta_data`.
--   Por isso a promoção DEVE escrever nos DOIS lugares (perfis + raw_app_meta_data),
--   senão o staff é promovido no banco mas o app continua tratando-o como CLIENTE.
--   O JWT só reflete a mudança no PRÓXIMO login/refresh — peça re-login ao usuário.
-- ---------------------------------------------------------------------------

-- Colaborador que responde chamados de RH (staff do setor de RH):
UPDATE perfis
   SET role = 'OPERADOR',
       departamento_id = (SELECT id FROM departamentos WHERE nome = 'RH')
 WHERE id = (SELECT id FROM auth.users WHERE email = 'colaborador.rh@bondmann.com.br');
UPDATE auth.users
   SET raw_app_meta_data =
       COALESCE(raw_app_meta_data, '{}'::jsonb) || jsonb_build_object('role', 'OPERADOR')
 WHERE email = 'colaborador.rh@bondmann.com.br';

-- Colaborador que responde chamados de Marketing:
UPDATE perfis
   SET role = 'OPERADOR',
       departamento_id = (SELECT id FROM departamentos WHERE nome = 'Marketing')
 WHERE id = (SELECT id FROM auth.users WHERE email = 'colaborador.mkt@bondmann.com.br');
UPDATE auth.users
   SET raw_app_meta_data =
       COALESCE(raw_app_meta_data, '{}'::jsonb) || jsonb_build_object('role', 'OPERADOR')
 WHERE email = 'colaborador.mkt@bondmann.com.br';

-- GESTOR do RH (Admin do setor) — atende E vê os relatórios do RH (/admin):
UPDATE perfis
   SET role = 'ADMIN',
       departamento_id = (SELECT id FROM departamentos WHERE nome = 'RH')
 WHERE id = (SELECT id FROM auth.users WHERE email = 'gestor.rh@bondmann.com.br');
UPDATE auth.users
   SET raw_app_meta_data =
       COALESCE(raw_app_meta_data, '{}'::jsonb) || jsonb_build_object('role', 'ADMIN')
 WHERE email = 'gestor.rh@bondmann.com.br';

-- ACESSO TOTAL ao sistema = colaborador do departamento TI:
UPDATE perfis
   SET role = 'ADMIN',
       departamento_id = (SELECT id FROM departamentos WHERE nome = 'TI')
 WHERE id = (SELECT id FROM auth.users WHERE email = 'ti@bondmann.com.br');
UPDATE auth.users
   SET raw_app_meta_data =
       COALESCE(raw_app_meta_data, '{}'::jsonb) || jsonb_build_object('role', 'ADMIN')
 WHERE email = 'ti@bondmann.com.br';

-- ---------------------------------------------------------------------------
-- PERFIL DE SERVIÇO "Assistente IA" (frente de IA de triagem — F0, decisão C4
-- do plano_md_mestre_IA.md). `perfis.id` tem FK para `auth.users(id)`, então o
-- perfil exige um usuário real no Supabase Auth:
--   1) Criar em Authentication > Users (Add user) — ou via Admin API — com:
--        e-mail: assistente-ia@bondmann.internal
--        senha:  aleatória e DESCARTADA (ninguém jamais loga com este usuário;
--                pode marcar "Auto Confirm"). Ex.: `openssl rand -hex 24`.
--      O trigger `handle_new_user` cria o `perfis` como CLIENTE.
--   2) Promover com o SQL abaixo (SQL Editor). role=OPERADOR permite que a RLS
--      trate as mensagens dele como staff; departamento_id=NULL — ele "atende"
--      qualquer departamento com IA ativa, e NÃO aparece nas filas de nenhum.
--      (O guarda-corpo `enforce_departamento_recebe_chamados` só vale para
--      chamados.departamento_id, não para perfis — NULL é aceito aqui.)
--
-- O app NUNCA autentica com esse usuário: as escritas da IA (mensagens de
-- triagem, ia_triagens) usam admin_connection() com remetente_id apontando
-- para este perfil. O UUID é resolvido por lookup de nome ("Assistente IA")
-- com cache em memória — sem env var, sem hardcode (Seção 4.2 do plano IA).
--
-- >>> BLOCO PRONTO — copiar e rodar INTEIRO no SQL Editor (uma única vez).
--     Alternativa ao passo 1 acima: cria o usuário direto por SQL (mesmo
--     padrão validado na suíte e2e), sem passar pelo painel. Idempotente:
--     reexecutar não duplica (o INSERT é pulado se o e-mail já existe).
BEGIN;

INSERT INTO auth.users (
  instance_id, id, aud, role, email, encrypted_password,
  email_confirmed_at, created_at, updated_at,
  raw_app_meta_data, raw_user_meta_data,
  confirmation_token, recovery_token, email_change_token_new, email_change
)
SELECT
  '00000000-0000-0000-0000-000000000000', gen_random_uuid(), 'authenticated', 'authenticated',
  'assistente-ia@bondmann.internal',
  crypt(encode(gen_random_bytes(32), 'hex'), gen_salt('bf')),  -- senha aleatória, descartada
  now(), now(), now(),
  jsonb_build_object('provider', 'email', 'providers', ARRAY['email'], 'role', 'OPERADOR'),
  jsonb_build_object('nome', 'Assistente IA'),
  '', '', '', ''
WHERE NOT EXISTS (
  SELECT 1 FROM auth.users WHERE email = 'assistente-ia@bondmann.internal'
);

-- Promoção: o trigger perfis_self_so_avatar (0033) bloqueia UPDATE de
-- nome/role sem auth_is_ti(); desabilitado SÓ dentro desta transação.
ALTER TABLE perfis DISABLE TRIGGER perfis_self_so_avatar;
UPDATE perfis
   SET nome = 'Assistente IA',
       role = 'OPERADOR',
       departamento_id = NULL
 WHERE id = (SELECT id FROM auth.users WHERE email = 'assistente-ia@bondmann.internal');
ALTER TABLE perfis ENABLE TRIGGER perfis_self_so_avatar;

UPDATE auth.users
   SET raw_app_meta_data =
       COALESCE(raw_app_meta_data, '{}'::jsonb) || jsonb_build_object('role', 'OPERADOR')
 WHERE email = 'assistente-ia@bondmann.internal';

COMMIT;

-- Conferência (deve devolver 1 linha: Assistente IA · OPERADOR · depto NULL):
-- SELECT p.nome, p.role, p.departamento_id FROM perfis p WHERE p.nome = 'Assistente IA';

-- Reverter para funcionário comum (CLIENTE, sem departamento):
-- UPDATE perfis SET role = 'CLIENTE', departamento_id = NULL
--  WHERE id = (SELECT id FROM auth.users WHERE email = '...');
-- UPDATE auth.users
--    SET raw_app_meta_data =
--        COALESCE(raw_app_meta_data, '{}'::jsonb) || jsonb_build_object('role', 'CLIENTE')
--  WHERE email = '...';

-- Adicionar um novo departamento (acesso total / TI também pode fazer pela UI futura):
-- INSERT INTO departamentos (nome) VALUES ('Financeiro');

-- ---------------------------------------------------------------------------
-- Auditoria de reconciliação (Sprint 1 / item 1.5, M12 — opcional, rodar
-- periodicamente): a rota /admin/usuarios/{id}/papel já relê e alerta na hora
-- de cada promoção, mas essa query pega qualquer divergência acumulada por
-- outra via (ex.: UPDATE manual direto no SQL Editor, migration antiga,
-- alteração fora da rota). Divergência = perfis.role ≠ app_metadata.role.
SELECT u.email,
       p.role AS role_perfis,
       u.raw_app_meta_data ->> 'role' AS role_jwt
  FROM perfis p
  JOIN auth.users u ON u.id = p.id
 WHERE p.role::text IS DISTINCT FROM (u.raw_app_meta_data ->> 'role');
