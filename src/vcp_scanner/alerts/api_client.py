"""The dashboard API in-process (FastAPI TestClient), for the evening alert."""

from __future__ import annotations

from typing import Any


class Api:
    """The dashboard API in-process (FastAPI TestClient), built on first use."""

    def __init__(self, config_dir: str, data_dir: str, client: Any = None) -> None:
        self.config_dir, self.data_dir = config_dir, data_dir
        self._client: Any = client

    def get(self, path: str, **params: Any) -> Any:
        if self._client is None:
            from fastapi.testclient import TestClient

            from vcp_scanner.api.app import create_app

            app = create_app(config_dir=self.config_dir, data_dir=self.data_dir)
            self._client = TestClient(app)
        clean = {k: v for k, v in params.items() if v is not None}
        r = self._client.get(f"/api/v1/{path}", params=clean)
        if r.status_code != 200:
            is_json = "json" in r.headers.get("content-type", "")
            detail = r.json().get("detail") if is_json else r.text
            raise ValueError(f"{path}: HTTP {r.status_code}: {detail}")
        return r.json()
