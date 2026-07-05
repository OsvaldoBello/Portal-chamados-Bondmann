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

-- Reverter para funcionário comum (CLIENTE, sem departamento):
-- UPDATE perfis SET role = 'CLIENTE', departamento_id = NULL
--  WHERE id = (SELECT id FROM auth.users WHERE email = '...');
-- UPDATE auth.users
--    SET raw_app_meta_data =
--        COALESCE(raw_app_meta_data, '{}'::jsonb) || jsonb_build_object('role', 'CLIENTE')
--  WHERE email = '...';

-- Adicionar um novo departamento (acesso total / TI também pode fazer pela UI futura):
-- INSERT INTO departamentos (nome) VALUES ('Financeiro');
