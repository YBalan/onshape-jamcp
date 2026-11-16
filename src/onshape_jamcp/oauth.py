"""OAuth 2 (authorization-code flow) for Onshape, for people who do not want long-lived API keys.

How it fits together:

* The user registers **their own** OAuth application in the Onshape developer portal (a client
  secret cannot be shipped in an open-source package) with the redirect URL
  ``http://localhost:<port>/callback``.
* ``onshape-jamcp auth login`` opens the browser, receives the one-time code on a throw-away
  listener bound to 127.0.0.1, exchanges it for tokens and stores them in a per-user file.
* While the server runs, :class:`OAuthSession` hands the REST client a valid access token. Access
  tokens last about an hour. Onshape's documentation says every refresh returns a *new* refresh
  token (checked live: it kept the same one), so whatever comes back is saved every time, and a
  pair another server process already saved is picked up instead of refreshing again.
  While the access token is still valid Onshape may hand back the *same* one and reports its true
  remaining lifetime in ``expires_in``, which is why the renewal margin is short.

Tokens are never logged, never shown to the model and never sent to a host outside
``*.onshape.com`` (the REST client enforces that).
"""

import asyncio
import html
import json
import logging
import os
import secrets
import tempfile
import time
import webbrowser
from collections.abc import Callable
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx

from . import __version__
from .config import Settings
from .errors import ConfigError, NetworkError

log = logging.getLogger("onshape_jamcp.oauth")

# Renew this long before the access token runs out (it is valid for about 60 minutes). Short on
# purpose: a refresh before expiry may return the very same token (checked live), so a long margin
# would turn the last minutes of every token's life into one token request per API request.
REFRESH_MARGIN = 30.0
LOGIN_TIMEOUT = 300.0
_DEFAULT_LIFETIME = 3600.0

NOT_SIGNED_IN = (
    "Not signed in to Onshape. Run `onshape-jamcp auth login` once in a terminal (with the same "
    "ONSHAPE_OAUTH_CLIENT_ID / ONSHAPE_OAUTH_CLIENT_SECRET), then restart the MCP client."
)


@dataclass(frozen=True)
class Tokens:
    access_token: str = field(repr=False)
    refresh_token: str = field(repr=False)
    expires_at: float  # epoch seconds
    client_id: str = ""

    def expires_soon(self, now: float, margin: float = REFRESH_MARGIN) -> bool:
        return self.expires_at - now < margin


class TokenStore:
    """The token file: one small JSON document, replaced atomically, readable only by the user
    where the operating system supports it (POSIX 0600; on Windows the per-user profile folder)."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> Tokens | None:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except (OSError, ValueError):
            raise self._unreadable() from None
        try:
            return Tokens(
                access_token=str(raw["access_token"]),
                refresh_token=str(raw["refresh_token"]),
                expires_at=float(raw["expires_at"]),
                client_id=str(raw.get("client_id") or ""),
            )
        except (KeyError, TypeError, ValueError):
            raise self._unreadable() from None

    def save(self, tokens: Tokens) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        document = json.dumps(
            {
                "access_token": tokens.access_token,
                "refresh_token": tokens.refresh_token,
                "expires_at": tokens.expires_at,
                "client_id": tokens.client_id,
            },
            indent=2,
        )
        # mkstemp creates the file private to the user; replace() swaps it in atomically so a
        # crash never leaves a half-written token file.
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".oauth-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(document)
            os.replace(tmp, self.path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise

    def delete(self) -> bool:
        try:
            self.path.unlink()
        except FileNotFoundError:
            return False
        return True

    def _unreadable(self) -> ConfigError:
        return ConfigError(
            f"The OAuth token file {self.path} is unreadable or damaged. Delete it and run "
            "`onshape-jamcp auth login` again."
        )


def authorize_url(settings: Settings, state: str) -> str:
    client_id, _ = settings.require_oauth_client()
    query = urlencode(
        {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": settings.oauth_redirect_uri,
            "state": state,
        }
    )
    return f"{settings.oauth_url}/oauth/authorize?{query}"


async def request_tokens(
    http: httpx.AsyncClient,
    settings: Settings,
    form: dict[str, str],
    *,
    now: float,
    keep_refresh_token: str | None = None,
) -> Tokens:
    """POST to Onshape's token endpoint (authorization code or refresh token) and parse it."""
    client_id, client_secret = settings.require_oauth_client()
    try:
        response = await http.post(
            f"{settings.oauth_url}/oauth/token",
            data={**form, "client_id": client_id, "client_secret": client_secret},
            headers={"Accept": "application/json"},
        )
    except httpx.TimeoutException:
        raise NetworkError("Timed out calling Onshape's OAuth server.") from None
    except httpx.HTTPError as exc:
        raise NetworkError(
            f"Could not reach Onshape's OAuth server at {settings.oauth_url}: {type(exc).__name__}"
        ) from None

    try:
        body: Any = response.json()
    except ValueError:
        body = None
    if response.status_code >= 400 or not isinstance(body, dict) or not body.get("access_token"):
        reason = _describe_rejection(response, body)
        if form.get("grant_type") == "refresh_token":
            raise ConfigError(
                f"Onshape rejected the stored sign-in ({reason}); it was probably revoked or "
                "expired. Run `onshape-jamcp auth login` again."
            )
        raise ConfigError(f"Onshape refused the OAuth sign-in ({reason}).")

    refresh_token = body.get("refresh_token") or keep_refresh_token
    if not refresh_token:
        raise ConfigError("Onshape's OAuth reply carried no refresh token; cannot stay signed in.")
    try:
        lifetime = float(body.get("expires_in") or _DEFAULT_LIFETIME)
    except (TypeError, ValueError):
        lifetime = _DEFAULT_LIFETIME
    return Tokens(
        access_token=str(body["access_token"]),
        refresh_token=str(refresh_token),
        expires_at=now + lifetime,
        client_id=client_id,
    )


def _describe_rejection(response: httpx.Response, body: Any) -> str:
    """HTTP status plus the OAuth error fields - never the request, which holds the secret."""
    parts = [f"HTTP {response.status_code}"]
    if isinstance(body, dict):
        for key in ("error", "error_description"):
            if body.get(key):
                parts.append(str(body[key])[:200])
    return ": ".join(parts)


class OAuthSession:
    """Gives the REST client a valid access token and renews it when needed."""

    def __init__(
        self,
        settings: Settings,
        http: httpx.AsyncClient,
        *,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._settings = settings
        self._http = http
        self._store = TokenStore(settings.oauth_token_file)
        self._clock = clock
        self._lock = asyncio.Lock()
        self._tokens: Tokens | None = None

    async def access_token(self) -> str:
        async with self._lock:
            tokens = self._tokens or self._load()
            if tokens.expires_soon(self._clock()):
                tokens = await self._renew(tokens)
            self._tokens = tokens
            return tokens.access_token

    async def renew_rejected(self, rejected: str) -> str:
        """Onshape answered 401 to ``rejected``: return a different token (renewing if needed)."""
        async with self._lock:
            tokens = self._tokens or self._load()
            if tokens.access_token == rejected:
                tokens = await self._renew(tokens)
            self._tokens = tokens
            return tokens.access_token

    def _load(self) -> Tokens:
        self._settings.require_oauth_client()
        tokens = self._store.load()
        if tokens is None:
            raise ConfigError(NOT_SIGNED_IN)
        if tokens.client_id and tokens.client_id != self._settings.oauth_client_id:
            raise ConfigError(
                "The stored sign-in belongs to a different OAuth application. Run "
                "`onshape-jamcp auth login` again."
            )
        return tokens

    async def _renew(self, known: Tokens) -> Tokens:
        now = self._clock()
        # Refresh tokens may rotate (Onshape documents it). If another server process already
        # renewed, the file holds a newer pair and ours could be refused: look before refreshing.
        current = self._load()
        if current != known and not current.expires_soon(now):
            return current
        tokens = await request_tokens(
            self._http,
            self._settings,
            {"grant_type": "refresh_token", "refresh_token": current.refresh_token},
            now=now,
            keep_refresh_token=current.refresh_token,
        )
        self._store.save(tokens)
        log.info("Refreshed the Onshape OAuth access token")
        return tokens


# --- interactive sign-in --------------------------------------------------------------------


class _CallbackServer(HTTPServer):
    # On Windows SO_REUSEADDR would let a second program share the port; elsewhere it only
    # lets a re-run bind straight after the previous sign-in closed.
    allow_reuse_address = os.name != "nt"

    def __init__(self, address: tuple[str, int], state: str) -> None:
        super().__init__(address, _CallbackHandler)
        self.state = state
        self.outcome: dict[str, str] | None = None
        self.stop = False  # set when the caller gives up (Ctrl+C), so the waiting thread ends


class _CallbackHandler(BaseHTTPRequestHandler):
    server: _CallbackServer

    def do_GET(self) -> None:
        parts = urlsplit(self.path)
        if parts.path != "/callback":
            self._reply(404, "Not found.")
            return
        query = {key: values[0] for key, values in parse_qs(parts.query).items()}
        if not secrets.compare_digest(query.get("state", "").encode(), self.server.state.encode()):
            # Not the answer to the request we made (a stray or forged request): keep waiting.
            self._reply(400, "Sign-in ignored: the state value did not match.")
        elif query.get("error"):
            detail = query["error"][:100]
            self.server.outcome = {"error": detail}
            self._reply(400, f"Onshape did not authorize the application ({detail}).")
        elif query.get("code"):
            self.server.outcome = {"code": query["code"]}
            self._reply(200, "Signed in to Onshape. You can close this tab.")
        else:
            self._reply(400, "Sign-in ignored: no authorization code.")

    def _reply(self, status: int, message: str) -> None:
        body = (
            "<!doctype html><meta charset=utf-8><title>onshape-jamcp</title>"
            f"<body style='font-family:sans-serif;margin:3em'><p>{html.escape(message)}</p>"
        ).encode()
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002 - stdlib signature
        return None  # the query string carries the one-time code; keep it out of any log


def _wait_for_callback(server: _CallbackServer, timeout: float) -> dict[str, str] | None:
    server.timeout = 0.5
    deadline = time.monotonic() + timeout
    try:
        while server.outcome is None and not server.stop and time.monotonic() < deadline:
            server.handle_request()
    except (OSError, ValueError):  # the socket was closed under us because the caller gave up
        return None
    return server.outcome


async def login(
    settings: Settings,
    *,
    open_browser: Callable[[str], object] = webbrowser.open,
    announce: Callable[[str], object] = print,
    transport: httpx.AsyncBaseTransport | None = None,
    timeout: float = LOGIN_TIMEOUT,
    clock: Callable[[], float] = time.time,
) -> Tokens:
    """Sign in through the browser and store the tokens. Used by ``onshape-jamcp auth login``."""
    settings.require_oauth_client()
    state = secrets.token_urlsafe(24)
    try:
        # 127.0.0.1 only: the listener is never reachable from the network.
        server = _CallbackServer(("127.0.0.1", settings.oauth_redirect_port), state)
    except OSError as exc:
        raise ConfigError(
            f"Cannot listen on 127.0.0.1:{settings.oauth_redirect_port} for the sign-in "
            f"callback ({exc.strerror or type(exc).__name__}). Free the port, or set "
            "ONSHAPE_OAUTH_PORT and change the application's redirect URL to match."
        ) from None
    try:
        url = authorize_url(settings, state)
        announce(f"Opening your browser to sign in to Onshape. If it does not open, visit:\n{url}")
        try:
            open_browser(url)
        except Exception:  # noqa: BLE001 - no browser available: the printed URL still works
            log.debug("Could not open a browser", exc_info=True)
        outcome = await asyncio.to_thread(_wait_for_callback, server, timeout)
    finally:
        server.stop = True
        server.server_close()

    if outcome is None:
        raise ConfigError(f"No sign-in arrived within {timeout:g} s; run the command again.")
    if "error" in outcome:
        raise ConfigError(f"Onshape did not authorize the application ({outcome['error']}).")

    async with httpx.AsyncClient(
        timeout=settings.timeout,
        transport=transport,
        follow_redirects=False,
        headers={"User-Agent": f"onshape-jamcp/{__version__}"},
    ) as http:
        tokens = await request_tokens(
            http,
            settings,
            {
                "grant_type": "authorization_code",
                "code": outcome["code"],
                "redirect_uri": settings.oauth_redirect_uri,
            },
            now=clock(),
        )
    TokenStore(settings.oauth_token_file).save(tokens)
    return tokens


def status(settings: Settings, *, clock: Callable[[], float] = time.time) -> dict[str, Any]:
    """What ``auth status`` shows: configuration and sign-in state, never a token."""
    info: dict[str, Any] = {
        "auth_mode": settings.auth_mode,
        "client_configured": settings.has_oauth_client,
        "redirect_url": settings.oauth_redirect_uri,
        "token_file": str(settings.oauth_token_file),
        "signed_in": False,
    }
    try:
        tokens = TokenStore(settings.oauth_token_file).load()
    except ConfigError as exc:
        info["problem"] = str(exc)
        return info
    if tokens is not None:
        info["signed_in"] = True
        info["access_token_valid_for_s"] = max(0, int(tokens.expires_at - clock()))
        info["same_client"] = not tokens.client_id or tokens.client_id == settings.oauth_client_id
    return info
