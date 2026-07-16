# ADR-0004 — SLA em horário comercial (não horas corridas)

**Status:** Aceito · **Data:** 2026-07-05 · **Ref.:** plano mestre Seção 5.2, migration `0017_sla_horario_comercial`

## Contexto

A decisão original (contradição C1 do plano mestre) calculava o prazo de SLA
em horas corridas (ex.: URGENTE = 4h corridas a partir da abertura). Isso
penalizava chamados abertos fora do expediente ou perto de um fim de
semana/feriado — o relógio corria igual, mesmo sem ninguém disponível para
atender.

## Decisão

**SLA passa a rodar em horário comercial:** seg–sex 08:00–18:00
(America/Sao_Paulo), pausado em feriados (tabela `feriados`, seed nacional
2026–2028) e enquanto o chamado está `AGUARDANDO`/`AGUARDANDO_TERCEIROS`
(0043/0044). Substitui integralmente as horas corridas da decisão C1
original. Implementado via funções `sla_minutos_uteis_entre`/
`sla_add_minutos_uteis` (SECURITY DEFINER) e a trigger
`sla_pausa_aguardando`.

## Consequências

- `calcular_sla_chamado` (trigger) foi recompilada para minutos úteis — todo
  cálculo de prazo passa por essa função, não há lógica de SLA duplicada em
  Python.
- `app/domain/sla_visual.py` (a barra de progresso visual) continua em tempo
  de parede como aproximação — a cor/atraso por prazo absoluto é correta
  (lê `limite_resolucao` já ajustado pela trigger), só o preenchimento da
  barra não modela a pausa em tempo real. Documentado como aproximação
  aceitável, não um bug.
- Qualquer novo status que deva pausar o SLA (como `AGUARDANDO_TERCEIROS` foi
  depois) precisa entrar na trigger `sla_pausa_aguardando`, não numa lógica
  separada.
