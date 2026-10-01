"""The User-Agent every NSE request sends (audit P3-1, owner decision 2026-10-02).

NSE's website and API refuse non-browser clients (HTTP 403 or a silent timeout), so the NSE
providers send a desktop browser's User-Agent. That is not an attempt to hide: the requests are
the same public pages and files a browser downloads, at a low rate. A contact suffix was tried
on 2026-10-02 ("... vcp-scanner (+https://github.com/...)"): NSE timed it out while the plain
string got HTTP 200 from the corporate-actions API, so none is added.

The string is configurable (``data.nse_user_agent`` in ``config/data.yaml``) so it can be
updated when NSE starts refusing an old browser version, without a code change.
"""

from __future__ import annotations

DEFAULT_NSE_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

_user_agent = DEFAULT_NSE_USER_AGENT


def nse_user_agent() -> str:
    """The User-Agent NSE providers send (set from config by the CLI)."""
    return _user_agent


def set_nse_user_agent(value: str | None) -> None:
    """Use ``value`` for every NSE provider created afterwards (None or blank: the default)."""
    global _user_agent
    _user_agent = value.strip() if value and value.strip() else DEFAULT_NSE_USER_AGENT
