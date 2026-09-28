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
