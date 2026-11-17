# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
- **One-click installer for Claude Desktop** (`onshape-jamcp.mcpb`): a 17 KB "MCP bundle" that asks for
  the API keys (stored by Claude Desktop, never in a file) and the usual settings (units, screenshot
  review, read-only, output folder, Onshape address) in a form. It is a `uv` bundle (manifest 0.4):
  `uv` builds the environment from PyPI on first start, so nothing compiled is shipped and one file
  serves Windows, macOS and Linux. Built by `scripts/build_mcpb.py` (reproducible, tool list generated
  from the server), attached to every GitHub release by the release workflow under a fixed name so
  `releases/latest/download/onshape-jamcp.mcpb` always works, and covered by `tests/test_mcpb.py`.
  See [docs/claude-desktop.md](docs/claude-desktop.md).
- **README examples** of what to ask for (plate and fillets, revolve, a wheel with a configurable spoke
  count, a parametric M10 / M18 bolt and nut, patterns and shell, assembly, export, read-only) with the
  volumes measured on live Onshape.
- **The Glama Dockerfile form** is documented in [docs/publishing.md](docs/publishing.md#the-glama-dockerfile-form):
  every field's value, the environment-variable schema and placeholder arguments, and a log-to-fix table.
  The build steps and command were replayed in a clean copy of the repository.

### Changed
- The README no longer says the package is unpublished (it is on PyPI).

## [1.1.1] - 2026-11-16

### Fixed
- **Variables and variable-driven names showed `?` in Onshape's feature tree.** A variable feature
  was named `#d` and an extrude with a variable depth `Extrude #head_h`, but in a feature name
  Onshape reads `#word` as one of that feature's own parameters (its own default name for a variable
  is `###name = #value`, where `##` is a literal `#`) and prints `?` when there is none. Variables are
  now named after themselves (`d`) and generated names write a variable in brackets (`Extrude
  (head_h)`, `Circular pattern x(spokes)`, `Linear pattern x(n) @ (pitch)`). An old `Extrude #head_h`
  is repaired the next time its depth is changed with `update_feature`. Found by looking at the
  feature tree on a fresh account; the server instructions now tell the assistant never to put
  `#word` in a name it chooses.
- **A gateway error (`502` / `503` / `504`) came back as a wall of HTML.** It is now a short message
  with a hint that a write is not repeated automatically (it may have been saved: look before
  repeating it).

## [1.1.0] - 2026-11-16

### Fixed
- **`get_part_properties` measured all parts as one.** Onshape adds the parts up unless asked
  (`massAsGroup=false`), so two cubes showed as one 16000 mm3 "part". Each part now has its own entry
  (and a `total_volume` when there are several).
- **Configuration inputs made in Onshape's UI.** Their generated IDs (`List_gfWXlOaT2Zmfyo`) and
  option IDs (`_3Spoke`) differ from the labels shown in the UI (`Configuration`, `3Spoke`), and the
  tools assumed ID = label. `get_configuration` now lists labels with the `id` / `option_ids` beside
  them, and `configuration=` (measure, faces, screenshots, `insert_instance`), `set_variable`
  `by_option` and `set_configuration_input` accept labels (any case) or IDs. An unknown input or
  option is refused with the valid choices, where Onshape would silently have shown the default
  variant. Checked live with an input shaped like a UI-made one.

### Added
- **Patterns, mirror, revolve, shell and boolean:** `circular_pattern`, `linear_pattern`, `mirror`,
  `revolve`, `shell` and `boolean` (37 tools now). Built from Onshape's own feature specs and checked
  live by volume: 3 and 6 spokes around a hub (also with the count in a variable `#spokes`), rows and
  grids along three directions, mirrors across origin planes, full / 90 degree / cutting revolves, an
  open-top and a hollowed box, union / subtract / intersect. Patterns repeat a *feature* (`feature_ids`:
  the extrude that makes one spoke, so the copies merge as it does - sent as Onshape's "full" pattern,
  because its "faster" mode answered `ERROR` for a feature merged into a part), whole *parts*
  (`part_ids`) or *faces*. The axis or direction is an edge / face ID, or `sketch:<feature_id>` for a
  construction line (`x` / `y` / `z` for linear directions). Every reply says what changed (`effect`:
  parts and volume before / after, parts created and removed) and warns when an accepted feature did
  nothing (`verify=false` skips the two measuring requests).
- **OAuth sign-in as an alternative to API keys.** Register your own Onshape OAuth application,
  set `ONSHAPE_OAUTH_CLIENT_ID` / `ONSHAPE_OAUTH_CLIENT_SECRET`, and run `onshape-jamcp auth login`
  once (browser, authorization-code flow on a `127.0.0.1` callback). The server then sends bearer
  tokens, renews them before they expire and once more on a `401`, and keeps the refresh token in a
  per-user file. New commands `auth login|status|logout`; new settings
  `ONSHAPE_OAUTH_CLIENT_ID`, `_CLIENT_SECRET`, `_PORT`, `_TOKEN_FILE`, `_URL`; `ONSHAPE_AUTH_MODE`
  accepts `oauth` (chosen automatically when only the OAuth client is set; API keys win when both
  are). Checked live against a real Onshape OAuth application: sign-in, API calls, early refresh and
  the retry after a rejected token. See `docs/oauth.md`.
- **Custom features (Feature Scripts such as Thread Creator).** `list_custom_features` shows the
  custom features a Part Studio can use - or, with `feature_studio_url`, those a given Feature
  Studio defines - with their parameter IDs, types, units, defaults and options.
  `add_custom_feature` adds one, building the feature from Onshape's own feature spec (namespace
  copied from Onshape, unspecified parameters keep the script's defaults). 24 tools now.
  Checked against a live account: discovery, cross-document namespaces (`d..::v..::e..::m..`,
  version URLs) and threading a real part with ThreadCreator work, and a hand-made ThreadCreator
  instance was reproduced parameter for parameter. Onshape gives no error text for a failing
  feature; `docs/how-to.md` explains how to compare against a hand-made instance.
- Enum parameters of a script in another document carry that script's namespace too; it is now
  qualified together with the feature's own (leaving the local form made the feature fail).
- The default feature name is the script's own name template, as Onshape's UI does.
- `add_custom_feature` reports the status read back from the feature list: Onshape's reply to the add
  call can say `OK` for a feature that fails when regenerated.

- **Names by default:** `rename` (part or tab), `part_name` on `extrude`, and `has_default_name` flags
  on tabs and parts that still carry Onshape's placeholder names.
- **Variables by default:** `set_variable` / `list_variables`, and `"#name"` as a size in sketches
  (circle, new `hexagon` shape, rectangle `width`/`height`), extrude depth, fillets and thread pitch.
- **Configurations (only on request):** `set_configuration_input`, `get_configuration`,
  `set_variable(by_option=...)` and a `configuration` option on `get_part_properties`,
  `get_body_details`, `capture_screenshot` and `insert_instance`.
- **Assemblies (only on request):** `insert_instance` and `add_mate` (all eight mate types, flip,
  offset). Built the way Onshape builds mates - two implicit mate connectors embedded in a
  `BTMMate-64` - learned from a hand-made mate and verified by solving it; the reply reports the
  real status and both instances' positions.
- `extrude(only_parts=...)` restricts add / remove / intersect to given parts; an unscoped
  `intersect` is refused when it would delete other parts.
- `get_part_properties` returns Onshape's tight bounding box by default (`tight=false` for the quick
  one); the REST box overshot by 0.5 mm per side on a threaded nut.
- A `429` with a long `Retry-After` (a per-endpoint daily limit) is no longer retried and the error
  says when it resets.
- **Extrude names carry the size by default** ("Extrude 8 mm", "Cut 3 mm", "Extrude #head_h"); `hole=true`
  names a drilled hole "Hole" without a size; `update_feature` refreshes an automatic name when the
  depth changes.
- **Fewer Onshape requests:** `set_variable(variables={...})` and `update_feature(updates=[...])` use
  Onshape's multi-update endpoint (`features/updates`, checked live: two variables, one request);
  `create_sketch` looks `#variables` up with one FeatureScript call instead of reading the feature
  list; the 9 MB feature specs are cached for 5 minutes.
- 31 tools at that point (37 with the pattern and shaping tools below).

### Fixed
- `update_feature` writes integer parameters (counts, e.g. thread turns) as plain numbers instead of
  lengths such as `3 mm`.

## [1.0.0] - 2026-11-14

### Changed
- Initial Version

## [0.1.0] - 2026-11-14

First release (alpha).

### Added
- 22 MCP tools: documents, Part Studio inspection, sketch / extrude / fillet / chamfer modeling,
  feature editing, assembly inspection, screenshots, export and a raw API escape hatch.
- **Metric by default**: all lengths are millimeters unless `ONSHAPE_UNITS=imperial` (inches).
- **Screenshot review toggle**: `ONSHAPE_SCREENSHOT_REVIEW` / `--[no-]screenshot-review` and a
  per-call `review` flag decide whether images are sent to the LLM (token cost) or only saved.
- Read-only mode (`ONSHAPE_READ_ONLY`) that removes every write tool.
- HMAC-signed API-key authentication (HTTP Basic available as a fallback).
- Default-plane IDs are resolved at runtime via FeatureScript instead of being hard-coded.
- `scripts/smoke_test.py` for verifying a build against a real Onshape account.
- Generated tool reference (`docs/tools.md`), Dockerfile and `glama.json` for Glama.
- Release automation: tag-triggered `release.yml` (verify, build, PyPI Trusted Publishing, GitHub
  Release) and `scripts/release.py` (`check`, `notes`, `bump`, `build`, `verify`, `preflight`,
  guarded `commit` / `tag` / `push` / `publish`).
- One VS Code task, `Release` (`.vscode/tasks.json`), for the whole flow: it asks which version, an
  optional note and Preview vs real, then bumps, verifies (restoring the files on failure), commits,
  tags and pushes. Notes come from `## [Unreleased]` or a typed line.

### Fixed during live verification
- `whoami` no longer reports success for an anonymous session (Onshape returns `200 {}`).
- `create_document` has a `public` option; free plans reject private documents (`409`).
- FeatureScript evaluation: `queries` is an object, and flat `BTFSValue*` results are decoded.
- STL export: the `units` query value must be lowercase (`millimeter` / `inch`).

### Known limitations
- Verified live on an Onshape free plan (31/31 smoke-test checks); not yet verified live: chamfer,
  assemblies with mates, paid-plan behavior. See `docs/development.md`.
- No revolve/pattern/hole helpers, variables or assembly mates yet (use `add_feature` or
  `onshape_api_request`).
