# onshape-jamcp

**J**ust **A**nother **MCP** server for [Onshape](https://www.onshape.com) CAD. It lets an AI
assistant (Claude, Cursor, VS Code, any [MCP](https://modelcontextprotocol.io) client) find your
documents, model parts, measure them, look at them and export them - in **millimeters by default**,
with a switch for **inches**, and a switch that decides whether the assistant gets to **see
screenshots** (which costs tokens) or not.

> **Status: alpha, verified live.** 140+ automated tests run against a simulated Onshape, and
> [`scripts/smoke_test.py`](scripts/smoke_test.py) passed **31/31 checks against a real Onshape
> account** (free plan, 2026-11-14): sketch → extrude → fillet → hole → edit → screenshot → STEP/STL
> export, with orientation and dimensions measured. Chamfer, assemblies with mates and paid-plan
> behavior are not yet verified live - see [what is verified](docs/development.md#verification-status-read-this-before-trusting-a-tool).
> Re-run the smoke test on your own account (see [below](#verify-it-works-against-your-account)).

## Highlights

| | |
|---|---|
| 📏 **Metric by default** | Every length you send or receive is in **mm**. Set `ONSHAPE_UNITS=imperial` for inches. Angles are always degrees. |
| 👁️ **Screenshot review toggle** | `ONSHAPE_SCREENSHOT_REVIEW=true` sends renders to the model so it can *see* its work (≈ 640 tokens per 800×600 image). `false` only saves them to disk - zero image tokens. Override per call with `review`. |
| 🔒 **Read-only mode** | `ONSHAPE_READ_ONLY=true` removes every tool that can modify a document. |
| 🧰 **37 focused tools** | Documents, sketch → extrude / revolve → patterns, mirror, shell, boolean, fillet/chamfer, edit parameters, measure, faces/edges, assemblies, screenshots, STEP/STL export - custom Feature Studio features such as Thread Creator, variables, configurations, part naming, plus escape hatches (raw feature JSON, FeatureScript, any REST endpoint). |
| 🧪 **Honest feedback loop** | Every modeling tool reports the feature's health (`OK`/`WARNING`/`ERROR`) so the model notices failures instead of piling more features on top. |

## Quick start

**1. Create API keys** at <https://dev-portal.onshape.com/keys> (tick *Read* and *Write*; *Delete*
only if you want `delete_feature`). Copy the secret immediately - Onshape shows it once.

**2. Add the server to your MCP client.** Claude Desktop (`claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "onshape": {
      "command": "uvx",
      "args": ["onshape-jamcp"],
      "env": {
        "ONSHAPE_ACCESS_KEY": "your-access-key",
        "ONSHAPE_SECRET_KEY": "your-secret-key",
        "ONSHAPE_UNITS": "metric",
        "ONSHAPE_SCREENSHOT_REVIEW": "true"
      }
    }
  }
}
```

Claude Code: `claude mcp add onshape -e ONSHAPE_ACCESS_KEY=... -e ONSHAPE_SECRET_KEY=... -- uvx onshape-jamcp`

> Until the package is published to PyPI, run it from a checkout instead: replace the command with
> `"command": "uv", "args": ["run", "--directory", "/path/to/onshape-jamcp", "onshape-jamcp"]`.
> More clients and details: [docs/getting-started.md](docs/getting-started.md).

**3. Ask for something:**

> *"Create a document called 'Bracket'. In a new Part Studio model a 60 × 40 mm plate, 8 mm thick,
> with four 5 mm holes 6 mm from the corners and 4 mm fillets on the vertical edges. Show me a
> screenshot when you're done."*

## Verify it works against your account

```bash
cp .env.example .env        # fill in the two keys
uv run python scripts/smoke_test.py            # on a free plan add --public (see below)
```

It creates a throw-away document (private; on a free plan Onshape only allows public ones, so pass
`--public`), models a plate, checks the dimensions/orientation/hole volume/fillets/edit/screenshot/
STEP+STL export numerically, then moves the document to Onshape's trash.
It prints `PASS`/`FAIL` per check. This is the step that confirms the assumptions a simulated
Onshape cannot (plane orientation, face normals, extrude direction).

## Settings

Set as environment variables (the `env` block of your MCP client) or CLI flags. The important ones:

| Variable | Default | Meaning |
|---|---|---|
| `ONSHAPE_ACCESS_KEY` / `ONSHAPE_SECRET_KEY` | - | **Required** (or OAuth, next row). API keys. |
| `ONSHAPE_OAUTH_CLIENT_ID` / `ONSHAPE_OAUTH_CLIENT_SECRET` | - | Alternative to API keys: your own Onshape OAuth app, then `onshape-jamcp auth login` once. See [docs/oauth.md](docs/oauth.md). |
| `ONSHAPE_UNITS` | `metric` | `metric` = millimeters, `imperial` = inches. Flag: `--units`. |
| `ONSHAPE_SCREENSHOT_REVIEW` | `true` | Send screenshots to the LLM (`true`) or only save them (`false`). Flag: `--[no-]screenshot-review`. |
| `ONSHAPE_READ_ONLY` | `false` | Hide all write tools. Flag: `--read-only`. |
| `ONSHAPE_BASE_URL` | `https://cad.onshape.com` | Enterprise: `https://yourcompany.onshape.com`. |

Everything else (image size, output folder, timeout, auth mode, API version, log level) is in
[docs/configuration.md](docs/configuration.md).

## Tools

| Group | Tools |
|---|---|
| Server | `get_settings`, `whoami` |
| Documents | `list_documents`, `get_document`, `create_document`, `create_element` |
| Inspect | `list_features`, `list_parts`, `get_part_properties`, `get_body_details`, `get_assembly`, `list_custom_features`, `list_variables`, `get_configuration`, `run_featurescript` |
| Model | `create_sketch`, `extrude`, `revolve`, `circular_pattern`, `linear_pattern`, `mirror`, `shell`, `boolean`, `fillet`, `chamfer`, `update_feature`, `delete_feature`, `add_feature`, `add_custom_feature` |
| Names, variables, configurations, assemblies | `rename`, `set_variable`, `set_configuration_input`, `insert_instance`, `add_mate` |
| Look & export | `capture_screenshot`, `export_model` |
| Escape hatch | `onshape_api_request` |

Full parameter reference (generated from the code): [docs/tools.md](docs/tools.md).

## Documentation

- [Getting started](docs/getting-started.md) - keys, installing in each client, first prompt
- [Configuration](docs/configuration.md) - every setting, units, precedence
- [OAuth sign-in](docs/oauth.md) - sign in with your Onshape account instead of API keys
- [How-to recipes](docs/how-to.md) - holes, fillets, edits, inches, export, read-only, advanced features
- [Threads and custom features](docs/threads.md) - ThreadCreator: bolt and nut example, every option, how to check it
- [Parametric modeling](docs/parametric.md) - names, variables, configurations (M10 / M18) and assemblies
- [Screenshots & token cost](docs/screenshots.md) - what the review toggle does and when to use it
- [Tool reference](docs/tools.md) - all tools and parameters
- [Troubleshooting](docs/troubleshooting.md)
- [Development](docs/development.md) - architecture, tests, adding a tool
- [Publishing](docs/publishing.md) - PyPI and Glama
- [Comparison](docs/comparison.md) - how this relates to the other Onshape MCP servers

## Security notes

- API keys stay in your MCP client's config / environment. They are never returned by any tool,
  never logged, and requests are HMAC-signed so the secret is not even sent over the wire.
  With OAuth the tokens live in a per-user file and are likewise never returned, logged or sent
  anywhere but Onshape.
- **Onshape's free plan makes every document public.** Do not model confidential work there.
- Write tools act as *you* on your real documents. Use `ONSHAPE_READ_ONLY=true` when you only want
  the assistant to look around, and rely on Onshape's version history as your undo.
- With screenshot review on, rendered images are sent to your LLM provider like any other message.

## Acknowledgements

Prior art that shaped this project: [hedless/onshape-mcp](https://github.com/hedless/onshape-mcp)
(MIT; the Onshape feature JSON conventions were cross-checked against it) and
[altendky/onshape-mcp](https://github.com/altendky/onshape-mcp). See
[docs/comparison.md](docs/comparison.md).

## License

[MIT](LICENSE). Not affiliated with or endorsed by PTC / Onshape.
