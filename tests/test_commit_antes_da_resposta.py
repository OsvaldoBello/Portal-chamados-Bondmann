"""A transação do request commita ANTES da resposta sair (app/routes/transacao.py).

Bug real, duas encarnações: abrir chamado caía em 404 (2026-08-27) e encerrar
chamado recarregava a tela com o status antigo (2026-09-03, de novo em
2026-09-10 no ``BD-2026-00895``). Nos dois casos a escrita estava certa no
banco — o que estava errado era a ORDEM: o FastAPI mandava o `303` pro browser
antes do commit, e o GET seguinte (outra transação) lia o estado anterior.

Aqui não há banco: um pool falso registra num log único quando a transação
commita e quando o primeiro byte da resposta é enviado, e o teste checa a ordem
entre os dois.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, FastAPI
from fastapi.responses import RedirectResponse

import app.db as db
from app.db import commit_now, rls_connection, rls_request_scope
from app.routes.transacao import CommitBeforeResponseRoute

CLAIMS = {"sub": "11111111-1111-1111-1111-111111111111", "role": "authenticated"}


# --------------------------------------------------------------------------
# Pool falso: só registra os eventos que interessam, na ordem em que ocorrem.
# --------------------------------------------------------------------------
class _FakeTx:
    def __init__(self, log: list[str], n: int) -> None:
        self._log, self._n = log, n

    async def start(self) -> None:
        self._log.append(f"tx{self._n}.start")

    async def commit(self) -> None:
        self._log.append(f"tx{self._n}.commit")

    async def rollback(self) -> None:
        self._log.append(f"tx{self._n}.rollback")


class _FakeConn:
    def __init__(self, log: list[str]) -> None:
        self._log = log
        self._txs = 0

    def transaction(self) -> _FakeTx:
        self._txs += 1
        return _FakeTx(self._log, self._txs)

    async def execute(self, sql: str) -> None:
        self._log.append("claims" if "set_config" in sql else "execute")

    async def fetchval(self, *a: Any, **k: Any) -> None:
        self._log.append("escrita")


class _FakeAcquire:
    def __init__(self, conn: _FakeConn, log: list[str]) -> None:
        self._conn, self._log = conn, log

    async def __aenter__(self) -> _FakeConn:
        self._log.append("acquire")
        return self._conn

    async def __aexit__(self, *exc: Any) -> None:
        self._log.append("release")


class _FakePool:
    def __init__(self, log: list[str]) -> None:
        self.conn = _FakeConn(log)
        self._log = log

    def acquire(self) -> _FakeAcquire:
        return _FakeAcquire(self.conn, self._log)


def _instalar_pool(monkeypatch) -> list[str]:
    log: list[str] = []
    monkeypatch.setattr(db, "_pool", _FakePool(log))
    return log


# --------------------------------------------------------------------------
# `commit_now` + `_RLSHolder`
# --------------------------------------------------------------------------
async def test_commit_now_commita_e_proxima_query_abre_tx_nova(monkeypatch) -> None:
    log = _instalar_pool(monkeypatch)
    async with rls_request_scope(CLAIMS):
        async with rls_connection(CLAIMS) as conn:
            await conn.fetchval("UPDATE chamados ...")
        await commit_now()
        # Depois do commit a conexão continua emprestada, mas a tx morreu junto
        # com o `SET LOCAL` dos claims: a próxima query precisa de tx + claims.
        async with rls_connection(CLAIMS) as conn:
            await conn.fetchval("SELECT ...")

    assert log == [
        "acquire", "tx1.start", "claims", "escrita",
        "tx1.commit",
        "tx2.start", "claims", "escrita",
        "tx2.commit",
        "release",
    ]


async def test_commit_now_sem_query_seguinte_nao_recommita_nem_reabre(monkeypatch) -> None:
    log = _instalar_pool(monkeypatch)
    async with rls_request_scope(CLAIMS):
        async with rls_connection(CLAIMS) as conn:
            await conn.fetchval("UPDATE chamados ...")
        await commit_now()
        await commit_now()  # idempotente

    # Uma tx, um commit — nada de transação vazia aberta só pra fechar no teardown.
    assert log == ["acquire", "tx1.start", "claims", "escrita", "tx1.commit", "release"]


async def test_commit_now_sem_conexao_aberta_e_noop(monkeypatch) -> None:
    log = _instalar_pool(monkeypatch)
    async with rls_request_scope(CLAIMS):
        await commit_now()  # request que nunca tocou o banco (ex.: repo fake)
    assert log == []


async def test_erro_no_request_reverte_o_que_veio_depois_do_commit_now(monkeypatch) -> None:
    log = _instalar_pool(monkeypatch)
    try:
        async with rls_request_scope(CLAIMS):
            async with rls_connection(CLAIMS) as conn:
                await conn.fetchval("UPDATE chamados ...")
            await commit_now()
            async with rls_connection(CLAIMS) as conn:
                await conn.fetchval("INSERT INTO historico ...")
            raise RuntimeError("boom")
    except RuntimeError:
        pass

    assert log == [
        "acquire", "tx1.start", "claims", "escrita",
        "tx1.commit",
        "tx2.start", "claims", "escrita",
        "tx2.rollback",
        "release",
    ]


# --------------------------------------------------------------------------
# `CommitBeforeResponseRoute`: ordem commit × envio da resposta
# --------------------------------------------------------------------------
def _app_de_teste() -> FastAPI:
    router = APIRouter(route_class=CommitBeforeResponseRoute)

    async def contexto():
        # Mesmo formato das dependências reais (staff_context/portal_context):
        # abre o escopo RLS e só fecha (commit) no teardown.
        async with rls_request_scope(CLAIMS):
            yield None

    @router.post("/encerrar")
    async def encerrar(_: None = Depends(contexto)) -> RedirectResponse:
        async with rls_connection(CLAIMS) as conn:
            await conn.fetchval("UPDATE chamados SET status = 'RESOLVIDO' ...")
        return RedirectResponse("/detalhe", status_code=303)

    @router.get("/detalhe")
    async def detalhe(_: None = Depends(contexto)) -> dict:
        async with rls_connection(CLAIMS) as conn:
            await conn.fetchval("SELECT status FROM chamados ...")
        return {"ok": True}

    api = FastAPI()
    api.include_router(router)
    return api


async def _chamar(api: FastAPI, metodo: str, caminho: str, log: list[str]) -> int:
    """Chama a app no nível ASGI para ver o instante EXATO do envio da resposta.

    O TestClient só devolve a resposta pronta; aqui o `send` anota no mesmo log
    do pool falso, que é o que permite comparar a ordem dos dois eventos.
    """
    scope = {
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
        "method": metodo, "path": caminho, "raw_path": caminho.encode(),
        "root_path": "", "scheme": "http", "query_string": b"",
        "headers": [(b"host", b"testserver")], "client": ("testclient", 50000),
        "server": ("testserver", 80),
    }
    status = {"code": 0}

    async def receive() -> dict:
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(mensagem: dict) -> None:
        if mensagem["type"] == "http.response.start":
            status["code"] = mensagem["status"]
            log.append("RESPOSTA ENVIADA")

    await api(scope, receive, send)
    return status["code"]


async def test_post_commita_antes_de_enviar_a_resposta(monkeypatch) -> None:
    log = _instalar_pool(monkeypatch)
    codigo = await _chamar(_app_de_teste(), "POST", "/encerrar", log)

    assert codigo == 303
    # O ponto do bug: sem o route_class, "RESPOSTA ENVIADA" vinha ANTES do commit
    # e o GET do redirect lia o chamado ainda EM_ATENDIMENTO.
    assert log.index("tx1.commit") < log.index("RESPOSTA ENVIADA"), log
    assert log == ["acquire", "tx1.start", "claims", "escrita", "tx1.commit",
                   "RESPOSTA ENVIADA", "release"]


async def test_get_nao_paga_commit_extra(monkeypatch) -> None:
    log = _instalar_pool(monkeypatch)
    codigo = await _chamar(_app_de_teste(), "GET", "/detalhe", log)

    assert codigo == 200
    # Leitura não escreve nada: commit segue no teardown, depois da resposta.
    assert log.index("RESPOSTA ENVIADA") < log.index("tx1.commit"), log
