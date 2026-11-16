"""OAuth sign-in: settings, token storage, refresh, the REST client's bearer handling, the browser
login flow (with a simulated browser) and the ``auth`` command line."""

import os
import socket
import threading
import time
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from onshape_jamcp import oauth
from onshape_jamcp.__main__ import main
from onshape_jamcp.client import OnshapeClient
from onshape_jamcp.config import Settings
from onshape_jamcp.errors import ConfigError, NetworkError, OnshapeError
from onshape_jamcp.oauth import OAuthSession, Tokens, TokenStore

CLIENT_ID, CLIENT_SECRET = "client-id-123", "client-secret-456"


def settings_for(tmp_path, **env):
    return Settings.from_env(
        {
            "ONSHAPE_OAUTH_CLIENT_ID": CLIENT_ID,
            "ONSHAPE_OAUTH_CLIENT_SECRET": CLIENT_SECRET,
            "ONSHAPE_OAUTH_TOKEN_FILE": str(tmp_path / "oauth.json"),
            **env,
        }
    )


def tokens(*, access="AT1", refresh="RT1", valid_for=3600.0, client_id=CLIENT_ID):
    return Tokens(access, refresh, time.time() + valid_for, client_id)


def token_reply(access="AT2", refresh="RT2", expires_in=3600):
    body = {"access_token": access, "token_type": "bearer", "expires_in": expires_in}
    if refresh:
        body["refresh_token"] = refresh
    return httpx.Response(200, json=body)


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class TestSettings:
    def test_oauth_is_chosen_when_it_is_all_that_is_configured(self, tmp_path):
        assert settings_for(tmp_path).auth_mode == "oauth"

    def test_api_keys_win_unless_oauth_is_forced(self, tmp_path):
        keys = {"ONSHAPE_ACCESS_KEY": "AK", "ONSHAPE_SECRET_KEY": "SK"}
        assert settings_for(tmp_path, **keys).auth_mode == "hmac"
        assert settings_for(tmp_path, **keys, ONSHAPE_AUTH_MODE="oauth").auth_mode == "oauth"

    def test_nothing_configured_stays_hmac(self):
        assert Settings.from_env({}).auth_mode == "hmac"

    def test_a_half_configured_client_is_not_enough(self, tmp_path):
        s = Settings.from_env({"ONSHAPE_OAUTH_CLIENT_ID": "only-id"})
        assert s.auth_mode == "hmac" and not s.has_oauth_client

    def test_defaults(self):
        s = Settings.from_env({})
        assert s.oauth_url == "https://oauth.onshape.com"
        assert s.oauth_redirect_uri == "http://localhost:18339/callback"
        assert s.oauth_token_file.name == "oauth.json"
        assert "onshape-jamcp" in str(s.oauth_token_file)

    @pytest.mark.parametrize(
        "env",
        [
            {"ONSHAPE_OAUTH_PORT": "80"},
            {"ONSHAPE_OAUTH_PORT": "http"},
            {"ONSHAPE_OAUTH_URL": "http://oauth.onshape.com"},
            {"ONSHAPE_AUTH_MODE": "oauth2"},
        ],
    )
    def test_invalid_values_are_rejected(self, env):
        with pytest.raises(ConfigError):
            Settings.from_env(env)

    def test_signed_in_needs_the_client_and_the_token_file(self, tmp_path):
        s = settings_for(tmp_path)
        assert not s.has_credentials
        TokenStore(s.oauth_token_file).save(tokens())
        assert s.has_credentials
        assert not Settings.from_env(
            {"ONSHAPE_AUTH_MODE": "oauth", "ONSHAPE_OAUTH_TOKEN_FILE": str(s.oauth_token_file)}
        ).has_credentials

    def test_secrets_never_show_up(self, tmp_path):
        s = settings_for(tmp_path)
        dumped = str(s.public_dict()) + repr(s)
        assert CLIENT_SECRET not in dumped and CLIENT_ID not in dumped

    def test_a_missing_client_explains_how_to_register_one(self):
        with pytest.raises(ConfigError, match=r"localhost:18339/callback.*auth login"):
            Settings.from_env({}).require_oauth_client()


class TestTokenStore:
    def test_round_trip_and_delete(self, tmp_path):
        store = TokenStore(tmp_path / "deep" / "dir" / "t.json")
        assert store.load() is None
        original = tokens()
        store.save(original)
        assert store.load() == original
        assert store.delete() is True and store.delete() is False and store.load() is None

    @pytest.mark.skipif(os.name == "nt", reason="POSIX file modes")
    def test_the_file_is_private_to_the_user(self, tmp_path):
        store = TokenStore(tmp_path / "t.json")
        store.save(tokens())
        assert (store.path.stat().st_mode & 0o777) == 0o600

    def test_saving_replaces_the_file_and_leaves_no_temporary_files(self, tmp_path):
        store = TokenStore(tmp_path / "t.json")
        store.save(tokens(access="A"))
        store.save(tokens(access="B"))
        assert store.load().access_token == "B"
        assert [p.name for p in tmp_path.iterdir()] == ["t.json"]

    @pytest.mark.parametrize("content", ["not json", "[]", '{"access_token": "x"}'])
    def test_a_damaged_file_says_what_to_do(self, tmp_path, content):
        path = tmp_path / "t.json"
        path.write_text(content)
        with pytest.raises(ConfigError, match="auth login"):
            TokenStore(path).load()

    def test_repr_hides_the_tokens(self):
        assert "AT1" not in repr(tokens()) and "RT1" not in repr(tokens())


class TestTokenEndpoint:
    async def test_the_code_exchange_sends_the_documented_form(self, tmp_path):
        seen = {}

        def handler(request):
            seen["url"] = str(request.url)
            seen["form"] = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
            seen["type"] = request.headers["content-type"]
            return token_reply()

        s = settings_for(tmp_path)
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            got = await oauth.request_tokens(
                http,
                s,
                {"grant_type": "authorization_code", "code": "C", "redirect_uri": "R"},
                now=1000.0,
            )
        assert seen["url"] == "https://oauth.onshape.com/oauth/token"
        assert seen["type"] == "application/x-www-form-urlencoded"
        assert seen["form"] == {
            "grant_type": "authorization_code",
            "code": "C",
            "redirect_uri": "R",
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
        }
        assert (got.access_token, got.refresh_token, got.expires_at) == ("AT2", "RT2", 4600.0)
        assert got.client_id == CLIENT_ID

    async def test_a_refresh_reply_without_a_new_refresh_token_keeps_the_old_one(self, tmp_path):
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda r: token_reply(refresh=None))
        ) as http:
            got = await oauth.request_tokens(
                http,
                settings_for(tmp_path),
                {"grant_type": "refresh_token", "refresh_token": "OLD"},
                now=0.0,
                keep_refresh_token="OLD",
            )
        assert got.refresh_token == "OLD"

    async def test_a_rejection_is_explained_without_leaking_the_secret(self, tmp_path):
        reply = httpx.Response(400, json={"error": "invalid_grant", "error_description": "gone"})
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: reply)) as http:
            with pytest.raises(ConfigError) as exc:
                await oauth.request_tokens(
                    http,
                    settings_for(tmp_path),
                    {"grant_type": "refresh_token", "refresh_token": "OLD"},
                    now=0.0,
                )
        message = str(exc.value)
        assert "invalid_grant" in message and "auth login" in message
        assert CLIENT_SECRET not in message and "OLD" not in message

    async def test_an_unreachable_server_is_a_network_error(self, tmp_path):
        def handler(request):
            raise httpx.ConnectError("no route")

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            with pytest.raises(NetworkError, match="OAuth server"):
                await oauth.request_tokens(http, settings_for(tmp_path), {}, now=0.0)

    def test_the_authorize_url(self, tmp_path):
        url = oauth.authorize_url(settings_for(tmp_path), "STATE")
        parts = urlsplit(url)
        query = {k: v[0] for k, v in parse_qs(parts.query).items()}
        assert f"{parts.scheme}://{parts.netloc}{parts.path}" == (
            "https://oauth.onshape.com/oauth/authorize"
        )
        assert query == {
            "response_type": "code",
            "client_id": CLIENT_ID,
            "redirect_uri": "http://localhost:18339/callback",
            "state": "STATE",
        }
        assert CLIENT_SECRET not in url


class TestSession:
    def make(self, tmp_path, handler=None, **kw):
        s = settings_for(tmp_path)
        calls = []

        def default(request):
            calls.append(parse_qs(request.content.decode()))
            return token_reply()

        http = httpx.AsyncClient(transport=httpx.MockTransport(handler or default))
        return s, OAuthSession(s, http, **kw), calls, TokenStore(s.oauth_token_file)

    async def test_a_valid_token_is_used_without_any_request(self, tmp_path):
        s, session, calls, store = self.make(tmp_path)
        store.save(tokens(access="STORED"))
        assert await session.access_token() == "STORED" and not calls

    async def test_not_signed_in(self, tmp_path):
        _, session, calls, _ = self.make(tmp_path)
        with pytest.raises(ConfigError, match="auth login"):
            await session.access_token()
        assert not calls

    async def test_a_token_about_to_expire_is_refreshed_and_both_tokens_are_saved(self, tmp_path):
        _, session, calls, store = self.make(tmp_path)
        store.save(tokens(access="OLD", refresh="RT-OLD", valid_for=10))
        assert await session.access_token() == "AT2"
        assert calls[0]["grant_type"] == ["refresh_token"]
        assert calls[0]["refresh_token"] == ["RT-OLD"]
        saved = store.load()
        assert (saved.access_token, saved.refresh_token) == ("AT2", "RT2")
        await session.access_token()
        assert len(calls) == 1  # the new token is good for an hour

    async def test_a_refresh_that_returns_the_same_token_keeps_its_real_lifetime(self, tmp_path):
        # Seen live: while the old token is still valid Onshape returns it again, with the
        # remaining lifetime in expires_in and the same refresh token.
        def handler(request):
            return token_reply(access="SAME", refresh="RT-SAME", expires_in=20)

        _, session, _, store = self.make(tmp_path, handler=handler)
        store.save(tokens(access="SAME", refresh="RT-SAME", valid_for=20))
        assert await session.access_token() == "SAME"
        assert 15 < store.load().expires_at - time.time() <= 20  # not stretched to an hour

    async def test_parallel_requests_refresh_once(self, tmp_path):
        import asyncio

        _, session, calls, store = self.make(tmp_path)
        store.save(tokens(valid_for=0))
        got = await asyncio.gather(*(session.access_token() for _ in range(5)))
        assert got == ["AT2"] * 5 and len(calls) == 1

    async def test_a_newer_pair_saved_by_another_process_is_used_not_refreshed_again(
        self, tmp_path
    ):
        _, session, calls, store = self.make(tmp_path)
        store.save(tokens(access="MINE", refresh="RT-MINE", valid_for=10))
        # the first call loads "MINE" into memory; meanwhile another server refreshes the file
        session._tokens = store.load()
        store.save(tokens(access="THEIRS", refresh="RT-THEIRS", valid_for=3600))
        assert await session.renew_rejected("MINE") == "THEIRS"
        assert not calls  # our old refresh token would have been refused

    async def test_a_rejected_token_is_renewed(self, tmp_path):
        _, session, calls, store = self.make(tmp_path)
        store.save(tokens(access="REVOKED", valid_for=3600))
        assert await session.access_token() == "REVOKED"
        assert await session.renew_rejected("REVOKED") == "AT2" and len(calls) == 1

    async def test_a_revoked_refresh_token_asks_for_a_new_sign_in(self, tmp_path):
        reply = httpx.Response(400, json={"error": "invalid_grant"})
        _, session, _, store = self.make(tmp_path, handler=lambda r: reply)
        store.save(tokens(valid_for=0))
        with pytest.raises(ConfigError, match="auth login"):
            await session.access_token()

    async def test_tokens_of_another_application_are_refused(self, tmp_path):
        _, session, calls, store = self.make(tmp_path)
        store.save(tokens(client_id="someone-else"))
        with pytest.raises(ConfigError, match="different OAuth application"):
            await session.access_token()
        assert not calls


def api_handler(seen, *, rejected=("AT-OLD",), refreshed="AT-NEW"):
    """A fake Onshape: the API answers 401 to ``rejected`` tokens; the OAuth server renews."""

    def handler(request):
        host = request.url.host
        seen.append((host, request.headers.get("authorization")))
        if host == "oauth.onshape.com":
            return token_reply(access=refreshed, refresh="RT-NEW")
        if request.headers.get("authorization", "").removeprefix("Bearer ") in rejected:
            return httpx.Response(401, json={"message": "token expired"})
        return httpx.Response(200, json={"ok": True})

    return handler


class TestClient:
    def make(self, tmp_path, handler, **stored):
        s = settings_for(tmp_path)
        TokenStore(s.oauth_token_file).save(tokens(**stored))
        return OnshapeClient(s, transport=httpx.MockTransport(handler))

    async def test_requests_carry_the_bearer_token_and_no_api_key_headers(self, tmp_path):
        seen_requests = []

        def handler(request):
            seen_requests.append(request)
            return httpx.Response(200, json={"ok": True})

        client = self.make(tmp_path, handler, access="AT-GOOD")
        assert await client.get("/documents") == {"ok": True}
        headers = seen_requests[0].headers
        assert headers["authorization"] == "Bearer AT-GOOD"
        assert "on-nonce" not in headers
        await client.aclose()

    async def test_a_401_renews_the_token_and_retries_once(self, tmp_path):
        seen = []
        client = self.make(tmp_path, api_handler(seen), access="AT-OLD")
        assert await client.get("/documents") == {"ok": True}
        assert [auth for host, auth in seen if host == "cad.onshape.com"] == [
            "Bearer AT-OLD",
            "Bearer AT-NEW",
        ]
        assert TokenStore(client._settings.oauth_token_file).load().refresh_token == "RT-NEW"
        await client.aclose()

    async def test_a_second_401_is_reported_with_the_oauth_hint(self, tmp_path):
        seen = []
        client = self.make(
            tmp_path, api_handler(seen, rejected=("AT-OLD", "AT-NEW")), access="AT-OLD"
        )
        with pytest.raises(OnshapeError) as exc:
            await client.get("/documents")
        assert exc.value.status == 401 and "auth login" in str(exc.value)
        assert "ONSHAPE_ACCESS_KEY" not in str(exc.value)
        assert len([1 for host, _ in seen if host == "cad.onshape.com"]) == 2  # not a loop
        await client.aclose()

    async def test_an_expiring_token_is_renewed_before_the_request(self, tmp_path):
        seen = []
        client = self.make(tmp_path, api_handler(seen, rejected=()), access="AT-OLD", valid_for=10)
        await client.get("/documents")
        assert [h for h, _ in seen] == ["oauth.onshape.com", "cad.onshape.com"]
        assert seen[1][1] == "Bearer AT-NEW"
        await client.aclose()

    async def test_not_signed_in_fails_before_any_request(self, tmp_path):
        called = []
        client = OnshapeClient(
            settings_for(tmp_path), transport=httpx.MockTransport(lambda r: called.append(r))
        )
        with pytest.raises(ConfigError, match="auth login"):
            await client.get("/documents")
        assert not called

    async def test_the_token_is_not_sent_to_a_third_party_host(self, tmp_path):
        seen = []

        def handler(request):
            seen.append((request.url.host, request.headers.get("authorization")))
            if request.url.host == "cad.onshape.com":
                return httpx.Response(307, headers={"location": "https://files.example.net/x"})
            return httpx.Response(401 if seen[-1][1] is None else 200, content=b"data")

        client = self.make(tmp_path, handler, access="AT-GOOD")
        # the storage host answers 401 because it got no token - and must not trigger a renewal
        with pytest.raises(OnshapeError):
            await client.get("/documents/x", expect="bytes")
        assert seen == [("cad.onshape.com", "Bearer AT-GOOD"), ("files.example.net", None)]
        await client.aclose()


class FakeBrowser:
    """Plays the user: follows the sign-in URL by calling the local redirect URL."""

    def __init__(self, *queries):
        self.queries = queries  # each is extra query text; {state} is filled in
        self.urls = []
        self.statuses = []
        self.thread = None

    def __call__(self, url):
        self.urls.append(url)
        state = parse_qs(urlsplit(url).query)["state"][0]
        redirect = parse_qs(urlsplit(url).query)["redirect_uri"][0]

        def visit():
            for query in self.queries:
                reply = httpx.get(f"{redirect}?{query.format(state=state)}", trust_env=False)
                self.statuses.append(reply.status_code)

        self.thread = threading.Thread(target=visit)
        self.thread.start()


class TestLogin:
    def run(self, tmp_path, browser, handler=None, **kw):
        s = settings_for(tmp_path, ONSHAPE_OAUTH_PORT=str(free_port()))
        forms = []

        def default(request):
            forms.append({k: v[0] for k, v in parse_qs(request.content.decode()).items()})
            return token_reply(access="AT-LOGIN", refresh="RT-LOGIN")

        messages = []
        coro = oauth.login(
            s,
            open_browser=browser,
            announce=messages.append,
            transport=httpx.MockTransport(handler or default),
            **kw,
        )
        return s, coro, forms, messages

    async def test_the_whole_sign_in(self, tmp_path):
        browser = FakeBrowser("code=ONE-TIME&state={state}")
        s, coro, forms, messages = self.run(tmp_path, browser)
        got = await coro
        browser.thread.join()
        assert browser.statuses == [200]
        assert (got.access_token, got.refresh_token) == ("AT-LOGIN", "RT-LOGIN")
        assert forms == [
            {
                "grant_type": "authorization_code",
                "code": "ONE-TIME",
                "redirect_uri": s.oauth_redirect_uri,
                "client_id": CLIENT_ID,
                "client_secret": CLIENT_SECRET,
            }
        ]
        assert TokenStore(s.oauth_token_file).load() == got
        assert browser.urls[0] in messages[0]  # the URL is printed too, for headless machines
        assert CLIENT_SECRET not in messages[0] and "AT-LOGIN" not in messages[0]

    async def test_a_callback_with_the_wrong_state_is_ignored(self, tmp_path):
        browser = FakeBrowser("code=FORGED&state=wrong", "code=REAL&state={state}")
        s, coro, forms, _ = self.run(tmp_path, browser)
        await coro
        browser.thread.join()
        assert browser.statuses == [400, 200]
        assert forms[0]["code"] == "REAL"

    async def test_a_callback_without_a_code_is_ignored(self, tmp_path):
        browser = FakeBrowser("state={state}", "code=REAL&state={state}")
        s, coro, forms, _ = self.run(tmp_path, browser)
        await coro
        browser.thread.join()
        assert browser.statuses == [400, 200] and forms[0]["code"] == "REAL"

    async def test_a_denied_authorization_is_reported_and_stores_nothing(self, tmp_path):
        browser = FakeBrowser("error=access_denied&state={state}")
        s, coro, forms, _ = self.run(tmp_path, browser)
        with pytest.raises(ConfigError, match="access_denied"):
            await coro
        browser.thread.join()
        assert not forms and not s.oauth_token_file.exists()

    async def test_no_answer_times_out(self, tmp_path):
        s, coro, forms, _ = self.run(tmp_path, lambda url: None, timeout=0.6)
        with pytest.raises(ConfigError, match="No sign-in arrived"):
            await coro
        assert not forms

    async def test_a_rejected_code_stores_nothing(self, tmp_path):
        browser = FakeBrowser("code=BAD&state={state}")
        reply = httpx.Response(400, json={"error": "invalid_grant"})
        s, coro, _, _ = self.run(tmp_path, browser, handler=lambda r: reply)
        with pytest.raises(ConfigError, match="refused"):
            await coro
        browser.thread.join()
        assert not s.oauth_token_file.exists()

    async def test_a_busy_port_is_explained(self, tmp_path):
        with socket.socket() as blocker:
            blocker.bind(("127.0.0.1", 0))
            blocker.listen()
            s = settings_for(tmp_path, ONSHAPE_OAUTH_PORT=str(blocker.getsockname()[1]))
            with pytest.raises(ConfigError, match="ONSHAPE_OAUTH_PORT"):
                await oauth.login(s, open_browser=lambda url: None, announce=lambda m: None)

    async def test_a_browser_that_cannot_open_does_not_stop_the_sign_in(self, tmp_path):
        def broken(url):
            raise RuntimeError("no browser")

        s, coro, _, _ = self.run(tmp_path, broken, timeout=0.6)
        with pytest.raises(ConfigError, match="No sign-in arrived"):
            await coro  # got as far as waiting: the printed URL is the fallback

    async def test_the_client_must_be_configured(self):
        with pytest.raises(ConfigError, match="ONSHAPE_OAUTH_CLIENT_ID"):
            await oauth.login(Settings.from_env({}), open_browser=lambda url: None)


class TestStatusAndCli:
    def test_status_never_shows_a_token(self, tmp_path):
        s = settings_for(tmp_path)
        assert oauth.status(s)["signed_in"] is False
        TokenStore(s.oauth_token_file).save(tokens(access="SECRET-AT", refresh="SECRET-RT"))
        info = oauth.status(s)
        assert info["signed_in"] is True and info["same_client"] is True
        assert 3500 < info["access_token_valid_for_s"] <= 3600
        assert "SECRET-AT" not in str(info) and "SECRET-RT" not in str(info)

    def test_status_reports_a_damaged_file(self, tmp_path):
        s = settings_for(tmp_path)
        s.oauth_token_file.write_text("{")
        assert "unreadable" in oauth.status(s)["problem"]

    def cli_env(self, monkeypatch, tmp_path):
        for key, value in {
            "ONSHAPE_OAUTH_CLIENT_ID": CLIENT_ID,
            "ONSHAPE_OAUTH_CLIENT_SECRET": CLIENT_SECRET,
            "ONSHAPE_OAUTH_TOKEN_FILE": str(tmp_path / "oauth.json"),
        }.items():
            monkeypatch.setenv(key, value)
        for key in ("ONSHAPE_ACCESS_KEY", "ONSHAPE_SECRET_KEY", "ONSHAPE_AUTH_MODE"):
            monkeypatch.delenv(key, raising=False)

    def test_auth_status_and_logout(self, monkeypatch, tmp_path, capsys):
        self.cli_env(monkeypatch, tmp_path)
        TokenStore(tmp_path / "oauth.json").save(tokens(access="SECRET-AT"))
        with pytest.raises(SystemExit) as stop:
            main(["auth", "status"])
        out = capsys.readouterr().out
        assert stop.value.code == 0 and "signed_in: True" in out and "SECRET-AT" not in out
        with pytest.raises(SystemExit) as stop:
            main(["auth", "logout"])
        assert stop.value.code == 0 and not (tmp_path / "oauth.json").exists()
        assert "Signed out" in capsys.readouterr().out
        with pytest.raises(SystemExit):
            main(["auth", "logout"])
        assert "not signed in" in capsys.readouterr().out

    def test_auth_login_without_a_client_exits_with_an_error(self, monkeypatch, tmp_path, capsys):
        for key in ("ONSHAPE_OAUTH_CLIENT_ID", "ONSHAPE_OAUTH_CLIENT_SECRET"):
            monkeypatch.delenv(key, raising=False)
        with pytest.raises(SystemExit) as stop:
            main(["auth", "login", "--no-browser"])
        assert stop.value.code == 1
        assert "ONSHAPE_OAUTH_CLIENT_ID" in capsys.readouterr().err

    def test_auth_login_reports_success(self, monkeypatch, tmp_path, capsys):
        self.cli_env(monkeypatch, tmp_path)

        async def fake_login(settings, **kw):
            assert kw["open_browser"]("https://example.test") is None  # --no-browser
            return tokens()

        monkeypatch.setattr("onshape_jamcp.__main__.oauth.login", fake_login)
        with pytest.raises(SystemExit) as stop:
            main(["auth", "login", "--no-browser"])
        assert stop.value.code == 0 and "Signed in" in capsys.readouterr().out

    def test_the_server_command_is_unchanged(self, monkeypatch):
        started = []

        class Fake:
            def run(self, transport):
                started.append(transport)

        monkeypatch.setattr("onshape_jamcp.__main__.build_server", lambda *a, **k: Fake())
        monkeypatch.setenv("ONSHAPE_ACCESS_KEY", "AK")
        monkeypatch.setenv("ONSHAPE_SECRET_KEY", "SK")
        main([])
        assert started == ["stdio"]
