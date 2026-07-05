"""Teste de carga (uso em grande escala) — Locust (Fase 5).

Simula vários funcionários e operadores usando o portal ao mesmo tempo, para
validar a meta de **100 CCU** da Seção 2 do plano mestre (pooling Supavisor,
ETag/304 no polling da fila, cache de catálogos, 1 conexão RLS por request).

Como rodar (contra um ambiente de teste, NUNCA produção):

    pip install locust
    # credenciais de usuários de teste (staff e funcionário) via env:
    export BASE_URL="http://localhost:8080"
    export LOAD_USERS="func1@bondmann.com.br:senha,rh1@bondmann.com.br:senha"
    locust -f tests/load/locustfile.py --host "$BASE_URL"
    # depois abra http://localhost:8089 e defina, ex.: 100 usuários, spawn 10/s

Ou em modo headless (CI/linha de comando):

    locust -f tests/load/locustfile.py --host "$BASE_URL" \
           --headless -u 100 -r 10 -t 2m --csv=tests/load/report

O que medir: p95 de latência das rotas de leitura, taxa de 304 no polling da
fila (deve ser alta quando nada muda) e ausência de 5xx sob carga.
"""

from __future__ import annotations

import os
import random

from locust import HttpUser, between, task

# Lista "email:senha" separada por vírgula. Cada usuário virtual pega um par.
_CREDS = [
    tuple(par.split(":", 1))
    for par in os.getenv("LOAD_USERS", "").split(",")
    if ":" in par
]


def _csrf(client) -> str:
    """Busca o /login para obter o cookie+token CSRF (double-submit)."""
    resp = client.get("/login")
    # O token vai no HTML e o cookie é setado; para simplificar, extraímos do HTML.
    marcador = 'name="csrf_token" value="'
    i = resp.text.find(marcador)
    if i == -1:
        return ""
    i += len(marcador)
    return resp.text[i : resp.text.find('"', i)]


class PortalUser(HttpUser):
    """Funcionário/operador navegando: dashboard, fila (polling), sino, detalhe."""

    wait_time = between(1, 4)  # think time realista

    def on_start(self):
        if not _CREDS:
            self.email, self.password = "", ""
            return
        self.email, self.password = random.choice(_CREDS)
        token = _csrf(self.client)
        self.client.post(
            "/login",
            data={"email": self.email, "password": self.password, "csrf_token": token},
            allow_redirects=True,
            name="POST /login",
        )

    @task(5)
    def portal_dashboard(self):
        self.client.get("/portal", name="GET /portal")

    @task(8)
    def fila_polling(self):
        # Polling da fila com ETag: mede a taxa de 304 (Seção 2.2).
        self.client.get("/workspace/fila/fragmento", name="GET /workspace/fila/fragmento")

    @task(4)
    def notificacoes(self):
        self.client.get("/notificacoes", name="GET /notificacoes")

    @task(3)
    def realtime_config(self):
        self.client.get("/realtime/config", name="GET /realtime/config")

    @task(1)
    def health(self):
        self.client.get("/health", name="GET /health")
