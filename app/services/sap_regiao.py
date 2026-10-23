"""Serviço de consulta de região comercial no SAP Business One Service Layer."""

from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Any
import urllib.parse
import urllib3

import requests

import app.cache as cache
from app.config import get_settings
from app.db import admin_connection

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
logger = logging.getLogger("app.services.sap_regiao")

CACHE_TTL_REGIAO_S = 21600.0  # 6 horas
CACHE_TTL_VAZIO_S = 3600.0    # 1 hora para e-mails sem cadastro no SAP
CACHE_TTL_EMAIL_S = 86400.0   # 24 horas para id -> e-mail


@dataclass(frozen=True)
class RegiaoInfo:
    tipo: str  # "representante" | "supervisor" | "gerente"
    rotulo: str  # ex: "057-VACARIA" ou "6 regiões vinculadas"
    regioes: list[str]  # lista de códigos das regiões
    detalhe: str  # ex: "033-POA, 009-BENTO..."
    eh_multiplo: bool  # True se len(regioes) > 1


class SAPRegiaoService:
    def __init__(self) -> None:
        settings = get_settings()
        default_url = "https://10.151.4.40:50000/b1s/v1"
        self.base_url = (settings.sap_api_url or default_url).rstrip("/")
        if not self.base_url.endswith("/b1s/v1"):
            self.base_url = f"{self.base_url}/b1s/v1"
        self.company_db = settings.sap_company_db
        self.username = settings.sap_username
        self.password = settings.sap_password
        self.proxy_url = settings.sap_proxy_url

        self._session: requests.Session | None = None
        self._session_id: str | None = None

    def _get_session(self) -> requests.Session:
        if self._session is not None:
            return self._session
        s = requests.Session()
        s.verify = False
        if self.proxy_url:
            s.proxies = {"http": self.proxy_url, "https": self.proxy_url}
            s.trust_env = False
        self._session = s
        return s

    def _login(self, session: requests.Session) -> bool:
        if self._session_id:
            return True
        if not (self.company_db and self.username and self.password):
            logger.debug("Credenciais SAP incompletas para login.")
            return False
        try:
            url = f"{self.base_url}/Login"
            payload = {
                "CompanyDB": self.company_db,
                "UserName": self.username,
                "Password": self.password,
            }
            resp = session.post(url, json=payload, timeout=3)
            if resp.status_code == 200:
                self._session_id = resp.json().get("SessionId")
                return True
            logger.warning("Falha de autenticação no SAP Service Layer (status=%s)", resp.status_code)
            return False
        except Exception as exc:
            logger.warning("Erro de conexão no login SAP Service Layer: %s", exc)
            return False

    def consultar_regiao_sap(self, email: str) -> RegiaoInfo | None:
        email_limpo = (email or "").strip().lower()
        if not email_limpo:
            return None

        try:
            session = self._get_session()
            if not self._login(session):
                return None

            # 1. Localiza os BusinessPartners pelo e-mail
            bp_url = f"{self.base_url}/BusinessPartners"
            filter_bp = f"EmailAddress eq '{email_limpo}'"
            bp_resp = session.get(
                bp_url,
                params={"$filter": filter_bp, "$select": "CardCode,CardName,CardType,EmailAddress,Valid"},
                timeout=3,
            )
            if bp_resp.status_code != 200:
                logger.warning("SAP BusinessPartners respondeu status %s", bp_resp.status_code)
                return None

            bps = bp_resp.json().get("value", [])
            if not bps:
                return None

            # Prioriza fornecedores (cSupplier)
            fornecedores = [b for b in bps if b.get("CardType") == "cSupplier"]
            alvos = fornecedores if fornecedores else bps
            card_codes = [b.get("CardCode") for b in alvos if b.get("CardCode")]

            for card_code in card_codes:
                filter_reg = (
                    f"U_IB_CodCom1 eq '{card_code}' or "
                    f"U_IB_CodCom3 eq '{card_code}' or "
                    f"U_IB_CodCom4 eq '{card_code}'"
                )
                r_reg = session.get(
                    f"{self.base_url}/IB_CO_REGIAO",
                    params={
                        "$filter": filter_reg,
                        "$select": "Code,Name,U_IB_CodCom1,U_IB_CodCom3,U_IB_CodCom4",
                    },
                    timeout=3,
                )
                if r_reg.status_code == 200:
                    regs = r_reg.json().get("value", [])
                    if regs:
                        # Identifica o papel de acordo com qual campo bateu
                        rep_regs = [r for r in regs if r.get("U_IB_CodCom1") == card_code]
                        sup_regs = [r for r in regs if r.get("U_IB_CodCom3") == card_code]
                        ger_regs = [r for r in regs if r.get("U_IB_CodCom4") == card_code]

                        if rep_regs:
                            nomes = [r.get("Code") or r.get("Name") for r in rep_regs if r.get("Code") or r.get("Name")]
                            if nomes:
                                return RegiaoInfo(
                                    tipo="representante",
                                    rotulo=nomes[0],
                                    regioes=nomes,
                                    detalhe=nomes[0],
                                    eh_multiplo=len(nomes) > 1,
                                )
                        elif sup_regs:
                            nomes = [r.get("Code") or r.get("Name") for r in sup_regs if r.get("Code") or r.get("Name")]
                            if nomes:
                                qtd = len(nomes)
                                rotulo = nomes[0] if qtd == 1 else f"{qtd} regiões vinculadas"
                                return RegiaoInfo(
                                    tipo="supervisor",
                                    rotulo=rotulo,
                                    regioes=nomes,
                                    detalhe=", ".join(nomes),
                                    eh_multiplo=qtd > 1,
                                )
                        elif ger_regs:
                            nomes = [r.get("Code") or r.get("Name") for r in ger_regs if r.get("Code") or r.get("Name")]
                            if nomes:
                                qtd = len(nomes)
                                rotulo = nomes[0] if qtd == 1 else f"{qtd} regiões vinculadas"
                                return RegiaoInfo(
                                    tipo="gerente",
                                    rotulo=rotulo,
                                    regioes=nomes,
                                    detalhe=", ".join(nomes),
                                    eh_multiplo=qtd > 1,
                                )

            return None
        except Exception as exc:
            logger.warning("Falha ao consultar região no SAP para %s: %s", email, exc)
            return None


_instancia_service: SAPRegiaoService | None = None


def get_sap_regiao_service() -> SAPRegiaoService:
    global _instancia_service
    if _instancia_service is None:
        _instancia_service = SAPRegiaoService()
    return _instancia_service


def obter_regiao_por_email(email: str) -> RegiaoInfo | None:
    if not email:
        return None
    cache_key = f"sap_regiao:{email.strip().lower()}"
    cached = cache.get(cache_key)
    if cached is not None:
        return None if cached == "__VAZIO__" else cached

    svc = get_sap_regiao_service()
    info = svc.consultar_regiao_sap(email)
    if info is not None:
        cache.set(cache_key, info, ttl=CACHE_TTL_REGIAO_S)
    else:
        cache.set(cache_key, "__VAZIO__", ttl=CACHE_TTL_VAZIO_S)
    return info


async def obter_email_do_autor(cliente_id: str) -> str | None:
    if not cliente_id:
        return None
    cache_key = f"user_email:{cliente_id}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    async with admin_connection() as conn:
        email = await conn.fetchval(
            "SELECT email FROM auth.users WHERE id = $1::uuid", cliente_id
        )
        if email:
            cache.set(cache_key, email, ttl=CACHE_TTL_EMAIL_S)
            return email
    return None


async def obter_regiao_do_autor(cliente_id: str) -> RegiaoInfo | None:
    email = await obter_email_do_autor(cliente_id)
    if not email:
        return None
    return obter_regiao_por_email(email)
