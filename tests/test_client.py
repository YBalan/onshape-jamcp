import base64
import hashlib
import hmac

import httpx
import pytest

from onshape_jamcp.client import OnshapeClient, encode_query, q, sign_request
from onshape_jamcp.config import Settings
from onshape_jamcp.errors import ConfigError, NetworkError, OnshapeError


def make_client(handler, **env):
    settings = Settings.from_env({"ONSHAPE_ACCESS_KEY": "AK", "ONSHAPE_SECRET_KEY": "SK", **env})
    return OnshapeClient(settings, transport=httpx.MockTransport(handler))


def test_signature_matches_independent_computation():
    """Re-derive the documented algorithm by hand: lower-cased 'method\\nnonce\\ndate\\n...'."""
    header = sign_request(
        "GET",
        "/api/v17/documents",
        "q=a&limit=5",
        access_key="AK",
        secret_key="SK",
        nonce="AbC123",
        date="Sat, 14 Nov 2026 12:00:00 GMT",
    )
    message = (
        "get\nabc123\nsat, 14 nov 2026 12:00:00 gmt\napplication/json\n"
        "/api/v17/documents\nq=a&limit=5\n"
    )
    digest = hmac.new(b"SK", message.encode(), hashlib.sha256).digest()
    assert header == f"On AK:HmacSHA256:{base64.b64encode(digest).decode()}"


def test_encode_query():
    assert encode_query(None) == ""
    assert encode_query({"a": None, "b": True, "c": 5}) == "b=true&c=5"
    assert encode_query({"ids": ["x", "y"]}) == "ids=x&ids=y"


def test_path_segments_are_escaped():
    assert q("a/b c") == "a%2Fb%20c"


async def test_hmac_request_headers_and_path():
    seen = {}

    def handler(request):
        seen["req"] = request
        return httpx.Response(200, json={"ok": True})

    client = make_client(handler)
    assert await client.get("/documents", {"q": "x y"}) == {"ok": True}
    req = seen["req"]
    assert req.url.path == "/api/v17/documents"
    auth = req.headers["authorization"]
    assert auth.startswith("On AK:HmacSHA256:")
    # The signature must cover exactly the path+query that went out on the wire.
    nonce, date = req.headers["on-nonce"], req.headers["date"]
    expected = sign_request(
        "GET",
        req.url.path,
        req.url.query.decode(),
        access_key="AK",
        secret_key="SK",
        nonce=nonce,
        date=date,
    )
    assert auth == expected
    assert len(nonce) == 25
    await client.aclose()


async def test_basic_auth_mode():
    seen = {}

    def handler(request):
        seen["auth"] = request.headers["authorization"]
        return httpx.Response(200, json={})

    client = make_client(handler, ONSHAPE_AUTH_MODE="basic")
    await client.get("/documents")
    assert seen["auth"] == "Basic " + base64.b64encode(b"AK:SK").decode()
    await client.aclose()


async def test_missing_credentials_fail_before_any_request():
    called = []
    client = OnshapeClient(
        Settings.from_env({}), transport=httpx.MockTransport(lambda r: called.append(r))
    )
    with pytest.raises(ConfigError, match="ONSHAPE_ACCESS_KEY"):
        await client.get("/documents")
    assert not called


@pytest.mark.parametrize(
    ("status", "needle"),
    [(401, "ONSHAPE_ACCESS_KEY"), (403, "scope"), (404, "IDs"), (400, "bad thing")],
)
async def test_error_mapping(status, needle):
    client = make_client(lambda r: httpx.Response(status, json={"message": "bad thing"}))
    with pytest.raises(OnshapeError) as exc:
        await client.get("/documents")
    assert exc.value.status == status
    assert needle in str(exc.value)
    await client.aclose()


async def test_rate_limit_retries_then_succeeds(monkeypatch):
    calls = []

    async def no_sleep(_):
        return None

    monkeypatch.setattr("onshape_jamcp.client.asyncio.sleep", no_sleep)

    def handler(request):
        calls.append(1)
        if len(calls) < 3:
            return httpx.Response(429, headers={"retry-after": "1"}, json={"message": "slow down"})
        return httpx.Response(200, json={"done": True})

    client = make_client(handler)
    assert await client.get("/documents") == {"done": True}
    assert len(calls) == 3
    await client.aclose()


async def test_rate_limit_gives_up(monkeypatch):
    async def no_sleep(_):
        return None

    monkeypatch.setattr("onshape_jamcp.client.asyncio.sleep", no_sleep)
    client = make_client(lambda r: httpx.Response(429, json={"message": "slow"}))
    with pytest.raises(OnshapeError) as exc:
        await client.get("/documents")
    assert exc.value.status == 429
    await client.aclose()


async def test_a_daily_limit_is_not_retried_and_says_when_it_resets(monkeypatch):
    """Seen live: Retry-After 80721 s on one endpoint while all others still worked."""
    calls = []

    async def no_sleep(_):
        raise AssertionError("must not wait for a daily limit")

    monkeypatch.setattr("onshape_jamcp.client.asyncio.sleep", no_sleep)

    def handler(request):
        calls.append(1)
        return httpx.Response(
            429, headers={"retry-after": "80721", "x-rate-limit-remaining": "0"}, json={}
        )

    client = make_client(handler)
    with pytest.raises(OnshapeError) as exc:
        await client.get("/partstudios/d/x/w/y/e/z/features")
    assert len(calls) == 1  # no pointless retries that would burn more calls
    assert exc.value.status == 429
    assert "22.4 h" in str(exc.value) and "THIS endpoint" in str(exc.value)
    await client.aclose()


async def test_redirect_to_onshape_host_is_resigned():
    paths = []

    def handler(request):
        paths.append((request.url.host, request.url.path, request.headers.get("authorization")))
        if request.url.path == "/api/v17/a":
            return httpx.Response(307, headers={"location": "https://cad.onshape.com/api/v17/b"})
        return httpx.Response(200, content=b"data")

    client = make_client(handler)
    assert await client.get("/a", expect="bytes") == b"data"
    assert [p[1] for p in paths] == ["/api/v17/a", "/api/v17/b"]
    assert all(p[2] and p[2].startswith("On AK:") for p in paths)
    assert paths[0][2] != paths[1][2]  # fresh signature for the new path
    await client.aclose()


async def test_redirect_to_foreign_host_never_gets_credentials():
    seen = []

    def handler(request):
        seen.append((request.url.host, request.headers.get("authorization")))
        if request.url.host == "cad.onshape.com":
            return httpx.Response(307, headers={"location": "https://storage.example.com/blob"})
        return httpx.Response(200, content=b"file")

    client = make_client(handler)
    assert await client.get("/a", expect="bytes") == b"file"
    assert seen[0][1] is not None
    assert seen[1] == ("storage.example.com", None)
    await client.aclose()


async def test_network_errors_become_friendly():
    def timeout(request):
        raise httpx.ReadTimeout("slow", request=request)

    client = make_client(timeout)
    with pytest.raises(NetworkError, match="Timed out"):
        await client.get("/documents")

    def refused(request):
        raise httpx.ConnectError("nope", request=request)

    client2 = make_client(refused)
    with pytest.raises(NetworkError, match="Could not reach Onshape"):
        await client2.get("/documents")


def test_resolve_path():
    client = make_client(lambda r: httpx.Response(200))
    assert client.resolve_path("/documents") == "/api/v17/documents"
    assert client.resolve_path("documents") == "/api/v17/documents"
    assert client.resolve_path("/api/v9/documents") == "/api/v9/documents"
