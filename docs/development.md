# Development

## Setup

```bash
uv sync                                   # .venv + the package + dev tools
uv run pytest                             # all tests (~1.5 min; stdio tests start real processes)
uv run pytest --deselect tests/test_stdio.py   # fast loop (~5 s)
uv run ruff check src tests scripts && uv run ruff format src tests scripts
uv run python scripts/gen_tools_doc.py    # regenerate docs/tools.md after changing a tool
```

Requires Python ≥ 3.10 and `mcp` 2.x (the SDK renamed `FastMCP` to `MCPServer` in 2.0, and only
`ToolError` messages reach the model - see below).

## Layout

```
src/onshape_jamcp/
  __main__.py     CLI: flags -> env -> Settings -> build_server().run("stdio")
  config.py       Settings.from_env(): every ONSHAPE_* variable, validation, public_dict()
  units.py        UnitSystem (metric/imperial): conversions + Onshape expression strings
  client.py       OnshapeClient: HMAC signing, retries on 429, safe manual redirects, errors
  app.py          App: lazy client + caches (default workspace, plane IDs, element types)
  fs.py           FeatureScript helpers: unwrap() typed results, plane-ID script
  sketch.py       Shape models (pydantic) -> BTMSketch-151 JSON
  features.py     extrude / fillet / chamfer JSON builders, feature-response summary
  views.py        named camera views for screenshots
  server.py       build_server(): MCPServer + instructions + lifespan
  tools/          one module per theme; each has register(mcp, app, tool)
    common.py     typed parameter aliases + the @tool registrar
tests/            unit + end-to-end tests (fake Onshape) + real stdio subprocess tests
scripts/          smoke_test.py (live), gen_tools_doc.py
```

### Request flow

```
MCP client ──stdio──▶ MCPServer ──▶ tool function ──▶ App ──▶ OnshapeClient ──HTTPS──▶ Onshape
                         ▲               │  units / builders (pure functions)
                         └── ToolError ◀─┘  JamcpError -> ToolError at the registrar
```

## Design decisions worth knowing

- **Units at the edge.** Tools accept/return the configured unit; Onshape gets SI or expression
  strings (`"10 mm"`). Nothing below `tools/` knows about the user's preference except through a
  `UnitSystem` argument. Builders are pure functions and easy to test.
- **Errors are for the model.** In `mcp` 2.x an exception that is not `ToolError` is masked as a
  generic "Error executing tool X". The registrar in `tools/common.py` converts every `JamcpError`
  (config, input, network, Onshape) to `ToolError` so the model sees an actionable message. Never
  put secrets in exception messages.
- **No hard-coded origin-plane IDs.** `App.plane_id` evaluates FeatureScript
  (`qCreatedBy(makeId("Top"), EntityType.FACE)`) once per element and caches the result.
- **Credentials are lazy.** The server starts without keys so registries can introspect it;
  `Settings.require_credentials()` runs on first API use.
- **Read-only is structural.** Tools declared `writes=True` are simply not registered.
- **Redirects never leak credentials.** The client follows `3xx` manually; auth headers are only
  attached for the configured host / `*.onshape.com`.
- **Screenshots are unstructured output.** The tool mixes a JSON text block and image blocks, so it
  is registered with `structured=False`.
- **stdout is sacred.** It carries the protocol; all logging goes to stderr.

## Adding a tool

1. Pick the module in `tools/` (or add one and list it in `tools/__init__.py`).
2. Write an `async def` inside `register()` decorated with
   `@tool(title=..., read_only=True)` (or `writes=True` for anything that modifies a document).
3. Use the typed aliases from `tools/common.py` (`DocumentId`, `ElementId`, `WorkspaceId`) and
   `Annotated[..., Field(description=...)]` for every parameter - descriptions are what the model
   reads. Do **not** add `from __future__ import annotations` to tool modules: the SDK builds the
   JSON schema from runtime annotations.
4. The docstring is the tool description. Say what it does, what it returns and when to use it.
5. Raise `InputError` for bad arguments; let `OnshapeError` propagate.
6. Add the tool to `GROUPS` in `scripts/gen_tools_doc.py`, then regenerate `docs/tools.md`.
7. Add a route to `tests/fake_onshape.py` if it calls a new endpoint, and a test in
   `tests/test_tools.py`.

The docs test fails if `docs/tools.md` is stale or an `ONSHAPE_*` variable is undocumented.

## Tests

| File | What it proves |
|---|---|
| `test_units_config.py` | conversions, aliases, validation, secrets never printed |
| `test_client.py` | HMAC signature recomputed independently, basic mode, 429 retry, error mapping, redirect safety, timeouts |
| `test_builders.py` | sketch/feature JSON structure and unit conversion |
| `test_tools.py` | every tool end to end through a real MCP `Client` against `fake_onshape.py` |
| `test_stdio.py` | the real process: starts without keys, flags override env, bad config exits 2, stdout stays clean |
| `test_docs.py` | tool reference up to date, settings documented, versions consistent, publishing files present |

`tests/fake_onshape.py` is an in-memory stand-in implementing the endpoints the tools use. It is
**not a CAD kernel**: it returns canned geometry, so it verifies *what we send and how we interpret
replies*, not that Onshape accepts our feature JSON.

### Verification status (read this before trusting a tool)

The unit tests verify request construction, unit conversion, response parsing, error handling and
MCP wiring against the simulated Onshape. Whether *Onshape itself* accepts what we send can only
be checked live, by `scripts/smoke_test.py`.

**Last live run: 2026-11-14, Onshape free plan, 31/31 checks passed**, covering: authentication
(HMAC and Basic), document and element creation, sketch + extrude on `Top`/`Front`/`Right` (axes
and extrude directions measured), face sketches, `outward_normal`, fillet, a through-hole, parameter
edits via `update_feature`, screenshots (both review modes), STEP and STL export, document/part
listing, FeatureScript, an empty Assembly and the raw API.

The live run found and fixed bugs a simulation could not, which is why the simulated Onshape in
`tests/fake_onshape.py` now mimics them:

| Found live | Lesson |
|---|---|
| `whoami` reported success on an anonymous session | Onshape answers `/users/sessioninfo` with `200 {}` for unrecognised keys; check for a user ID. |
| Free plans reject private documents (`409`) | `create_document` has an explicit `public` option instead of silently publishing. |
| FeatureScript eval `queries` must be an object, not an array | The fake now rejects the array form with a `400`. |
| FeatureScript results are *flat* (`{"btType": ".../BTFSValueArray", "value": [...]}`), not `message`-wrapped | `fs.unwrap` handles both; a verbatim live payload is a test fixture. |
| STL `units` must be lowercase (`millimeter`), contrary to the OpenAPI spec | Spec ≠ behavior; verify live. |
| `Top` plane resolved to `JDC` (a remembered "Front = JDC" was wrong) | Why plane IDs are looked up, never hard-coded. |

Still unverified live (not covered by the smoke test): `chamfer`, `delete_feature` on a feature
others depend on, `add_feature` with non-trivial JSON, Assembly with instances/mates, configured
(parametric) Part Studios, imperial output on real data, enterprise base URLs, and Onshape's
paid-plan behavior. Run the smoke test after any change to `sketch.py`, `features.py`,
`tools/modeling.py` or `client.py`:

```bash
uv run python scripts/smoke_test.py            # add --public on a free plan
```

## Releasing

Releases are tag-driven and fully described in [publishing](publishing.md#2-pypi-so-uvx-onshape-jamcp-works).
In short:

Write user-facing changes under `## [Unreleased]` in `CHANGELOG.md` as you go, then in VS Code run
*Terminal → Run Task → `Release`* (pick patch/minor/major, leave the note empty, choose *Preview*
first). Or from a terminal:

```bash
uv run python scripts/release.py release --bump patch --confirm yes   # preview without --confirm
```

`.github/workflows/release.yml` then verifies, builds and publishes to PyPI (Trusted Publishing, no
stored token) and creates the GitHub Release. `scripts/release.py` is standard-library only and
covered by `tests/test_release.py`; `test_the_real_repository_is_release_consistent` fails if the
version, package metadata and changelog ever drift apart.

## Ideas / roadmap

- Revolve, linear/circular pattern, hole and mirror helpers (needs a verified axis/selection model).
- Part Studio variables (`assign` features) and Variable Studios.
- Assembly mates and instance insertion.
- OAuth instead of long-lived keys; streamable-HTTP transport for self-hosting.
- `ONSHAPE_SCREENSHOT_AUTO` to attach a screenshot to every modeling result.
