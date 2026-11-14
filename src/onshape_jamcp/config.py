"""Runtime settings, read from environment variables (and CLI flags that map onto them).

Nothing here talks to the network. Credentials are *optional at startup* so that the server can
still start and list its tools (Glama, MCP Inspector and CI do this without any secrets); they are
checked the first time a tool actually needs the Onshape API.
"""

import tempfile
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

from .errors import ConfigError, InputError
from .units import METRIC, UnitSystem, parse_unit_system

DEFAULT_BASE_URL = "https://cad.onshape.com"
DEFAULT_API_VERSION = "v17"

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


def _normalize_base_url(raw: str) -> str:
    url = raw.strip().rstrip("/")
    if url.endswith("/api"):
        url = url[: -len("/api")]
    parts = urlsplit(url)
    local = parts.hostname in {"localhost", "127.0.0.1", "::1"}
    if parts.scheme != "https" and not (parts.scheme == "http" and local):
        raise ConfigError(
            f"ONSHAPE_BASE_URL must use https (got {raw!r}); API credentials are never sent "
            f"over plain http except to localhost."
        )
    if not parts.hostname or parts.path not in ("", "/"):
        raise ConfigError(
            f"ONSHAPE_BASE_URL must be a bare origin like {DEFAULT_BASE_URL} (got {raw!r})."
        )
    return f"{parts.scheme}://{parts.netloc}"


@dataclass(frozen=True)
class Settings:
    """All server settings. Immutable; build with :meth:`from_env`."""

    access_key: str | None = field(default=None, repr=False)
    secret_key: str | None = field(default=None, repr=False)
    base_url: str = DEFAULT_BASE_URL
    api_version: str = DEFAULT_API_VERSION
    auth_mode: str = "hmac"
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

        auth_mode = (env.get("ONSHAPE_AUTH_MODE") or "hmac").strip().lower()
        if auth_mode not in {"hmac", "basic"}:
            raise ConfigError(f"ONSHAPE_AUTH_MODE must be 'hmac' or 'basic', got {auth_mode!r}.")

        api_version = (env.get("ONSHAPE_API_VERSION") or DEFAULT_API_VERSION).strip()
        if not api_version.startswith("v") or not api_version[1:].isdigit():
            raise ConfigError(f"ONSHAPE_API_VERSION must look like 'v17', got {api_version!r}.")

        output_dir = env.get("ONSHAPE_OUTPUT_DIR")
        defaults = cls()
        return cls(
            access_key=(env.get("ONSHAPE_ACCESS_KEY") or "").strip() or None,
            secret_key=(env.get("ONSHAPE_SECRET_KEY") or "").strip() or None,
            base_url=_normalize_base_url(env.get("ONSHAPE_BASE_URL") or DEFAULT_BASE_URL),
            api_version=api_version,
            auth_mode=auth_mode,
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
    def has_credentials(self) -> bool:
        return bool(self.access_key and self.secret_key)

    def require_credentials(self) -> tuple[str, str]:
        if not (self.access_key and self.secret_key):
            raise ConfigError(
                "Onshape API credentials are not configured. Set ONSHAPE_ACCESS_KEY and "
                "ONSHAPE_SECRET_KEY in the MCP server's environment "
                "(create keys at https://dev-portal.onshape.com/keys)."
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
