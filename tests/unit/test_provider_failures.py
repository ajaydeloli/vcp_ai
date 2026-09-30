"""Provider failures must raise, never look like an empty (successful) result (audit P0-4)."""

from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock

import pytest
import requests

from vcp_scanner.data.providers.nse_ca import NSECorporateActionProvider
from vcp_scanner.data.providers.nse_security_master import NSESecurityMasterProvider
from vcp_scanner.data.providers.nse_surveillance import NSESurveillanceProvider
from vcp_scanner.data.providers.upstox_ca import UpstoxCorporateActionProvider
from vcp_scanner.domain.errors import ProviderError
from vcp_scanner.domain.market import Instrument

START, END = date(2024, 1, 1), date(2024, 1, 31)


def _resp(status: int = 200, *, text: str = "", json_data=None) -> MagicMock:
    r = MagicMock()
    r.status_code = status
    r.text = text
    r.json.return_value = json_data
    return r


def _with_session(provider, *responses_or_exc):
    session = MagicMock()
    session.get.side_effect = list(responses_or_exc) * 20  # homepage + api calls, repeated
    provider._session = session
    return provider


# ---- NSE corporate actions ---------------------------------------------------------


def test_nse_ca_http_error_raises() -> None:
    p = _with_session(NSECorporateActionProvider(), _resp(500, text="boom"))
    with pytest.raises(ProviderError):
        p.get_actions(START, END)


def test_nse_ca_network_error_raises() -> None:
    p = _with_session(NSECorporateActionProvider(), requests.ConnectionError("down"))
    with pytest.raises(ProviderError):
        p.get_actions(START, END)


def test_nse_ca_genuinely_empty_response_is_still_empty() -> None:
    p = _with_session(NSECorporateActionProvider(), _resp(200, json_data=[]))
    assert p.get_actions(START, END) == []


# ---- NSE security master -----------------------------------------------------------


def test_security_master_download_failure_raises() -> None:
    p = _with_session(NSESecurityMasterProvider(), _resp(503, text="unavailable"))
    with pytest.raises(ProviderError):
        p.get_security_history(START, END)


def test_security_master_empty_file_raises() -> None:
    header = "SYMBOL, NAME OF COMPANY, SERIES, DATE OF LISTING, ISIN NUMBER\n"
    p = _with_session(NSESecurityMasterProvider(), _resp(200, text=header))
    with pytest.raises(ProviderError):
        p.get_security_history(START, END)


def test_security_master_valid_file_parses() -> None:
    text = (
        "SYMBOL, NAME OF COMPANY, SERIES, DATE OF LISTING, ISIN NUMBER\n"
        "ABC,ABC Ltd,EQ,01-JAN-2000,INE000A01010\n"
    )
    p = _with_session(NSESecurityMasterProvider(), _resp(200, text=text))
    records = p.get_security_history(START, END)
    assert [r.symbol for r in records] == ["ABC"]


# ---- NSE surveillance (a swallowed failure would close every open flag) ------------


def test_surveillance_asm_failure_raises() -> None:
    p = _with_session(NSESurveillanceProvider(), _resp(500, text="boom"))
    with pytest.raises(ProviderError):
        p.get_flags(START, END)


def test_surveillance_t2t_failure_raises() -> None:
    asm_ok = _resp(200, json_data={"longterm": {"data": []}, "shortterm": {"data": []}})
    # homepage, ASM ok, then sec_list fails
    p = _with_session(NSESurveillanceProvider(), _resp(200), asm_ok, _resp(500, text="boom"))
    p._session.get.side_effect = [_resp(200), asm_ok, _resp(500, text="boom")]
    with pytest.raises(ProviderError):
        p.get_flags(START, END)


# ---- Upstox corporate actions ------------------------------------------------------


def _inst(sym: str) -> Instrument:
    return Instrument(instrument_id=f"NSE_EQ|{sym}", symbol=sym, exchange="NSE", isin=f"INE{sym}")


def test_upstox_auth_failure_raises() -> None:
    p = _with_session(UpstoxCorporateActionProvider("tok"), _resp(401, text="bad token"))
    with pytest.raises(ProviderError):
        p.get_actions(START, END, [_inst("AAA"), _inst("BBB")])


def test_upstox_partial_failures_raise_instead_of_returning_partial_list() -> None:
    p = _with_session(UpstoxCorporateActionProvider("tok"), _resp(200, json_data={"data": []}))
    p._session.get.side_effect = [_resp(200, json_data={"data": []}), _resp(502, text="bad gw")]
    with pytest.raises(ProviderError, match="1 of 2"):
        p.get_actions(START, END, [_inst("AAA"), _inst("BBB")])


def test_upstox_404_means_no_actions_not_failure() -> None:
    p = _with_session(UpstoxCorporateActionProvider("tok"), _resp(404, text="none"))
    assert p.get_actions(START, END, [_inst("AAA")]) == []
