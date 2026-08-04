"""Janela de período (filtros "de/até") ancorada no fuso de Brasília.

Por que existe (2026-08-04): os filtros de período do Kanban e de "Meus
chamados" comparavam `created_at` (`timestamptz`) direto com `::date`. O
Postgres resolve `timestamptz >= date` convertendo a data para timestamptz **no
fuso da sessão**, que em produção é **UTC** (conferido: `current_setting
('TimeZone') = 'UTC'`) — enquanto a tela mostra tudo em **America/Sao_Paulo**
(`app/templating.py::fmt_dt`). Resultado: as bordas do período ficavam 3h
deslocadas em relação ao que o usuário lê no cartão. Um chamado aberto às 21h30
de 03/08 (BRT) é gravado como `2026-08-04T00:30Z`; o cartão diz "Solicitado em
03/08", mas o filtro "de 03/08 até 03/08" o descartava — e o mesmo chamado
aparecia no filtro do dia 04. Hoje isso atinge 0 das 632 linhas de produção
(ninguém abriu chamado depois das 21h ainda), o que é exatamente o tipo de bug
que só aparece no dia em que alguém trabalha até tarde.

A conversão é feita **em Python** (e não com `AT TIME ZONE` no SQL) por dois
motivos: o predicado continua sendo um range simples sobre a coluna
(`created_at >= $x AND created_at < $y`), que o índice consegue usar — uma
função sobre a coluna descartaria o índice —, e a regra fica pura e testável
sem banco, como `sla_visual`/`feriados`.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

# Mesmo fuso de exibição usado em app/templating.py e app/services/portal.py.
TZ_BR = ZoneInfo("America/Sao_Paulo")


def janela_utc(
    data_de: date | None, data_ate: date | None
) -> tuple[datetime | None, datetime | None]:
    """Converte o período informado pelo usuário (datas no fuso de Brasília) no
    intervalo **semiaberto** ``[inicio, fim)`` em UTC para comparar com
    `timestamptz`.

    - ``data_de`` → 00:00:00 daquele dia em Brasília, convertido para UTC.
    - ``data_ate`` → 00:00:00 do **dia seguinte** em Brasília (fim exclusivo),
      de modo que o dia informado entra inteiro, incluindo 23:59.
    - Qualquer ponta ausente vira ``None`` (sem limite daquele lado).

    O deslocamento é calculado por data (e não com um offset fixo de -3h) para
    que o horário de verão, se voltar a existir no Brasil, seja respeitado
    automaticamente pelo tzdata.
    """
    inicio = (
        datetime.combine(data_de, time.min, tzinfo=TZ_BR).astimezone(UTC)
        if data_de is not None
        else None
    )
    fim = (
        datetime.combine(data_ate + timedelta(days=1), time.min, tzinfo=TZ_BR).astimezone(UTC)
        if data_ate is not None
        else None
    )
    return inicio, fim


def periodo_invertido(data_de: date | None, data_ate: date | None) -> bool:
    """``True`` quando o usuário informou um período impossível (início depois
    do fim). Vale um aviso na tela: sem ele o quadro simplesmente fica vazio, o
    que é indistinguível de "não há chamados no período"."""
    return data_de is not None and data_ate is not None and data_de > data_ate
