"""Script de backfill das regiões comerciais do SAP Business One para o Supabase."""
from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

# Adiciona raiz do projeto ao path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import dotenv

dotenv.load_dotenv(ROOT / ".env")

from app.db import admin_connection
from app.services.sap_regiao import SAPRegiaoService

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("backfill_sap_regioes")


async def executar_backfill() -> bool:
    svc = SAPRegiaoService()
    session = svc._get_session()
    session.timeout = 15.0

    logger.info("Conectando ao SAP Service Layer em %s...", svc.base_url)
    if not svc._login(session):
        logger.error("Falha ao autenticar no SAP Service Layer.")
        return False

    # 1. Recupera todas as regiões da IB_CO_REGIAO (paginado)
    regioes_sap = []
    skip = 0
    while True:
        resp = session.get(
            f"{svc.base_url}/IB_CO_REGIAO",
            params={
                "$select": "Code,Name,U_IB_CodCom1,U_IB_CodCom3,U_IB_CodCom4",
                "$skip": skip,
            },
            timeout=10.0,
        )
        if resp.status_code != 200:
            logger.error("Erro ao consultar IB_CO_REGIAO: %s", resp.text)
            break
        dados = resp.json().get("value", [])
        if not dados:
            break
        regioes_sap.extend(dados)
        skip += len(dados)

    logger.info("Total de regiões encontradas em IB_CO_REGIAO: %d", len(regioes_sap))

    # Mapeamento de CardCode -> {tipo: ..., regioes: set()}
    cardcode_map: dict[str, dict] = {}
    for r in regioes_sap:
        nome_reg = r.get("Code") or r.get("Name")
        if not nome_reg:
            continue

        c1 = r.get("U_IB_CodCom1")
        c3 = r.get("U_IB_CodCom3")
        c4 = r.get("U_IB_CodCom4")

        for code, tipo in [(c1, "representante"), (c3, "supervisor"), (c4, "gerente")]:
            if code and code != "RH2020":
                if code not in cardcode_map:
                    cardcode_map[code] = {"tipo": tipo, "regioes": set()}
                cardcode_map[code]["regioes"].add(nome_reg)

    logger.info("Total de CardCodes ativos encontrados: %d", len(cardcode_map))

    # 2. Busca e-mail e nome de cada CardCode em BusinessPartners
    registros_finais = []
    for card_code, info in cardcode_map.items():
        try:
            resp_bp = session.get(
                f"{svc.base_url}/BusinessPartners('{card_code}')",
                params={"$select": "CardCode,CardName,EmailAddress"},
                timeout=8.0,
            )
            if resp_bp.status_code != 200:
                logger.warning("PN %s não encontrado no SAP", card_code)
                continue
            bp = resp_bp.json()
            email = (bp.get("EmailAddress") or "").strip().lower()
            if not email:
                logger.warning("PN %s (%s) não possui EmailAddress cadastrado", card_code, bp.get("CardName"))
                continue

            regs = sorted(list(info["regioes"]))
            qtd = len(regs)
            eh_multiplo = qtd > 1
            if info["tipo"] == "representante":
                rotulo = regs[0] if regs else "—"
                detalhe = regs[0] if regs else "—"
            else:
                rotulo = regs[0] if qtd == 1 else f"{qtd} regiões vinculadas"
                detalhe = ", ".join(regs)

            registros_finais.append({
                "email": email,
                "card_code": card_code,
                "card_name": bp.get("CardName"),
                "tipo": info["tipo"],
                "rotulo": rotulo,
                "regioes": regs,
                "detalhe": detalhe,
                "eh_multiplo": eh_multiplo,
            })
        except Exception as exc:
            logger.warning("Erro ao processar PN %s: %s", card_code, exc)

    logger.info("Total de parceiros comerciais com e-mail válido: %d", len(registros_finais))

    # 3. Grava no banco Supabase
    async with admin_connection() as conn:
        for reg in registros_finais:
            await conn.execute(
                """
                INSERT INTO public.sap_regioes_autores (
                    email, card_code, card_name, tipo, rotulo, regioes, detalhe, eh_multiplo, atualizado_em
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, now())
                ON CONFLICT (email) DO UPDATE SET
                    card_code = EXCLUDED.card_code,
                    card_name = EXCLUDED.card_name,
                    tipo = EXCLUDED.tipo,
                    rotulo = EXCLUDED.rotulo,
                    regioes = EXCLUDED.regioes,
                    detalhe = EXCLUDED.detalhe,
                    eh_multiplo = EXCLUDED.eh_multiplo,
                    atualizado_em = now()
                """,
                reg["email"],
                reg["card_code"],
                reg["card_name"],
                reg["tipo"],
                reg["rotulo"],
                reg["regioes"],
                reg["detalhe"],
                reg["eh_multiplo"],
            )

    logger.info("Backfill concluído com sucesso! %d parceiros inseridos/atualizados.", len(registros_finais))
    return True


if __name__ == "__main__":
    asyncio.run(executar_backfill())
