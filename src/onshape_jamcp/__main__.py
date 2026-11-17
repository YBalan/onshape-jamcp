"""Command-line entry point: ``onshape-jamcp`` / ``python -m onshape_jamcp``."""

import argparse
import asyncio
import logging
import os
import sys
import webbrowser
from collections.abc import Sequence

from . import __version__, oauth
from .config import Settings
from .errors import ConfigError, JamcpError
from .server import build_server

EPILOG = """\
Settings are read from environment variables (flags override them):
  ONSHAPE_ACCESS_KEY / ONSHAPE_SECRET_KEY   API keys from https://cad.onshape.com/user/developer
  ONSHAPE_OAUTH_CLIENT_ID / _SECRET   your own Onshape OAuth application, instead of API keys;
                                 then run: onshape-jamcp auth login   (see docs/oauth.md)
  ONSHAPE_UNITS                  metric (default, mm) | imperial (inches)
  ONSHAPE_SCREENSHOT_REVIEW      true (default) | false - send screenshots to the LLM or not
  ONSHAPE_READ_ONLY              true | false (default)
  ONSHAPE_BASE_URL               default https://cad.onshape.com (enterprise: https://<co>.onshape.com)
Full list: see docs/configuration.md
"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="onshape-jamcp",
        description="Just Another MCP server for Onshape CAD (stdio transport).",
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"onshape-jamcp {__version__}")
    parser.add_argument(
        "--units", choices=["metric", "imperial"], help="Length unit system (ONSHAPE_UNITS)."
    )
    parser.add_argument(
        "--screenshot-review",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Send screenshots to the LLM for review (ONSHAPE_SCREENSHOT_REVIEW).",
    )
    parser.add_argument(
        "--read-only",
        action="store_true",
        default=None,
        help="Disable every tool that modifies documents (ONSHAPE_READ_ONLY).",
    )
    commands = parser.add_subparsers(dest="command", metavar="[command]")
    auth = commands.add_parser(
        "auth",
        help="OAuth sign-in (instead of API keys): login, status, logout.",
        description="Sign in to Onshape with OAuth. Needs ONSHAPE_OAUTH_CLIENT_ID and "
        "ONSHAPE_OAUTH_CLIENT_SECRET (your own OAuth application); see docs/oauth.md.",
    )
    actions = auth.add_subparsers(dest="auth_command", required=True)
    login = actions.add_parser("login", help="Sign in through the browser and store the tokens.")
    login.add_argument(
        "--no-browser",
        action="store_true",
        help="Only print the sign-in URL; open it yourself (e.g. on a remote machine).",
    )
    actions.add_parser("status", help="Show the OAuth configuration and sign-in state.")
    actions.add_parser("logout", help="Delete the stored tokens.")
    return parser


def run_auth(args: argparse.Namespace, settings: Settings) -> int:
    """``onshape-jamcp auth ...``: returns the process exit code."""
    if args.auth_command == "status":
        for key, value in oauth.status(settings).items():
            print(f"{key}: {value}")
        return 0
    if args.auth_command == "logout":
        removed = oauth.TokenStore(settings.oauth_token_file).delete()
        print("Signed out: token file deleted." if removed else "Nothing to do: not signed in.")
        print("To revoke the application's access on Onshape's side, use your Onshape account.")
        return 0
    try:
        asyncio.run(
            oauth.login(
                settings,
                open_browser=(lambda url: None) if args.no_browser else webbrowser.open,
            )
        )
    except JamcpError as exc:
        print(f"onshape-jamcp: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("onshape-jamcp: sign-in cancelled.", file=sys.stderr)
        return 130
    print(f"Signed in. Tokens saved to {settings.oauth_token_file}")
    if settings.auth_mode != "oauth":
        print("API keys are also set and take precedence: set ONSHAPE_AUTH_MODE=oauth for OAuth.")
    return 0


def main(argv: Sequence[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    env = dict(os.environ)
    if args.units:
        env["ONSHAPE_UNITS"] = args.units
    if args.screenshot_review is not None:
        env["ONSHAPE_SCREENSHOT_REVIEW"] = str(args.screenshot_review).lower()
    if args.read_only:
        env["ONSHAPE_READ_ONLY"] = "true"

    # stdout is the MCP protocol channel - log to stderr only.
    level_name = env.get("ONSHAPE_LOG_LEVEL", "WARNING").strip().upper()
    if level_name not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
        level_name = "WARNING"
    logging.basicConfig(
        stream=sys.stderr,
        level=level_name,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        settings = Settings.from_env(env)
    except ConfigError as exc:
        print(f"onshape-jamcp: configuration error: {exc}", file=sys.stderr)
        raise SystemExit(2) from None
    if args.command == "auth":
        raise SystemExit(run_auth(args, settings))
    if not settings.has_credentials:
        logging.getLogger("onshape_jamcp").warning(
            "%s - tools that call Onshape will report an error until that is done.",
            "Not signed in (OAuth): run `onshape-jamcp auth login`"
            if settings.auth_mode == "oauth"
            else "ONSHAPE_ACCESS_KEY / ONSHAPE_SECRET_KEY not set",
        )
    build_server(settings, log_level=level_name).run("stdio")  # type: ignore[arg-type]


if __name__ == "__main__":
    main()
