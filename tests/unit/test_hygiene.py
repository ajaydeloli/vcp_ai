"""Audit P3-1 hygiene (clean-up batch 2, C9): NSE User-Agent from config, no secret flags."""

from __future__ import annotations

from pathlib import Path

import pytest

from vcp_scanner.data.providers import nse_http
from vcp_scanner.data.providers.nse_ca import NSECorporateActionProvider
from vcp_scanner.data.providers.nse_delisted import NSEDelistedProvider
from vcp_scanner.data.providers.nse_security_master import NSESecurityMasterProvider
from vcp_scanner.data.providers.nse_surveillance import NSESurveillanceProvider


@pytest.fixture(autouse=True)
def _reset_user_agent():
    yield
    nse_http.set_nse_user_agent(None)


def test_every_nse_provider_sends_the_configured_user_agent() -> None:
    nse_http.set_nse_user_agent("Mozilla/5.0 TestBrowser/99")
    for provider in (
        NSECorporateActionProvider(),
        NSESurveillanceProvider(),
        NSESecurityMasterProvider(),
        NSEDelistedProvider(),
    ):
        assert provider._session.headers["User-Agent"] == "Mozilla/5.0 TestBrowser/99"


def test_blank_user_agent_falls_back_to_the_default() -> None:
    nse_http.set_nse_user_agent("  ")
    assert nse_http.nse_user_agent() == nse_http.DEFAULT_NSE_USER_AGENT


def test_cli_applies_the_user_agent_from_the_config_folder(tmp_path: Path) -> None:
    import shutil

    from vcp_scanner.cli import main

    cfg = tmp_path / "config"
    shutil.copytree(Path(__file__).resolve().parents[2] / "config", cfg)
    data = cfg / "data.yaml"
    data.write_text(data.read_text().replace("nse_user_agent: null", 'nse_user_agent: "UA/1"'))
    main(["config", "validate", "--config-dir", str(cfg)])
    assert nse_http.nse_user_agent() == "UA/1"


def test_api_secret_flag_is_refused_with_a_pointer_to_env(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from vcp_scanner.cli import main

    argv = ["auth", "kite", "--env-file", str(tmp_path / ".env"), "--api-secret", "s3cr3tV4lue"]
    assert main(argv) == 2
    err = capsys.readouterr().err
    assert "removed" in err and "KITE_API_SECRET" in err
    assert "s3cr3tV4lue" not in err  # the secret is not echoed back


def test_scratch_folder_is_not_tracked() -> None:
    gitignore = (Path(__file__).resolve().parents[2] / ".gitignore").read_text()
    assert "\nscratch/\n" in gitignore
