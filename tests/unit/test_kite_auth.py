"""Unit tests for KiteAuthenticator."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from dotenv import dotenv_values

from vcp_scanner.auth.kite_auth import KiteAuthenticator


@pytest.fixture
def mock_kiteconnect():
    with patch("vcp_scanner.auth.kite_auth.KiteConnect") as MockKite:
        mock_instance = MockKite.return_value
        mock_instance.login_url.return_value = (
            "https://kite.trade/connect/login?api_key=test_api_key"
        )
        mock_instance.generate_session.return_value = {
            "access_token": "mocked_access_token",
            "public_token": "mocked_public_token",
        }
        yield mock_instance


def test_get_login_url(mock_kiteconnect) -> None:
    authenticator = KiteAuthenticator(api_key="test_api_key", api_secret="test_secret")
    url = authenticator.get_login_url()
    assert url == "https://kite.trade/connect/login?api_key=test_api_key"
    mock_kiteconnect.login_url.assert_called_once()


def test_generate_and_store_token(mock_kiteconnect, tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    authenticator = KiteAuthenticator(
        api_key="test_api_key", api_secret="test_secret", env_file=env_file
    )

    # Execute the flow
    access_token = authenticator.generate_and_store_token(request_token="req_token_123")

    # Assert return value
    assert access_token == "mocked_access_token"

    # Assert KiteConnect API call
    mock_kiteconnect.generate_session.assert_called_once_with(
        "req_token_123", api_secret="test_secret"
    )

    # Assert .env file was written securely
    assert env_file.exists()

    env_data = dotenv_values(str(env_file))
    assert env_data.get("KITE_ACCESS_TOKEN") == "mocked_access_token"
    assert env_data.get("KITE_PUBLIC_TOKEN") == "mocked_public_token"


def test_created_env_file_is_owner_only(mock_kiteconnect, tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    KiteAuthenticator("k", "s", env_file=env_file).generate_and_store_token("req")
    assert env_file.stat().st_mode & 0o777 == 0o600


def test_existing_world_readable_env_file_is_tightened(mock_kiteconnect, tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("KITE_API_KEY=k\n")
    env_file.chmod(0o644)
    KiteAuthenticator("k", "s", env_file=env_file).generate_and_store_token("req")
    assert env_file.stat().st_mode & 0o777 == 0o600
    assert dotenv_values(str(env_file))["KITE_API_KEY"] == "k"  # content kept


# ---------------------------------------------------------------------------- `vcp auth kite`


class _FakeAuthenticator:
    seen: dict[str, str] = {}

    def __init__(self, api_key: str, api_secret: str, env_file: str) -> None:
        type(self).seen = {"api_key": api_key, "api_secret": api_secret, "env_file": env_file}

    def get_login_url(self) -> str:
        return "https://kite.example/login"

    def generate_and_store_token(self, request_token: str) -> str:
        return "tok"


@pytest.fixture
def cli_env(monkeypatch: pytest.MonkeyPatch):
    import vcp_scanner.auth.kite_auth as kite_auth

    for var in ("KITE_API_KEY", "KITE_API_SECRET"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(kite_auth, "KiteAuthenticator", _FakeAuthenticator)
    monkeypatch.setattr("builtins.input", lambda prompt="": "req_token")
    _FakeAuthenticator.seen = {}
    yield monkeypatch
    for var in ("KITE_API_KEY", "KITE_API_SECRET"):  # load_dotenv writes os.environ
        monkeypatch.delenv(var, raising=False)


def test_auth_kite_reads_credentials_from_the_env_file(cli_env, tmp_path: Path) -> None:
    """Regression: the command read only os.environ and ignored .env, so it failed with
    'Kite API Key and Secret are required' although both were in the file."""
    from vcp_scanner.cli import main

    env_file = tmp_path / ".env"
    env_file.write_text("KITE_API_KEY=file_key\nKITE_API_SECRET=file_secret\n")
    assert main(["auth", "kite", "--env-file", str(env_file)]) == 0
    assert _FakeAuthenticator.seen == {
        "api_key": "file_key",
        "api_secret": "file_secret",
        "env_file": str(env_file),
    }


def test_auth_kite_prefers_arguments_then_environment(cli_env, tmp_path: Path) -> None:
    from vcp_scanner.cli import main

    env_file = tmp_path / ".env"
    env_file.write_text("KITE_API_KEY=file_key\nKITE_API_SECRET=file_secret\n")
    cli_env.setenv("KITE_API_SECRET", "env_secret")
    argv = ["auth", "kite", "--env-file", str(env_file), "--api-key", "arg_key"]
    assert main(argv) == 0
    assert _FakeAuthenticator.seen["api_key"] == "arg_key"
    assert _FakeAuthenticator.seen["api_secret"] == "env_secret"


def test_auth_kite_still_fails_clearly_without_credentials(
    cli_env, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from vcp_scanner.cli import main

    env_file = tmp_path / "missing.env"
    assert main(["auth", "kite", "--env-file", str(env_file)]) == 1
    assert str(env_file) in capsys.readouterr().err
    assert _FakeAuthenticator.seen == {}
