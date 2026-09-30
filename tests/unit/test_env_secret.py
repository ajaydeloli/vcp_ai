"""Placeholder credentials copied from .env.example count as unset (found in audit Fix 5b)."""

from __future__ import annotations

import pytest

from vcp_scanner.cli_pipeline import env_secret


@pytest.mark.parametrize(
    "value",
    ["", "   ", "your_upstox_access_token_here", "your_kite_api_key_here"],
)
def test_blank_or_placeholder_is_unset(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv("SOME_TOKEN", value)
    assert env_secret("SOME_TOKEN") is None


def test_missing_is_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SOME_TOKEN", raising=False)
    assert env_secret("SOME_TOKEN") is None


def test_real_value_is_returned_stripped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SOME_TOKEN", "  abc123  ")
    assert env_secret("SOME_TOKEN") == "abc123"


def test_every_env_example_placeholder_is_recognised() -> None:
    """Keeps .env.example and the placeholder rule in step."""
    from pathlib import Path

    example = Path(__file__).resolve().parents[2] / ".env.example"
    for line in example.read_text().splitlines():
        if "=" not in line or line.lstrip().startswith("#"):
            continue
        name, value = line.split("=", 1)
        if name.endswith(("_KEY", "_SECRET", "_TOKEN", "_ID")):
            with pytest.MonkeyPatch.context() as mp:
                mp.setenv(name, value)
                assert env_secret(name) is None, f"{name}={value} would be used as a credential"


def test_corporate_actions_run_without_upstox_when_token_is_a_placeholder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from vcp_scanner import cli_pipeline

    seen: dict[str, object] = {}

    def fake_build(token: str | None):  # noqa: ANN202
        seen["token"] = token
        raise SystemExit(0)  # stop right after provider construction

    monkeypatch.setenv("UPSTOX_ACCESS_TOKEN", "your_upstox_access_token_here")
    monkeypatch.setattr(cli_pipeline, "_build_ca_providers", fake_build)
    import argparse
    from pathlib import Path

    args = argparse.Namespace(
        env_file="/nonexistent.env", start="2024-01-01", end="2024-01-02",
        config_dir=str(Path(__file__).resolve().parents[2] / "config"),
        db=":memory:", instrument=None, limit=None,
    )  # fmt: skip
    with pytest.raises(SystemExit):
        cli_pipeline.run_corporate_actions(args)
    assert seen["token"] is None
