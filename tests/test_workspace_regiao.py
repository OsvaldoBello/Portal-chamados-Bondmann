from unittest.mock import AsyncMock, patch

import pytest

from app.services.sap_regiao import RegiaoInfo


@pytest.mark.asyncio
async def test_obter_regiao_do_autor_integracao():
    with patch("app.services.sap_regiao.admin_connection") as mock_conn:
        mock_ctx = AsyncMock()
        mock_ctx.fetchval.return_value = "douglas.melo@bondmann.com.br"
        mock_conn.return_value.__aenter__.return_value = mock_ctx

        with patch("app.services.sap_regiao.obter_regiao_por_email") as mock_por_email:
            info = RegiaoInfo(
                tipo="representante",
                rotulo="057-VACARIA",
                regioes=["057-VACARIA"],
                detalhe="057-VACARIA",
                eh_multiplo=False,
            )
            mock_por_email.return_value = info

            from app.services.sap_regiao import obter_regiao_do_autor
            res = await obter_regiao_do_autor("c513fce8-e66a-4c35-8355-deb576db0eb2")
            assert res == info
            mock_por_email.assert_called_once_with("douglas.melo@bondmann.com.br")
