"""Runtime settings, read from environment variables (and CLI flags that map onto them).

Nothing here talks to the network. Credentials are *optional at startup* so that the server can
still start and list its tools (Glama, MCP Inspector and CI do this without any secrets); they are
checked the first time a tool actually needs the Onshape API.
"""

import os
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from .errors import ConfigError, InputError
from .units import METRIC, UnitSystem, parse_unit_system

DEFAULT_BASE_URL = "https://cad.onshape.com"
DEFAULT_API_VERSION = "v17"
DEFAULT_OAUTH_URL = "https://oauth.onshape.com"
# The redirect URL registered with the Onshape OAuth application is
# http://localhost:<port>/callback; the port is fixed because Onshape matches it exactly.
DEFAULT_OAUTH_PORT = 18339
AUTH_MODES = ("hmac", "basic", "oauth")

_TRUE = {"1", "true", "yes", "on", "y", "t"}
_FALSE = {"0", "false", "no", "off", "n", "f"}

MIN_IMAGE_PX = 64
MAX_IMAGE_PX = 2000


def _parse_bool(name: str, raw: str | None, default: bool) -> bool:
    if raw is None or not raw.strip():
        return default
    value = raw.strip().lower()
    if value in _TRUE:
        return True
    if value in _FALSE:
        return False
    raise ConfigError(f"{name} must be true or false, got {raw!r}.")


def _parse_int(name: str, raw: str | None, default: int, lo: int, hi: int) -> int:
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw.strip())
    except ValueError:
        raise ConfigError(f"{name} must be an integer, got {raw!r}.") from None
    if not lo <= value <= hi:
        raise ConfigError(f"{name} must be between {lo} and {hi}, got {value}.")
    return value


def _parse_float(name: str, raw: str | None, default: float, lo: float, hi: float) -> float:
    if raw is None or not raw.strip():
        return default
    try:
        value = float(raw.strip())
    except ValueError:
        raise ConfigError(f"{name} must be a number, got {raw!r}.") from None
    if not lo <= value <= hi:
        raise ConfigError(f"{name} must be between {lo} and {hi}, got {value}.")
    return value


def _normalize_base_url(raw: str, name: str = "ONSHAPE_BASE_URL") -> str:
    url = raw.strip().rstrip("/")
    if url.endswith("/api"):
        url = url[: -len("/api")]
    parts = urlsplit(url)
    local = parts.hostname in {"localhost", "127.0.0.1", "::1"}
    if parts.scheme != "https" and not (parts.scheme == "http" and local):
        raise ConfigError(
            f"{name} must use https (got {raw!r}); credentials are never sent "
            f"over plain http except to localhost."
        )
    if not parts.hostname or parts.path not in ("", "/"):
        raise ConfigError(f"{name} must be a bare origin like {DEFAULT_BASE_URL} (got {raw!r}).")
    return f"{parts.scheme}://{parts.netloc}"


def default_token_file(env: Mapping[str, str] | None = None) -> Path:
    """Where the OAuth tokens are kept: the per-user config directory, never the repository."""
    env = os.environ if env is None else env
    if os.name == "nt":
        base = Path(env.get("APPDATA") or Path.home() / "AppData" / "Roaming")
    else:
        base = Path(env.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / "onshape-jamcp" / "oauth.json"


@dataclass(frozen=True)
class Settings:
    """All server settings. Immutable; build with :meth:`from_env`."""

    access_key: str | None = field(default=None, repr=False)
    secret_key: str | None = field(default=None, repr=False)
    base_url: str = DEFAULT_BASE_URL
    api_version: str = DEFAULT_API_VERSION
    auth_mode: str = "hmac"
    oauth_client_id: str | None = field(default=None, repr=False)
    oauth_client_secret: str | None = field(default=None, repr=False)
    oauth_url: str = DEFAULT_OAUTH_URL
    oauth_redirect_port: int = DEFAULT_OAUTH_PORT
    oauth_token_file: Path = field(default_factory=default_token_file)
    units: UnitSystem = METRIC
    screenshot_review: bool = True
    screenshot_width: int = 800
    screenshot_height: int = 600
    output_dir: Path = field(default_factory=lambda: Path(tempfile.gettempdir()) / "onshape-jamcp")
    read_only: bool = False
    timeout: float = 60.0

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> "Settings":
        try:
            units = parse_unit_system(env.get("ONSHAPE_UNITS") or "metric")
        except InputError as exc:
            raise ConfigError(f"ONSHAPE_UNITS: {exc}") from None

        access_key = (env.get("ONSHAPE_ACCESS_KEY") or "").strip() or None
        secret_key = (env.get("ONSHAPE_SECRET_KEY") or "").strip() or None
        oauth_client_id = (env.get("ONSHAPE_OAUTH_CLIENT_ID") or "").strip() or None
        oauth_client_secret = (env.get("ONSHAPE_OAUTH_CLIENT_SECRET") or "").strip() or None

        auth_mode = (env.get("ONSHAPE_AUTH_MODE") or "").strip().lower()
        if auth_mode and auth_mode not in AUTH_MODES:
            raise ConfigError(
                f"ONSHAPE_AUTH_MODE must be 'hmac', 'basic' or 'oauth', got {auth_mode!r}."
            )
        if not auth_mode:
            # API keys win when both are present; OAuth is chosen when it is all that is set.
            has_keys = bool(access_key and secret_key)
            has_oauth = bool(oauth_client_id and oauth_client_secret)
            auth_mode = "oauth" if has_oauth and not has_keys else "hmac"

        token_file = env.get("ONSHAPE_OAUTH_TOKEN_FILE")

        api_version = (env.get("ONSHAPE_API_VERSION") or DEFAULT_API_VERSION).strip()
        if not api_version.startswith("v") or not api_version[1:].isdigit():
            raise ConfigError(f"ONSHAPE_API_VERSION must look like 'v17', got {api_version!r}.")

        output_dir = env.get("ONSHAPE_OUTPUT_DIR")
        defaults = cls()
        return cls(
            access_key=access_key,
            secret_key=secret_key,
            base_url=_normalize_base_url(env.get("ONSHAPE_BASE_URL") or DEFAULT_BASE_URL),
            api_version=api_version,
            auth_mode=auth_mode,
            oauth_client_id=oauth_client_id,
            oauth_client_secret=oauth_client_secret,
            oauth_url=_normalize_base_url(
                env.get("ONSHAPE_OAUTH_URL") or DEFAULT_OAUTH_URL, "ONSHAPE_OAUTH_URL"
            ),
            oauth_redirect_port=_parse_int(
                "ONSHAPE_OAUTH_PORT",
                env.get("ONSHAPE_OAUTH_PORT"),
                DEFAULT_OAUTH_PORT,
                1024,
                65535,
            ),
            oauth_token_file=Path(token_file).expanduser()
            if token_file
            else default_token_file(env),
            units=units,
            screenshot_review=_parse_bool(
                "ONSHAPE_SCREENSHOT_REVIEW", env.get("ONSHAPE_SCREENSHOT_REVIEW"), True
            ),
            screenshot_width=_parse_int(
                "ONSHAPE_SCREENSHOT_WIDTH",
                env.get("ONSHAPE_SCREENSHOT_WIDTH"),
                800,
                MIN_IMAGE_PX,
                MAX_IMAGE_PX,
            ),
            screenshot_height=_parse_int(
                "ONSHAPE_SCREENSHOT_HEIGHT",
                env.get("ONSHAPE_SCREENSHOT_HEIGHT"),
                600,
                MIN_IMAGE_PX,
                MAX_IMAGE_PX,
            ),
            output_dir=Path(output_dir).expanduser() if output_dir else defaults.output_dir,
            read_only=_parse_bool("ONSHAPE_READ_ONLY", env.get("ONSHAPE_READ_ONLY"), False),
            timeout=_parse_float("ONSHAPE_TIMEOUT", env.get("ONSHAPE_TIMEOUT"), 60.0, 1.0, 600.0),
        )

    # --- helpers -------------------------------------------------------------------------------

    @property
    def has_oauth_client(self) -> bool:
        return bool(self.oauth_client_id and self.oauth_client_secret)

    @property
    def oauth_redirect_uri(self) -> str:
        """The redirect URL to register with the Onshape OAuth application."""
        return f"http://localhost:{self.oauth_redirect_port}/callback"

    @property
    def has_credentials(self) -> bool:
        """True when a request could be authenticated (OAuth: client set up AND signed in)."""
        if self.auth_mode == "oauth":
            return self.has_oauth_client and self.oauth_token_file.is_file()
        return bool(self.access_key and self.secret_key)

    @property
    def auth_help(self) -> str:
        """What to do about a rejected login, for the active authentication mode."""
        if self.auth_mode == "oauth":
            return (
                "run `onshape-jamcp auth login` to sign in again, and check "
                "ONSHAPE_OAUTH_CLIENT_ID / ONSHAPE_OAUTH_CLIENT_SECRET"
            )
        return (
            "check ONSHAPE_ACCESS_KEY / ONSHAPE_SECRET_KEY (the secret is exactly 48 characters - "
            "look for stray spaces or punctuation) and that the API key has not been revoked"
        )

    def require_oauth_client(self) -> tuple[str, str]:
        if not (self.oauth_client_id and self.oauth_client_secret):
            raise ConfigError(
                "OAuth is selected but the application is not configured. Set "
                "ONSHAPE_OAUTH_CLIENT_ID and ONSHAPE_OAUTH_CLIENT_SECRET (register an OAuth "
                "application in your Onshape developer settings, "
                "https://cad.onshape.com/user/developer, "
                f"with the redirect URL {self.oauth_redirect_uri}), then run "
                "`onshape-jamcp auth login`."
            )
        return self.oauth_client_id, self.oauth_client_secret

    def require_credentials(self) -> tuple[str, str]:
        if not (self.access_key and self.secret_key):
            raise ConfigError(
                "Onshape API credentials are not configured. Set ONSHAPE_ACCESS_KEY and "
                "ONSHAPE_SECRET_KEY in the MCP server's environment "
                "(create keys at https://cad.onshape.com/user/developer)."
            )
        return self.access_key, self.secret_key

    @property
    def screenshot_dir(self) -> Path:
        return self.output_dir / "screenshots"

    @property
    def export_dir(self) -> Path:
        return self.output_dir / "exports"

    def public_dict(self) -> dict[str, object]:
        """Settings safe to show the model: never includes the keys themselves."""
        return {
            "units": self.units.name,
            "length_unit": self.units.length_unit,
            "angle_unit": "deg",
            "screenshot_review": self.screenshot_review,
            "screenshot_size_px": [self.screenshot_width, self.screenshot_height],
            "read_only": self.read_only,
            "base_url": self.base_url,
            "api_version": self.api_version,
            "auth_mode": self.auth_mode,
            "credentials_configured": self.has_credentials,
            "output_dir": str(self.output_dir),
        }
