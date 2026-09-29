"""Gerência responsável por colaborador interno → e-mail do gerente, que vira
o líder na UBD (plano v2, F4 — tabela da Seção 0, confirmada pelo gestor)."""

from __future__ import annotations

OUTRA = "Outra (informar o e-mail do gestor)"

GERENCIAS: tuple[tuple[str, str], ...] = (
    ("Compras — Alessandro Lodion", "alessandro@bondmann.com.br"),
    ("Controladoria — Anderson Viana", "anderson@bondmann.com.br"),
    ("PCP / Recebimento / Expedição — Elias Kirsten", "elias@bondmann.com.br"),
    ("Laboratório / Químico — Guilherme Rosa", "guilherme.rosa@bondmann.com.br"),
    ("RH — Mariana Silva", "mariana.silva@bondmann.com.br"),
    ("Comercial — Patricia Alves", "patricia.alves@bondmann.com.br"),
    ("Financeiro — Thiago Rodrigues", "thiago.rodrigues@bondmann.com.br"),
    ("Filial — Rogério Rossini", "rogerio@bondmann.com.br"),
)
OPCOES = tuple(r for r, _ in GERENCIAS) + (OUTRA,)


def email_do_gerente(rotulo: str | None) -> str | None:
    return dict(GERENCIAS).get((rotulo or "").strip())
