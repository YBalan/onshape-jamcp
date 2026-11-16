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
  client.py       OnshapeClient: HMAC / Basic / Bearer auth, retries on 429, safe manual redirects, errors
  oauth.py        OAuth 2: token file, refresh (rotation-safe), browser sign-in (`auth login`)
  app.py          App: lazy client + caches (default workspace, plane IDs, element types)
  fs.py           FeatureScript helpers: unwrap() typed results, plane-ID script
  sketch.py       Shape models (pydantic) -> BTMSketch-151 JSON
  features.py     extrude / fillet / chamfer JSON builders, feature-response summary
  custom_features.py  Feature Studio ("custom") features: spec summaries, spec -> feature JSON
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
  `Settings.require_credentials()` (or, for OAuth, the token file read in `OAuthSession`) runs on
  first API use.
- **OAuth is a client concern.** Tools never see tokens: `OnshapeClient` asks `OAuthSession` for a
  bearer token per request, renews it once on a `401`, and attaches it only to Onshape hosts.
  Refresh tokens may rotate (Onshape documents it; one live check showed none), so every renewal
  saves whatever comes back and re-reads the file first.
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
| `test_oauth.py` | OAuth settings, token file, token endpoint form, refresh and rotation, bearer + 401 retry, no token to third-party hosts, the browser flow with a simulated browser (state check, denial, timeout, busy port), `auth` commands |
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

**Custom features (`list_custom_features`, `add_custom_feature`), checked live on 2026-11-16** with
the public ThreadCreator Feature Studio (a Parametric Products "Screw Creator"):

| Found live | Consequence |
|---|---|
| A Part Studio's `featurespecs` (9 MB, 91 specs) lists no account-toolbar scripts | Discovery needs a Feature Studio URL, or the script used once in the Part Studio. |
| A Feature Studio's spec reports the *local* namespace `e<element>::m<microversion>`, even for a version-pinned fetch | `qualify_namespace` builds `d<doc>::v<version>::e<element>::m<microversion>` for another document. Onshape resolved it (the studio's spec appeared in the Part Studio's specs). |
| Spec `defaultValue`s are full `BTMParameter`s with a `nodeId` | The builder copies them and drops `nodeId`. |
| Quantity defaults often have `expression: ""` and the number in `value` with a per-parameter `units` (`meter`, `degree`, `radian`) | Defaults are shown from `value`+`units`; they are sent back untouched, and Onshape fills the expression itself. |
| The POST reply said `OK` for a feature that was `ERROR` after regeneration | `add_custom_feature` re-reads the feature list for the real status. |
| The feature state has no error text (`getFeatureStatus` only gives `REGEN_ERROR`) | Failures cannot be explained through the API. |
| A feature in another document's namespace came back `ERROR` while the same call with the script's own namespace was fine. The cause: **enum parameters repeat the namespace** (`"namespace"` inside `threadProfile`, `diameterType`, `lengthType`), and the spec's copy was still the local `e..::m..` form | `qualify_namespace` rewrites every occurrence, not only the feature's. After that the public ThreadCreator threaded a test cylinder with status `OK`. (An earlier note here blamed the script's editing-logic function; that was wrong.) |
| The UI stores the script's name template as the feature name (`#screwfriendlyname`) and renders it | The default feature name is the spec's `featureNameTemplate`. |

**Naming, variables, configurations, assemblies (2026-11-16).** Verified live unless noted:

| Area | Result |
|---|---|
| Names | `rename` sets the Name metadata property of a part or tab (same property ID for both); `extrude(part_name=...)` and the `has_default_name` flags work. |
| Variables | `assignVariable` with `mode`/`variableType`/`name`/`lengthValue` is enough. A sketch dimension constraint (`DIAMETER`, `RADIUS`, `LENGTH`) with `#name` follows the variable: a circle, a constrained regular hexagon and a rectangle all resized when the variable changed. |
| Configurations | `POST .../configuration` defines a list or number input (the read-back omits `parameterType`, so inputs are told apart by `btType`). A configured variable value is a `BTMParameterConfigured` whose inner values carry **no** `parameterId` (Onshape rejects them otherwise). `?configuration=Size=M18` re-evaluates the model. |
| Tight bounding box | The REST `boundingboxes` is loose for swept surfaces (an M10 nut: 9.45 mm instead of 8.4); `evBox3d` with `tight: true` is exact. `box` is a reserved word in FeatureScript. |
| Scoped intersect | An unscoped `intersect` extrude deleted a part that did not overlap the tool body; `only_parts` (`defaultScope: false` + `booleanScope`) is accepted. The tool now refuses the unscoped case when several parts exist. |
| Assemblies | Inserting parts works (`POST .../instances`, empty reply; read instances back with `GET`). **Mates work once built the real way.** First attempts (`BTMAssemblyFeature-887` with geometry queries straight in `mateConnectorsQuery`, 4 styles) were accepted with status `OK` and moved nothing. A hand-made cylindrical mate showed the real structure: a `BTMMate-64` with a `mateConnectors` list of two implicit `BTMMateConnector-66` (each an `originQuery` of `BTMInferenceQueryWithOccurrence-1083`: `inferenceType`, `deterministicIds`, `path` = [instance]) and a `mateConnectorsQuery` of `BTMFeatureQueryWithOccurrence-157` pointing at those connector feature IDs. The compressed `entityQuery` blob is not needed; a `center` on an edge needs the edge ID (a face ID alone gives `ERROR`). Solved positions appear in the assembly's `occurrences[].transform` and persist after the mate is deleted. Verified numerically: fastened origin to nut face z = 0.000 mm, `offset` 5.000 mm, cylindrical axes aligned. A threaded face's centroid is about 0.19 mm off the thread axis. |
| Batching | There is no general batch API. `POST .../features/updates` (`BTUpdateFeaturesCall-1748`) updated two variables in one request and the model followed; the reply includes each feature's regenerated status; a `name` in the request is **ignored**; it works for std features too (an extrude's depth). `set_variable(variables=...)`, `update_feature(updates=...)` and the spec cache are covered by tests but not yet exercised live (the feature-list endpoint was rate limited); the default extrude names were checked live. |
| Rate limits | Each endpoint has its own daily limit: `GET .../features` returned `429`, `Retry-After: 80721`, remaining `0`, while other endpoints showed 3000. The client no longer retries a limit longer than a minute. |
| Not verified | The complete parametric bolt and nut (threads under `Size=M18`): the run was cut short by that rate limit. Exporting a configuration. A number input inside an expression. |

**Replay check (2026-11-16).** A ThreadCreator instance made by hand in the UI (a different copy of the
script than the public one) was read back with `list_features(feature_id=...)`. Building the same
feature with `build_custom_feature` from that copy's Feature Studio **version URL** (derived from the
instance's own namespace) reproduced its namespace, name and all 16 parameters exactly (0 differences).
`add_custom_feature` then threaded a separate test cylinder with it (status `OK`; the stored feature
matched the original parameter for parameter); `update_feature` on that custom feature also worked.

**Internal thread (2026-11-16).** An M10 nut (16 mm across flats, 8.4 mm high, Ø10 hole) threaded with
`internalThreads: true`: status `OK`, +96.5 mm³ of ridges, minimum axis-to-face distance 4.25 mm
(Ø8.5 minor). The selected face is the thread's major diameter; `diameterType` and `majorDiameter`
made no difference when a face was selected.

**ThreadCreator parameter matrix (2026-11-16).** Each variant was added to a temporary Ø10 x 40 mm
cylinder and measured with FeatureScript point probes (is a point inside the body?), then deleted:

| Parameter | Observed |
|---|---|
| `leftHanded` | The helix is mirrored: a quarter-turn shifts the pattern by 3p/4 instead of p/4. |
| `numStarts` = 2 | The quarter-turn shift doubles (0.375 to 0.75 mm); `p` stays the spacing of adjacent threads. |
| `lengthType` `LENGTH` 6 mm / `TURNS` 4 | Identical result: a 6.5 mm thread at the free end, 83.3 mm³ removed (6/40 of a full thread). |
| `oppositeEnd` | The same 6 mm thread moves to the other end of the cylinder. |
| `p` = 2.0 | Period 2.0 mm, deeper thread (716.7 vs 551.5 mm³). |
| `threadProfile` | `ISO_STD` and `ANSI_STD` give identical geometry; `ISO_SQUARE` = `ANSI_SQUARE`; `ACME` and `TRAPEZOIDAL` each differ. |
| `taperFirstEnd` + `taperFirstEndAngle` + `leadInPitches` | Accepted (status OK); the effect is small (+0.66 mm³), so it is not confirmed beyond that. |
| `diameterType`, `majorDiameter` | No effect while a face is selected (the face sets the diameter). |

Not checked: several faces in `refChoice`, and the non-default options on an *internal* thread
(only the nut with the defaults above was built).

Also verified live in the same session: `chamfer` (tip chamfer of an M10 bolt; volume change matched
the geometry), polygon sketches, `qContainsPoint` edge selection through `run_featurescript`.
The smoke test only checks that `list_custom_features` runs; to also try a real script pass a
Feature Studio version URL: `uv run python scripts/smoke_test.py --custom-feature-studio <url>`.

**Patterns, mirror, revolve, shell, boolean (2026-11-16, verified live by volume).** Specs came from
`GET .../featurespecs` (not rate limited); every tool ran through the real MCP code against Onshape
on geometry with a known answer:

| Area | Result |
|---|---|
| Circular pattern | A feature pattern of a spoke extrude around the hub's cylinder face: 3 spokes = hub + 3 x one spoke exactly (17979.92 mm3), and `count="#spokes"` with a variable of 6 gave 23393.46 mm3. **A feature pattern needs `fullFeaturePattern=true`**: without it (Onshape's "faster" mode) the same request answered `ERROR` for a feature merged into a part, and for a hub extrude too; a *separate* new-part block was patterned either way. A pattern of a part with `operation=add` also gave hub + 3 spokes. A circular edge works as the axis as well. A pattern of a *sketch* feature is accepted (`OK`) and changes nothing (caught by `effect`). |
| Linear pattern | Origin planes as the direction run along their normals: Right +X, Top +Z, **Front -Y** (so `y` flips it). Edges and planar faces of the part work too. Rows (x4 = 4x volume, extents exact) and a 3 x 2 grid. |
| Mirror | Across Right and Front, for parts (volume x2, bounding box exact) and for a feature (`OK`, volume +1804.5 mm3). |
| Revolve | Full (28274.33 mm3 = pi x (20^2 - 10^2) x 30), 90 degrees (a quarter, 7068.58) and `remove` (a groove, 1256.64) around a cylinder face as the axis. The part is named by `part_name`. |
| Shell | Open top: 7152 mm3 = 24000 - 36 x 26 x 18. Closed hollow: 9024 mm3 = 24000 - 36 x 26 x 16 (my first expectation, 7152, was wrong). |
| Boolean | Two 20 mm cubes overlapping by 10 mm: union 12000, subtract 4000, intersect 4000 mm3; the reply lists the parts that disappeared. |
| Found while testing | `massproperties` evaluates **all parts as one body** unless `massAsGroup=false`: two cubes showed as one 16000 mm3 part, and part counts never rose. `get_part_properties` and the new `effect` now ask for one entry per part. Origin planes: Top = `JDC`, Front = `JCC`, Right = `JEC`. |
| Not verified | The `sketch:<feature_id>` lookup (a FeatureScript query for the construction lines of a sketch) and `revolve` without `axis`, because `POST .../featurescript` hit its daily limit during the session; the named directions `x`/`y`/`z` and plane names through that lookup (the IDs they resolve to were checked); a count driven by a variable configured *by an input*; `face_ids` patterns; `merge_with` with a real merge; a second-direction feature pattern; `keep_tools`. |

**What to re-run once the limits are gone:** [re-test checklist](retest-after-limits.md) (every item
marked "Not verified" in the tables here, with the exact calls and the expected numbers).

**OAuth (2026-11-16, verified live).** Implemented from Onshape's OAuth documentation and tested
against a simulated token server (including that a forged callback is ignored and that breaking the
host check is caught), then run against a real OAuth application:

| Area | Result |
|---|---|
| Sign-in | `auth login` worked with no `scope` and no PKCE: authorize on `oauth.onshape.com`, redirect to `http://localhost:18339/callback`, code exchange with `client_id` and `client_secret` in the form body. Denying on the consent page comes back as `error=access_denied`, which the server reports and stores nothing for. |
| Token reply | Fields `access_token`, `refresh_token`, `token_type` (`bearer`), `expires_in` (about 3600) and `scope` (the application's permissions, e.g. `OAuth2Read OAuth2Write OAuth2Delete ...`). |
| API calls | `/users/sessioninfo` and `/documents` with `Authorization: Bearer ...` worked. |
| Early refresh | With the stored expiry forced into the past, the next request refreshed and worked. **The refresh token did not change** (documentation says it rotates), the old one still worked, and Onshape returned the **same access token** while it was still valid, with the true remaining lifetime in `expires_in` (3599, then 3598 a moment later), but a different token after a pause of a minute or two. Hence the 30-second renewal margin and saving whatever comes back. |
| Rejected token | An invalid access token got a `401`; the client renewed and repeated the request successfully. |
| Not checked | A session spanning a real one-hour expiry, refresh-token lifetime over days, enterprise accounts (`ONSHAPE_OAUTH_URL`), and what happens after the grant is revoked. |

Still unverified live (not covered by the smoke test): `delete_feature` on a feature
others depend on, `add_feature` with non-trivial JSON, configured
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
- Variable Studios (Part Studio variables exist: `set_variable`).
- Streamable-HTTP transport for self-hosting, with per-user OAuth (today's OAuth is a local sign-in
  for one user).
- `ONSHAPE_SCREENSHOT_AUTO` to attach a screenshot to every modeling result.
