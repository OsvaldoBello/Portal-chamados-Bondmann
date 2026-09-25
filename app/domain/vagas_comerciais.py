"""Vagas comerciais do SAP (plano v2, F3): marcadores de equipe (supervisor,
`U_IB_CodCom3`) e de gerência (gerente, `U_IB_CodCom4`) na IB_CO_REGIAO.

Extraído do SAP em 2026-09-25 (BusinessPartners `RH*` com nome EQUIPE/GERENTE,
só os ativos — `RH2040 EQUIPE RJ 1` está inativo). Equipe nova = uma linha aqui.
"""

from __future__ import annotations

SEPARADOR = " — "

EQUIPES: tuple[tuple[str, str], ...] = (
    ("RH2018", "EQUIPE SP 1"), ("RH2019", "EQUIPE SP 2"), ("RH2021", "EQUIPE MG 2"),
    ("RH2024", "EQUIPE SP 5"), ("RH2029", "EQUIPE RS 1"), ("RH2032", "EQUIPE PR 2"),
    ("RH2039", "EQUIPE SP 6"), ("RH2041", "EQUIPE PR 1"), ("RH2043", "EQUIPE MG 1"),
    ("RH2047", "EQUIPE SP 3"), ("RH2048", "EQUIPE MG 3"), ("RH2053", "EQUIPE SC 1"),
    ("RH2054", "EQUIPE PR 4"), ("RH2055", "EQUIPE GO1"), ("RH2074", "EQUIPE PR3"),
    ("RH2075", "EQUIPE RS 2"), ("RH2082", "EQUIPE SC 2"), ("RH2083", "EQUIPE RS 5"),
    ("RH2084", "EQUIPE PR 5"), ("RH2086", "EQUIPE SP 4"), ("RH2090", "EQUIPE DIRETA SP"),
)
GERENCIAS: tuple[tuple[str, str], ...] = (("RH2005", "GERENTE SP"), ("RH2033", "GERENTE MG/RJ"))


def rotulo(codigo: str, nome: str) -> str:
    return f"{codigo}{SEPARADOR}{nome}"


ROTULOS_EQUIPES = tuple(rotulo(c, n) for c, n in EQUIPES)
ROTULOS_GERENCIAS = tuple(rotulo(c, n) for c, n in GERENCIAS)


def vaga_do_rotulo(valor: str | None) -> dict[str, str] | None:
    """``"RH2018 — EQUIPE SP 1"`` → ``{"tipo": "EQUIPE", "codigo": ..., "nome": ...}``."""
    texto = (valor or "").strip()
    for tipo, catalogo in (("EQUIPE", EQUIPES), ("GERENCIA", GERENCIAS)):
        for codigo, nome in catalogo:
            if texto == rotulo(codigo, nome):
                return {"tipo": tipo, "codigo": codigo, "nome": nome}
    return None
