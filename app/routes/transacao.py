"""Commit da transação do request ANTES da resposta sair (corrida do redirect).

Todo request roda em **uma** transação (``app.db._RLSHolder``), commitada no
teardown da dependência ``yield`` que abriu o escopo (``staff_context``,
``portal_context``…). Só que o FastAPI 0.141 envia a resposta ao cliente
**dentro** do mesmo ``AsyncExitStack`` que fecha essas dependências e **antes**
de fechá-las (``fastapi/routing.py::request_response``: ``await response(...)``
roda antes do ``async with AsyncExitStack()`` sair).

Consequência: num POST que escreve e devolve ``303`` para uma página que **lê a
mesma linha**, o browser podia seguir o redirect e o GET seguinte — outra
transação, outra conexão do pool — rodar antes do commit. RLS não enxerga o que
não foi commitado, então a tela vinha desatualizada. Já mordeu duas vezes:

* 2026-08-27 — abertura de chamado: o detalhe do chamado recém-criado dava 404
  ("Página não encontrada") mesmo com o INSERT gravado.
* 2026-09-03 e de novo 2026-09-10 (``BD-2026-00895``) — ``/encerrar``: o
  chamado virava ``RESOLVIDO`` no banco, mas a tela recarregada mostrava o
  status antigo, o painel "Encerrar chamado" de volta e a conversa sem a nota
  de solução, como se o encerramento não tivesse acontecido.

As correções anteriores foram pontuais (``await commit_now()`` em cada rota) e
por isso a mesma corrida voltou noutro endpoint. Aqui a garantia é da camada de
rota: qualquer método que não seja de leitura commita assim que o handler
devolve a Response e antes dela ser enviada. Rotas de leitura não pagam nada, e
requests que nunca abriram conexão (repos fake nos testes) também não —
``commit_now`` é no-op nesses casos.

Bônus: escrita não depende mais do cliente receber a resposta. Antes, se o
browser desconectasse durante o envio, a exceção subia pelo mesmo stack e a
transação era **revertida** — a ação sumia mesmo tendo sido concluída.
"""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from typing import Any

from fastapi import Request, Response
from fastapi.routing import APIRoute

from app.db import commit_now

# GET/HEAD/OPTIONS não escrevem; TRACE nem é servido. O resto (POST/PUT/PATCH/
# DELETE) pode ter escrito algo que a próxima tela vai reler.
METODOS_DE_LEITURA = frozenset({"GET", "HEAD", "OPTIONS"})


class CommitBeforeResponseRoute(APIRoute):
    """``route_class`` que commita a transação do request antes de responder.

    O handler original resolve as dependências e roda o endpoint, devolvendo a
    Response — ainda **sem** enviá-la (quem envia é o ``request_response`` do
    FastAPI, um nível acima). Este é o único ponto em que dá para commitar
    depois de toda a escrita do endpoint e antes do primeiro byte sair.

    Se o endpoint levantar exceção, ``commit_now`` não roda e o teardown segue
    revertendo a transação como sempre.
    """

    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        handler_original = super().get_route_handler()

        async def handler(request: Request) -> Response:
            resposta = await handler_original(request)
            if request.method not in METODOS_DE_LEITURA:
                await commit_now()
            return resposta

        return handler
