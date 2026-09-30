"""Kite Connect Authentication Workflow (DATA_SPECIFICATION section 28A).

Handles the out-of-band interactive login flow to retrieve daily access tokens
without exposing secrets to logs or databases. Stores tokens securely in .env.
"""

from pathlib import Path

from dotenv import set_key

try:
    from kiteconnect import KiteConnect
    from kiteconnect.exceptions import NetworkException, TokenException
except ImportError:
    KiteConnect = None
    TokenException = Exception
    NetworkException = Exception


class KiteAuthenticator:
    """Manages Kite Connect interactive login and token storage."""

    def __init__(self, api_key: str, api_secret: str, env_file: Path | str = ".env") -> None:
        if KiteConnect is None:
            raise ImportError("kiteconnect package is required for KiteAuthenticator")

        self.api_key = api_key
        self.api_secret = api_secret
        self.env_file = Path(env_file)
        self.kite = KiteConnect(api_key=self.api_key)

    def get_login_url(self) -> str:
        """Return the Kite Connect login URL."""
        return str(self.kite.login_url())

    def generate_and_store_token(self, request_token: str) -> str:
        """Exchange the request token for an access token and store it in .env."""
        try:
            data = self.kite.generate_session(request_token, api_secret=self.api_secret)
            access_token = data.get("access_token")

            if not isinstance(access_token, str) or not access_token:
                raise ValueError("Response did not contain an access_token")

            # Store in .env. The file holds secrets: create it owner-only, and tighten an
            # existing one that other users could read.
            if not self.env_file.exists():
                self.env_file.touch(mode=0o600)
            if self.env_file.stat().st_mode & 0o077:
                self.env_file.chmod(0o600)

            set_key(str(self.env_file), "KITE_ACCESS_TOKEN", access_token)

            # Optionally store the public token too if needed for websockets
            public_token = data.get("public_token")
            if public_token:
                set_key(str(self.env_file), "KITE_PUBLIC_TOKEN", public_token)

            return access_token

        except TokenException as e:
            raise ValueError(f"Invalid request token or API secret: {e}") from e
        except NetworkException as e:
            raise ConnectionError(f"Network error during authentication: {e}") from e
