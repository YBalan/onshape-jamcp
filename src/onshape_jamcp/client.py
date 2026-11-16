"""Async Onshape REST client with API-key authentication.

Two authentication modes are supported (``ONSHAPE_AUTH_MODE``):

* ``hmac`` (default): the request is signed with the secret key, which is never transmitted.
* ``basic``: HTTP Basic with ``access:secret``. Onshape documents this as lowest-security and
  "for local testing only"; it exists as a fallback if signing misbehaves.

Redirects (Onshape answers some downloads with ``307``) are followed manually so that credentials
are only ever attached to ``*.onshape.com`` / the configured base host.
"""

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import secrets
import string
from email.utils import formatdate
from typing import Any, Literal
from urllib.parse import quote, urlencode, urljoin, urlsplit

import httpx

from . import __version__
from .config import Settings
from .errors import NetworkError, OnshapeError

log = logging.getLogger("onshape_jamcp.client")

_NONCE_ALPHABET = string.ascii_letters + string.digits
_JSON_ACCEPT = "application/json;charset=UTF-8; qs=0.09"
_MAX_REDIRECTS = 5
_MAX_RATE_LIMIT_RETRIES = 2
# A 429 that asks for longer than this is a per-endpoint DAILY limit (seen live: Retry-After
# 80721 s on GET .../features while every other endpoint was fine). Retrying cannot help.
_MAX_RETRYABLE_WAIT = 60.0

Expect = Literal["json", "bytes", "text"]


def q(value: object) -> str:
    """Percent-encode one URL path segment (IDs are usually safe, but never assume)."""
    return quote(str(value), safe="")


def sign_request(
    method: str,
    path: str,
    query: str,
    *,
    access_key: str,
    secret_key: str,
    nonce: str,
    date: str,
    content_type: str = "application/json",
) -> str:
    """Return the ``Authorization`` header value for Onshape's HMAC-SHA256 API-key scheme.

    The signed message is ``method\\nnonce\\ndate\\ncontent-type\\npath\\nquery\\n``, lower-cased
    as a whole. ``path`` and ``query`` must be exactly what is sent on the wire.
    """
    message = f"{method}\n{nonce}\n{date}\n{content_type}\n{path}\n{query}\n".lower()
    digest = hmac.new(secret_key.encode(), message.encode(), hashlib.sha256).digest()
    return f"On {access_key}:HmacSHA256:{base64.b64encode(digest).decode()}"


def encode_query(params: dict[str, Any] | None) -> str:
    """URL-encode query parameters; ``None`` is dropped and booleans become ``true``/``false``."""
    if not params:
        return ""
    cleaned: list[tuple[str, str]] = []
    for key, value in params.items():
        if value is None:
            continue
        values = value if isinstance(value, list | tuple) else [value]
        for item in values:
            cleaned.append((key, str(item).lower() if isinstance(item, bool) else str(item)))
    return urlencode(cleaned)


def _retry_after_seconds(response: httpx.Response) -> float:
    try:
        return float(response.headers.get("retry-after", "1") or 1)
    except ValueError:
        return 1.0


def _rate_limit_hint(response: httpx.Response) -> str:
    wait = _retry_after_seconds(response)
    if wait > _MAX_RETRYABLE_WAIT:
        hours = wait / 3600
        return (
            f"Onshape's daily limit for THIS endpoint is used up and resets in about "
            f"{hours:.1f} h; other endpoints still work. Use fewer calls of this kind "
            f"(for example list_features / feature status reads) and try again later"
        )
    return _HINTS[429]


def _error_message(response: httpx.Response) -> str:
    try:
        body = response.json()
    except (ValueError, json.JSONDecodeError):
        text = response.text.strip()
        return text[:500] or response.reason_phrase
    if isinstance(body, dict):
        for key in ("message", "error", "errorMessage", "error_description"):
            if body.get(key):
                return str(body[key])[:500]
    return str(body)[:500]


_HINTS = {
    401: "check ONSHAPE_ACCESS_KEY / ONSHAPE_SECRET_KEY (the secret is exactly 48 characters - "
    "look for stray spaces or punctuation) and that the API key has not been revoked",
    403: "the API key lacks the required scope (read/write/delete) or you lack permission on "
    "this document",
    404: "check the document, workspace and element IDs",
    429: "Onshape API rate limit reached - wait a bit, or reduce the number of calls",
}


class OnshapeClient:
    """Thin authenticated wrapper around ``httpx.AsyncClient``."""

    def __init__(
        self, settings: Settings, *, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        self._settings = settings
        self._base = urlsplit(settings.base_url)
        self._http = httpx.AsyncClient(
            timeout=settings.timeout,
            transport=transport,
            follow_redirects=False,
            headers={"User-Agent": f"onshape-jamcp/{__version__}"},
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    # --- public API ----------------------------------------------------------------------------

    @property
    def api_root(self) -> str:
        return f"/api/{self._settings.api_version}"

    def resolve_path(self, path: str) -> str:
        """Prefix ``/api/vNN`` unless the caller already supplied an ``/api/...`` path."""
        if not path.startswith("/"):
            path = "/" + path
        return path if path.startswith("/api/") else self.api_root + path

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        body: Any = None,
        expect: Expect = "json",
    ) -> Any:
        """Send one request and return JSON / bytes / text according to ``expect``."""
        access_key, secret_key = self._settings.require_credentials()
        method = method.upper()
        url = f"{self._settings.base_url}{self.resolve_path(path)}"
        query = encode_query(params)
        if query:
            url = f"{url}?{query}"
        payload = None if body is None else json.dumps(body)

        for redirect in range(_MAX_REDIRECTS + 1):
            response = await self._send_with_retry(
                method, url, payload, expect, access_key, secret_key
            )
            if response.status_code in (301, 302, 303, 307, 308) and response.headers.get(
                "location"
            ):
                if redirect == _MAX_REDIRECTS:
                    raise OnshapeError(response.status_code, "Too many redirects")
                url = urljoin(url, response.headers["location"])
                if response.status_code in (301, 302, 303):
                    method, payload = "GET", None
                continue
            return self._parse(response, expect)
        raise OnshapeError(0, "Too many redirects")  # pragma: no cover

    async def get(self, path: str, params: dict[str, Any] | None = None, **kw: Any) -> Any:
        return await self.request("GET", path, params=params, **kw)

    async def post(
        self, path: str, body: Any = None, params: dict[str, Any] | None = None, **kw: Any
    ) -> Any:
        return await self.request("POST", path, params=params, body=body, **kw)

    async def delete(self, path: str, params: dict[str, Any] | None = None, **kw: Any) -> Any:
        return await self.request("DELETE", path, params=params, **kw)

    # --- internals -----------------------------------------------------------------------------

    def _trusted(self, host: str | None) -> bool:
        host = (host or "").lower()
        return host == (self._base.hostname or "") or host.endswith(".onshape.com")

    def _headers(
        self,
        method: str,
        url: str,
        payload: str | None,
        expect: Expect,
        access_key: str,
        secret_key: str,
    ) -> dict[str, str]:
        parts = urlsplit(url)
        headers = {"Accept": _JSON_ACCEPT if expect == "json" else "*/*"}
        if payload is not None or method != "GET":
            headers["Content-Type"] = "application/json"
        if not self._trusted(parts.hostname):
            return headers  # never leak credentials to a third-party host (e.g. object storage)
        if self._settings.auth_mode == "basic":
            token = base64.b64encode(f"{access_key}:{secret_key}".encode()).decode()
            headers["Authorization"] = f"Basic {token}"
            return headers
        nonce = "".join(secrets.choice(_NONCE_ALPHABET) for _ in range(25))
        date = formatdate(usegmt=True)
        headers.update(
            {
                "Content-Type": "application/json",
                "Date": date,
                "On-Nonce": nonce,
                "Authorization": sign_request(
                    method,
                    parts.path,
                    parts.query,
                    access_key=access_key,
                    secret_key=secret_key,
                    nonce=nonce,
                    date=date,
                ),
            }
        )
        return headers

    async def _send_with_retry(
        self,
        method: str,
        url: str,
        payload: str | None,
        expect: Expect,
        access_key: str,
        secret_key: str,
    ) -> httpx.Response:
        for attempt in range(_MAX_RATE_LIMIT_RETRIES + 1):
            headers = self._headers(method, url, payload, expect, access_key, secret_key)
            try:
                log.debug("%s %s", method, urlsplit(url).path)
                response = await self._http.request(
                    method,
                    url,
                    content=payload.encode() if payload is not None else None,
                    headers=headers,
                )
            except httpx.TimeoutException:
                raise NetworkError(
                    f"Timed out after {self._settings.timeout:g}s calling Onshape "
                    f"({method} {urlsplit(url).path}). Raise ONSHAPE_TIMEOUT if the model is large."
                ) from None
            except httpx.HTTPError as exc:
                raise NetworkError(
                    f"Could not reach Onshape at {self._settings.base_url}: {type(exc).__name__}"
                ) from None
            if response.status_code == 429 and attempt < _MAX_RATE_LIMIT_RETRIES:
                wait = _retry_after_seconds(response)
                if wait > _MAX_RETRYABLE_WAIT:
                    return response  # a daily limit: fail now, with a clear message
                delay = min(wait, 15.0)
                log.warning("Rate limited by Onshape; retrying in %.1fs", delay)
                await asyncio.sleep(delay)
                continue
            return response
        raise AssertionError("unreachable")  # pragma: no cover

    def _parse(self, response: httpx.Response, expect: Expect) -> Any:
        if response.status_code >= 400:
            try:
                parsed: Any = response.json()
            except ValueError:
                parsed = response.text[:2000]
            raise OnshapeError(
                response.status_code,
                _error_message(response),
                parsed,
                hint=_rate_limit_hint(response)
                if response.status_code == 429
                else _HINTS.get(response.status_code),
            )
        if expect == "bytes":
            return response.content
        if expect == "text":
            return response.text
        if not response.content:
            return {}
        try:
            return response.json()
        except ValueError:
            raise OnshapeError(
                response.status_code, "Onshape returned a non-JSON body", response.text[:500]
            ) from None
