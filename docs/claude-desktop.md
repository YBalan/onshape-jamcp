# Install in Claude Desktop (one click)

`onshape-jamcp.mcpb` is an installer for Claude Desktop (a "desktop extension" / MCP bundle). You
download one file, open it, paste your Onshape API keys into a form and you are done - no
`claude_desktop_config.json` to edit.

## Install

1. **Install `uv` once** (<https://docs.astral.sh/uv/getting-started/installation/>). The extension is
   started with it: `uv` downloads Python and the `onshape-jamcp` package on first start, so you do
   not need to install either yourself.
2. **Create Onshape API keys** at <https://dev-portal.onshape.com/keys> (tick *Read* and *Write*;
   *Delete* only if you want `delete_feature`). Copy the secret immediately - Onshape shows it once.
   See [getting started](getting-started.md#2-create-onshape-api-keys) for the details.
3. **Download the installer:**
   [onshape-jamcp.mcpb](https://github.com/YBalan/onshape-jamcp/releases/latest/download/onshape-jamcp.mcpb)
   (always the newest release).
4. **Open it.** Double-click the file, or drag it onto Claude Desktop's *Settings → Extensions* page.
   Claude Desktop shows what the extension is and asks for the settings below. Click **Install**.
5. **Ask for something.** The first start takes a minute (dependencies are downloaded once and then
   cached). Try: *"Who am I on Onshape?"* and then a part, see [examples](../README.md#examples).

## What the form asks for

| Field | Needed | Becomes | Notes |
|---|---|---|---|
| Onshape API access key | yes | `ONSHAPE_ACCESS_KEY` | Claude Desktop keeps it in your operating system's secure storage. |
| Onshape API secret key | yes | `ONSHAPE_SECRET_KEY` | Exactly as Onshape showed it, no spaces. |
| Onshape address | no | `ONSHAPE_BASE_URL` | Leave `https://cad.onshape.com`; an enterprise account uses its own address. |
| Units | no | `ONSHAPE_UNITS` | `metric` (millimeters, default) or `imperial` (inches). |
| Let Claude look at screenshots | no | `ONSHAPE_SCREENSHOT_REVIEW` | On by default: renders are sent to Claude (about 640 tokens each). Off: only saved. |
| Read-only mode | no | `ONSHAPE_READ_ONLY` | On removes every tool that changes a document. |
| Folder for screenshots and exports | no | `ONSHAPE_OUTPUT_DIR` | Default `Documents/onshape-jamcp`. |

Change them later under *Settings → Extensions → Onshape CAD (jamcp)*. Every setting is explained in
[configuration](configuration.md).

## Not in the installer

- **OAuth sign-in** needs a one-time `onshape-jamcp auth login` in a terminal, so it is configured
  the manual way: see [OAuth sign-in](oauth.md).
- **Other MCP clients** (Claude Code, Cursor, VS Code) do not read `.mcpb` files; use the JSON
  configuration in [getting started](getting-started.md#3-install-in-your-mcp-client).

## If it does not start

| Symptom | Cause and fix |
|---|---|
| The extension shows an error or `uv` is "not found" | Install `uv` (step 1) and restart Claude Desktop so it sees the new `PATH`. |
| It times out the first time | The first start downloads Python and the packages. Wait a minute and restart the extension (or Claude Desktop); the second start is fast. |
| Tools answer "401" or "anonymous" | The keys are wrong: no trailing space or full stop, secret exactly 48 characters. Fix them under *Settings → Extensions*. See [troubleshooting](troubleshooting.md). |
| Nothing helps | Read the extension's log (`mcp-server-*.log` in Claude Desktop's logs folder: `%APPDATA%\Claude\logs` on Windows, `~/Library/Logs/Claude` on macOS). Or use the [manual JSON configuration](getting-started.md#3-install-in-your-mcp-client) instead. |

## How it works

The `.mcpb` file is a small zip (about 17 KB): a manifest, a launcher and a `pyproject.toml` that
says "install `onshape-jamcp` of exactly this version from PyPI". It is a **`uv` bundle** (manifest
version 0.4): Claude Desktop runs `uv run src/server.py` in the extension's folder, `uv` builds the
environment from that `pyproject.toml`, and the settings arrive as the same `ONSHAPE_*` environment
variables as in any other client. Nothing compiled is shipped, so one file works on Windows, macOS
and Linux. The tool list in the manifest is generated from the running server, so it cannot drift
from the code.

Your keys go only to Claude Desktop's secure storage and from there to the server process, which
sends them to Onshape and nowhere else (see the README's security notes).

## For maintainers

```bash
uv run python scripts/build_mcpb.py            # -> dist/onshape-jamcp.mcpb (the same bytes every time)
uv run python scripts/build_mcpb.py --print-manifest
```

The release workflow builds the bundle next to the wheel and attaches it to the GitHub release under
the fixed name `onshape-jamcp.mcpb`, which is why the link above always serves the newest one. The
bundle pins the version it was built from, so the PyPI release must exist before anyone installs it
(the workflow publishes to PyPI first). `tests/test_mcpb.py` checks the manifest (required fields,
version, tool list, every setting mapped to a real `ONSHAPE_*` variable, no key shipped). To check it
against Anthropic's own validator, unpack the file and run `npx @anthropic-ai/mcpb validate
manifest.json` in that folder.

Verified live (2026-11-17, Windows): the unpacked bundle was started exactly as Claude Desktop does it
(manifest command, arguments and environment with the form's values) through `uv`; the environment
was built from PyPI, the server answered, and units, screenshot review, read-only, output folder and
credentials all arrived as set. **Not verified:** the installation dialog of Claude Desktop itself
(it needs a Claude Desktop that supports `uv` bundles).
