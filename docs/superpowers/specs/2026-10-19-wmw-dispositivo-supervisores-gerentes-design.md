# Especificação de Design: Dispositivo WMW para Supervisores e Gerentes Comerciais

- **Data**: 2026-10-19
- **Status**: Aprovado
- **Autor**: Antigravity AI / Equipe Bondmann
- **Arquivo de Origem**: `app/domain/formularios_acessos.py`

---

## 1. Contexto e Motivação

Na versão v1.5.0 do sistema de automação (`Automação/`), a criação de usuários no aplicativo **WMW Vendas** foi expandida para incluir os perfis comerciais de liderança: **Supervisor** e **Gerente de Vendas**. Nesses perfis, o login no WMW é provisionado automaticamente através do código do Parceiro de Negócio (PN Fornecedor no SAP) gerado/identificado para o colaborador, acompanhado da configuração do aplicativo de smartphone e das credenciais de acesso.

No **Portal de Chamados**, o formulário dinâmico da subcategoria "Criação de Novo Usuário" mantinha o campo `dispositivo_wmw` ("Dispositivo do app WMW") configurado com visibilidade condicional exclusiva para `PERFIL_REPRESENTANTE` (`visivel_se=_SO_REPRESENTANTE`). Com isso, ao selecionar "Supervisor / Liderança de Equipe" ou "Gerente de Vendas", o solicitante (RH ou TI) não podia informar o dispositivo desejado (iOS, Android ou Simulador), fazendo com que o payload da automação ficasse sem o campo e o worker precisasse assumir valores padrão ou exigir intervenção.

O objetivo desta especificação é unificar a exibição e obrigatoriedade do campo `dispositivo_wmw` para todos os perfis comerciais que usam o WMW (Representante, Supervisor e Gerente), e alinhar o plano mestre do projeto (`plano_md_mestre_automacao_acessos_v2.md`).

---

## 2. Requisitos e Regras de Negócio

1. **Formulário de Criação ("Criação de Novo Usuário")**:
   - O campo `dispositivo_wmw` deve ser visível e obrigatório para:
     - `Representante Comercial (Externo)`
     - `Supervisor / Liderança de Equipe`
     - `Gerente de Vendas`
   - O campo `regiao_wmw` continua visível **apenas** para `Representante Comercial (Externo)` (Supervisores selecionam `equipe` e Gerentes selecionam `gerencia`, sendo o acesso ao WMW mapeado pelo PN SAP correspondente).
   - Para `Colaborador Interno (Escritório/Fábrica)`, o campo `dispositivo_wmw` permanece oculto e não aplicável.
2. **Formulário de Desligamento**:
   - Permanece inalterado, sem seleção de dispositivo.
3. **Payload da Fila (`montar_payload`)**:
   - `montar_payload` já extrai e normaliza `dispositivo_wmw` via `_primeira_palavra_em(_DISPOSITIVO_ENUM, dados.get("dispositivo_wmw"))`. Quando preenchido nos chamados de Supervisor e Gerente, o valor (`IOS`, `ANDROID` ou `SIMULADOR`) será incluído automaticamente no payload do job enviado ao worker.
4. **Documentação Mestre**:
   - Atualizar a premissa **V9** de `plano_md_mestre_automacao_acessos_v2.md` para documentar que a criação no WMW para liderança foi integrada na v1.5.0 e refletida no portal.
   - Atualizar a tabela de campos do formulário na Seção 4 do plano mestre.

---

## 3. Arquitetura e Componentes Modificados

### 3.1 `app/domain/formularios_acessos.py`
- Adicionar tupla de controle de visibilidade condicional:
  ```python
  _SO_COMERCIAIS_WMW = ("perfil", (PERFIL_REPRESENTANTE, PERFIL_SUPERVISOR, PERFIL_GERENTE))
  ```
- Atualizar a definição do campo `dispositivo_wmw` em `campos_criacao`:
  ```python
  CampoDef(
      "dispositivo_wmw", "Dispositivo do app WMW", "select", obrigatorio=True,
      opcoes=_DISPOSITIVOS_WMW, visivel_se=_SO_COMERCIAIS_WMW,
  ),
  ```

### 3.2 Comportamento no Front-End e Validação
- O template parcial `app/templates/portal/_campos_dinamicos.html` renderiza `data-visivel-se-valores` unindo a tupla com `"||"`.
- O client-side `app/static/js/novo_chamado.js` divide por `"||"` e exibe o bloco quando o valor do select `campo__perfil` for qualquer um dos três perfis comerciais.
- O motor de validação `app/domain/campos_dinamicos.py` valida o campo como obrigatório sempre que o perfil selecionado estiver na tupla `_SO_COMERCIAIS_WMW`.

### 3.3 Documentação Mestre (`plano_md_mestre_automacao_acessos_v2.md`)
- Atualizar a premissa V9 para explicitar o suporte a WMW para Supervisor e Gerente.
- Atualizar o resumo dos campos de formulário.

---

## 4. Plano de Testes

1. **Testes de Validação Dinâmica (`tests/test_formularios_dinamicos.py`)**:
   - `test_criacao_supervisor_pede_equipe_e_nao_regiao`: Atualizar fixture para fornecer `dispositivo_wmw` válido (`IOS (iPhone / iPad)`).
   - `test_criacao_gerente_pede_gerencia`: Atualizar fixture para fornecer `dispositivo_wmw` válido.
   - Adicionar novo teste `test_criacao_supervisor_e_gerente_exigem_dispositivo_wmw`:
     - Testar que omitir ou enviar vazio `dispositivo_wmw` para Supervisor e Gerente resulta em falha de validação (`ok is False`).
     - Testar que para Colaborador Interno o campo não é exigido nem gravado.
2. **Testes de Payload (`tests/test_automacao.py`)**:
   - Garantir que `montar_payload` para criação de Supervisor e Gerente contém `dispositivo_wmw` com o valor esperado (`IOS`, `ANDROID` ou `SIMULADOR`).
3. **Execução da Suíte Completa de Testes**:
   - Executar `pytest tests/test_formularios_dinamicos.py tests/test_automacao.py` e garantir 100% de aprovação.
