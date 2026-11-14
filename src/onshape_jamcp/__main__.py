"""Command-line entry point: ``onshape-jamcp`` / ``python -m onshape_jamcp``."""

import argparse
import logging
import os
import sys
from collections.abc import Sequence

from . import __version__
from .config import Settings
from .errors import ConfigError
from .server import build_server

EPILOG = """\
Settings are read from environment variables (flags override them):
  ONSHAPE_ACCESS_KEY / ONSHAPE_SECRET_KEY   API keys from https://dev-portal.onshape.com/keys
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
    return parser


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
    if not settings.has_credentials:
        logging.getLogger("onshape_jamcp").warning(
            "ONSHAPE_ACCESS_KEY / ONSHAPE_SECRET_KEY not set - tools that call Onshape will "
            "report an error until they are configured."
        )
    build_server(settings, log_level=level_name).run("stdio")  # type: ignore[arg-type]


if __name__ == "__main__":
    main()
