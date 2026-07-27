"""Fixtures da suíte de red team do Químico (F5, Seção 8.3 do plano IA).

Duas camadas (mesma nomenclatura do plano):

- **Estrutural** (``test_estrutural.py``): sempre roda, modelo mockado,
  determinística — prova que os invariantes de segurança (Seção 8.2) seguram
  mesmo com o corpus de entradas maliciosas no chamado. Nunca pula (é o que
  garante "zero vazamentos por construção" a cada `pytest`).
- **Comportamental** (``test_comportamental.py``): chamada real ao provedor
  de IA configurado em ``IA_TRIAGEM_API_KEY``/``IA_TRIAGEM_BASE_URL`` — mede o
  comportamento do MODELO (obediência ao prompt), não é determinística e tem
  custo/rede. Só roda com opt-in explícito (``IA_REDTEAM_LIVE=1``), mesmo
  padrão de skip automático da suíte ``rls`` (``tests/e2e/conftest.py``): não
  quebra o `pytest` default de quem não tem a env configurada. É o gatilho
  permanente da Seção 8.3 — qualquer PR que altere `app/ia/prompts/*` ou o
  modelo do Químico deve rodar esta camada com `IA_REDTEAM_LIVE=1` antes do
  merge e registrar a execução (Tabela em `plano_md_mestre_IA.md`).
"""

from __future__ import annotations

import os

import pytest

IA_REDTEAM_LIVE = os.environ.get("IA_REDTEAM_LIVE") == "1"


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if IA_REDTEAM_LIVE:
        return
    skip_live = pytest.mark.skip(
        reason="IA_REDTEAM_LIVE não configurada — bateria comportamental exige "
        "IA_REDTEAM_LIVE=1 e uma IA_TRIAGEM_API_KEY real (chamada de rede, com "
        "custo). Ver Seção 8.3 do plano_md_mestre_IA.md."
    )
    for item in items:
        path = str(item.fspath).replace("\\", "/")
        if "tests/red_team/test_comportamental.py" in path:
            item.add_marker(skip_live)
