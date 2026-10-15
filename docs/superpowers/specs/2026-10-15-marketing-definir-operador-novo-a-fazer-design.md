# Especificação de Design — Atribuição de Operador em Chamados "Novo" e "A Fazer" no Marketing

- **Data:** 2026-10-15
- **Setor:** Marketing
- **Contexto:** Portal de Chamados Bondmann Química

---

## 1. Visão Geral e Objetivos

No setor de Marketing, o fluxo operacional funciona no modelo de demandas e quadro Kanban contínuo (autoatendimento ativo via `departamentos.autoatendimento = true`), onde as demandas chegam na coluna "Novo" e são organizadas no backlog em "A fazer" antes de entrarem efetivamente em execução ("Em andamento").

Hoje, a plataforma só disponibiliza o campo de atribuição de operador após o chamado ter sido formalmente assumido através do botão "Iniciar atendimento". Isso impede a coordenação e a equipe de pré-atribuir um designer/responsável a uma demanda enquanto ela ainda está no backlog ("Novo" ou "A fazer"). Além disso, o botão "Iniciar atendimento" sempre sobrescreve o operador para quem clicou nele.

O objetivo desta especificação é permitir que o staff do Marketing defina o operador responsável para chamados com status `NOVO` e `A_FAZER`, sem a obrigatoriedade de iniciar o atendimento naquele momento, e garantindo que, quando o atendimento for iniciado, o operador pré-definido seja preservado.

### Critérios de Aceite:
1. **Atribuição no Backlog do Marketing:** Para chamados do setor de Marketing nos status `NOVO` e `A_FAZER`, o campo de seleção de operador responsável deve estar visível e funcional para o staff do setor, mesmo que o chamado ainda não tenha sido assumido.
2. **Preservação de Operador em `iniciar_atendimento`:** Ao acionar o botão "Iniciar atendimento" (ou arrastar o card no Kanban para colunas ativas), se o chamado já tiver um operador pré-definido, esse operador deve ser mantido e o status transicionado para `EM_ATENDIMENTO`. Se o chamado não tiver operador definido, quem acionou assume como responsável.
3. **Persistência do Botão "Iniciar Atendimento":** No Marketing, enquanto o chamado estiver em `NOVO` ou `A_FAZER`, o botão "Iniciar atendimento" deve permanecer acessível para permitir o início formal da demanda, mesmo quando o operador já estiver selecionado.
4. **Desbloqueio de Chat/Notas:** O envio de mensagens e notas internas deve ser liberado assim que o chamado tiver um operador atribuído, mesmo que o status continue em `NOVO` ou `A_FAZER`.
5. **Isolamento de Setores:** Os demais departamentos (TI, RH, Manutenção, etc.) devem manter seu fluxo e regras inalterados (reivindicação estrita na assunção).

---

## 2. Arquitetura e Regras de Permissão

### 2.1 Serviço de Atendimento (`app/services/atendimento.py`)

No dataclass `PermissoesAtendimento`, introduzir as regras refinadas de atribuição e início:

```python
@dataclass(frozen=True)
class PermissoesAtendimento:
    dept_bate: bool
    eh_autor: bool
    eh_autoatendimento: bool
    bloqueado_por_autoria: bool
    pode_reivindicar: bool
    pode_atender: bool
    pode_atribuir: bool
    pode_iniciar: bool
```

**Regras calculadas:**
1. `eh_marketing = (chamado.get("departamento") or "").strip().lower() == "marketing"`
2. `ja_assumido = chamado.get("operador_id") is not None`
3. `pode_atender = dept_bate and not bloqueado_por_autoria and ja_assumido`
4. `pode_atribuir`:
   - Se `eh_marketing`: `pode_atribuir = dept_bate and not bloqueado_por_autoria` (permite atribuir operador a qualquer momento no Marketing).
   - Demais setores: `pode_atribuir = pode_atender` (mantém o comportamento atual).
5. `pode_iniciar`:
   - Se `eh_marketing`: `pode_iniciar = dept_bate and not bloqueado_por_autoria and (chamado.get("status") in ("NOVO", "A_FAZER"))`
   - Demais setores: `pode_iniciar = pode_reivindicar` (`dept_bate and not bloqueado_por_autoria and not ja_assumido`).

---

## 3. Persistência e Repositório

### 3.1 Repositório de Atendimento (`app/repositories/atendimento.py`)

Ajustar `AtendimentoRepo.iniciar_atendimento` para utilizar `COALESCE`:

```sql
UPDATE chamados
   SET status = $4::status_chamado,
       operador_id = COALESCE(operador_id, $2::uuid)
 WHERE id = $1::uuid
   AND status IN ('NOVO'::status_chamado, 'A_FAZER'::status_chamado)
   AND ($3::boolean OR cliente_id <> $2::uuid)
RETURNING id, status, operador_id;
```

E no registro de auditoria:
```python
await self._registrar(
    conn, chamado_id, claims["sub"], "ATENDIMENTO_INICIADO",
    {"operador_id": str(row["operador_id"]) if row["operador_id"] else operador_id},
)
```

Comportamento:
- Se `operador_id` já estiver preenchido, `COALESCE(operador_id, $2::uuid)` preserva o valor atual.
- Se `operador_id` for NULL, atribui `$2::uuid` (o usuário logado).
- O status sempre transiciona para `$4::status_chamado` (default: `EM_ATENDIMENTO`).

### 3.2 Rota de Atribuição (`POST /workspace/chamados/{id}/atribuir`)

- A rota e o método `repo.atribuir` já suportam atualização de `operador_id` sem alterar status e gravando histórico `ATRIBUIDO`.
- A RLS `chamados_update_staff` já autoriza o update de `operador_id` para membros do mesmo departamento.

---

## 4. Interface e Templates

### 4.1 Detalhes do Chamado (`app/templates/workspace/atendimento.html`)

1. **Botão de Início:**
   - Trocar a condição `{% if pode_reivindicar %}` por `{% if pode_iniciar %}`.
   - No Marketing, o botão permanece visível enquanto o chamado estiver em `NOVO` ou `A_FAZER`, independentemente de já possuir operador.

2. **Card de Ações (Sidebar):**
   - Modificar a exibição do card de Ações para `{% if pode_atender or pode_atribuir %}`.
   - O campo `Operador responsável` (`<select name="operador_id" data-autosubmit>`) torna-se visível e editável para o staff do Marketing mesmo com chamado não assumido em `NOVO` ou `A_FAZER`.

3. **Chat / Composer:**
   - Sem operador: exibe aviso de que a demanda ainda não tem operador assumido.
   - Com operador atribuído (mesmo em `NOVO` ou `A_FAZER`): libera o composer de mensagens e notas internas.

---

## 5. Estratégia de Testes

1. **Testes Unitários (`tests/test_atendimento.py`):**
   - Validar retorno de `AtendimentoService.permissoes` para Marketing com chamado em `NOVO` e `A_FAZER`:
     - Sem operador: `pode_atribuir = True`, `pode_iniciar = True`, `pode_atender = False`.
     - Com operador: `pode_atribuir = True`, `pode_iniciar = True`, `pode_atender = True`.
     - Em `EM_ATENDIMENTO`: `pode_iniciar = False`, `pode_atender = True`.
   - Validar retorno para outros setores (TI/RH), garantindo que `pode_atribuir` permaneça estritamente amarrado a `pode_atender`.
2. **Testes de Integração / Repositório (`tests/test_atendimento.py`):**
   - Testar `iniciar_atendimento` quando o chamado já possui operador: validar que o operador não é sobrescrito e o status muda para `EM_ATENDIMENTO`.
   - Testar `iniciar_atendimento` quando o chamado não possui operador: validar que o operador que iniciou é gravado.
3. **Testes de Rota e Visualização (`tests/test_workspace.py`):**
   - Testar que a página `/workspace/chamados/{id}` exibe o formulário de atribuição para chamados de Marketing em `NOVO`/`A_FAZER`.
