"""Build the Claude Desktop installer: a `.mcpb` bundle ("MCP bundle", a zip with a manifest).

    uv run python scripts/build_mcpb.py                 # -> dist/onshape-jamcp.mcpb
    uv run python scripts/build_mcpb.py --out bundle    # another output folder
    uv run python scripts/build_mcpb.py --print-manifest

The bundle is tiny: a manifest, a launcher and a pyproject.toml that pins ``onshape-jamcp`` to the
version of this checkout. Claude Desktop builds the environment with `uv` on first start (the
`uv` bundle type, manifest 0.4), so nothing compiled is shipped. The file keeps one stable name
so that ``releases/latest/download/onshape-jamcp.mcpb`` always points at the newest release.

The manifest's tool list is generated from the running server, so it cannot drift from the code.
The version comes from pyproject.toml; the PyPI release of that version must exist before anyone
installs the bundle (the release workflow builds it right next to the wheel).
"""

import argparse
import asyncio
import json
import re
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "mcpb"
BUNDLE_NAME = "onshape-jamcp.mcpb"
#: Files copied into the bundle as they are: (path in the repository, path inside the bundle).
STATIC_FILES = [
    (SRC / "src" / "server.py", "src/server.py"),
    (SRC / "icon.png", "icon.png"),
    (ROOT / "LICENSE", "LICENSE"),
]
#: Fixed timestamp so building twice gives the same bytes (a zip stores the time of each file).
ZIP_TIME = (2026, 1, 1, 0, 0, 0)
DESCRIPTION_LIMIT = 160


def project_version() -> str:
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'^version = "([^"]+)"', text, re.M)
    if not match:
        raise SystemExit("Could not find the version in pyproject.toml.")
    return match.group(1)


def first_sentence(text: str | None) -> str:
    """The first sentence of a tool description, short enough for the manifest."""
    flat = " ".join((text or "").split())
    match = re.match(r"(.+?[.!?])(?:\s|$)", flat)
    sentence = match.group(1) if match else flat
    if len(sentence) > DESCRIPTION_LIMIT:
        sentence = sentence[: DESCRIPTION_LIMIT - 1].rstrip() + "…"
    return sentence


async def list_tools() -> list[dict[str, str]]:
    from onshape_jamcp.config import Settings
    from onshape_jamcp.server import build_server

    server = build_server(Settings.from_env({}))
    return [
        {"name": t.name, "description": first_sentence(t.description)}
        for t in await server.list_tools()
    ]


def render_manifest(version: str, tools: list[dict[str, str]]) -> dict:
    text = (SRC / "manifest.template.json").read_text(encoding="utf-8")
    manifest = json.loads(text.replace("{{version}}", version))
    manifest["tools"] = tools
    return manifest


def render_pyproject(version: str) -> str:
    text = (SRC / "pyproject.template.toml").read_text(encoding="utf-8")
    return text.replace("{{version}}", version)


def bundle_files(version: str, tools: list[dict[str, str]]) -> list[tuple[str, bytes]]:
    """Every file of the bundle as (name inside the zip, content), in a fixed order."""
    manifest = json.dumps(render_manifest(version, tools), indent=2, ensure_ascii=False) + "\n"
    files = [
        ("manifest.json", manifest.encode("utf-8")),
        ("pyproject.toml", render_pyproject(version).encode("utf-8")),
    ]
    files += [(name, path.read_bytes()) for path, name in STATIC_FILES]
    return files


def write_bundle(target: Path, files: list[tuple[str, bytes]]) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as bundle:
        for name, data in files:
            info = zipfile.ZipInfo(name, ZIP_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            bundle.writestr(info, data)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", default="dist", help="output folder (default: dist)")
    parser.add_argument(
        "--print-manifest", action="store_true", help="print the manifest and write nothing"
    )
    args = parser.parse_args()
    version = project_version()
    tools = asyncio.run(list_tools())
    if args.print_manifest:
        print(json.dumps(render_manifest(version, tools), indent=2, ensure_ascii=False))
        return
    target = Path(args.out) / BUNDLE_NAME
    files = bundle_files(version, tools)
    write_bundle(target, files)
    size = target.stat().st_size
    print(f"Wrote {target} ({size / 1024:.1f} KiB): onshape-jamcp {version}, {len(tools)} tools.")
    print("Files:", ", ".join(name for name, _ in files))


if __name__ == "__main__":
    sys.exit(main())
