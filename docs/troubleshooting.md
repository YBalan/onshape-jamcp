# Troubleshooting

Start with two checks: ask the assistant to run **`get_settings`** (shows what the server believes
its configuration is) and **`whoami`** (proves the API keys or OAuth sign-in work).

## Connection and authentication

| Symptom | Cause / fix |
|---|---|
| `Onshape API credentials are not configured` | `ONSHAPE_ACCESS_KEY` / `ONSHAPE_SECRET_KEY` did not reach the server. Put them in the client's `env` block (Claude Desktop does not inherit your shell). Restart the client. |
| `whoami` says "anonymous session" | Onshape answers `200` with an empty body (instead of `401`) when it does not recognise the keys, so this means the same as a 401. The secret key is **exactly 48 characters** - a stray trailing `.` or space copied along with it is the classic cause. |
| `This Onshape plan only allows PUBLIC documents` (409 from `create_document`) | Free plans cannot create private documents. Nothing was created. Retry with `public=true` if you accept a public document (the assistant should ask you first), or use a paid plan. |
| `Onshape API error 401` | Wrong or revoked keys, or a typo/extra whitespace. Create fresh keys at <https://dev-portal.onshape.com/keys>. If you are on an enterprise plan, set `ONSHAPE_BASE_URL` and use keys from *your company's* portal. As a last resort try `ONSHAPE_AUTH_MODE=basic` (Onshape calls Basic "local testing only", so treat it as a diagnostic). |
| `Onshape API error 403` | The key lacks a scope (write tools need *write*; `delete_feature` needs *delete*) or you have no permission on that document (shared read-only?). |
| `Onshape API error 404` | A wrong ID. The tool message says which kind of ID to re-check; use `list_documents` / `get_document` rather than guessing. |
| `Onshape API error 429` | Rate limit. The server retries twice with the `Retry-After` delay; if it persists, slow down or batch less. |
| `Timed out after 60s` | Large model or slow network. Raise `ONSHAPE_TIMEOUT` (e.g. `180`). |
| `Could not reach Onshape` | Network/proxy/DNS issue, or a wrong `ONSHAPE_BASE_URL`. |
| `ONSHAPE_BASE_URL must use https` | Credentials are never sent over plain http (except to localhost). |

## OAuth

Setup and background: [OAuth sign-in](oauth.md). Run `onshape-jamcp auth status` first: it shows the
mode, whether a client is configured, and whether you are signed in.

| Symptom | Cause / fix |
|---|---|
| `Not signed in to Onshape` | Run `onshape-jamcp auth login` once, with the same `ONSHAPE_OAUTH_CLIENT_ID` / `_SECRET` as the MCP client (and the same `ONSHAPE_OAUTH_TOKEN_FILE`, if you set one). Then restart the client. |
| `OAuth is selected but the application is not configured` | `ONSHAPE_OAUTH_CLIENT_ID` and `ONSHAPE_OAUTH_CLIENT_SECRET` did not reach the server. Put them in the client's `env`. |
| The server uses API keys although OAuth is set up | API keys win when both are set. Remove them, or set `ONSHAPE_AUTH_MODE=oauth`. |
| `Cannot listen on 127.0.0.1:18339` | Another program uses the port. Free it, or set `ONSHAPE_OAUTH_PORT` and register the same redirect URL (`http://localhost:<port>/callback`) with your application. |
| The browser shows an error from Onshape instead of asking for consent | The redirect URL registered with the application does not match `http://localhost:18339/callback` exactly (scheme, host, port, path), or the client ID is wrong. |
| `Onshape refused the OAuth sign-in (HTTP 400/401: ...)` | The client secret is wrong, or the code expired (it lasts about three minutes): run `auth login` again. The reason Onshape gave is in the brackets; the secret is never printed. |
| `Onshape rejected the stored sign-in` | The grant was revoked or the refresh token expired. Run `auth login` again. |
| `The stored sign-in belongs to a different OAuth application` | You changed the client ID. Run `auth login` again. |
| `No sign-in arrived within 300 s` | The consent page was not completed, or the callback went to another machine (`--no-browser` on a remote host). Sign in on the machine that runs the command. |
| `The OAuth token file ... is unreadable` | Delete the file (`auth logout`) and sign in again. |

## Server does not appear / start

- **First start is slow.** `uvx` downloads dependencies once, and importing the MCP SDK takes a few
  seconds. Clients usually allow ~60 s. Subsequent starts are quicker.
- **Check the client's MCP log** (Claude Desktop: *Settings → Developer → Open logs folder*, file
  `mcp-server-onshape.log`). The server writes diagnostics to **stderr**; set
  `ONSHAPE_LOG_LEVEL=DEBUG` for more.
- `configuration error: ONSHAPE_UNITS: ...` - the message names the invalid variable. Exit code 2.
- **`uvx` not found** - install [uv](https://docs.astral.sh/uv/) or use `pip install onshape-jamcp`
  and `"command": "onshape-jamcp"`. On Windows give an absolute path if the client can't find it.
- **Windows JSON paths** need `\\` or `/`.

## Modeling problems

| Symptom | Cause / fix |
|---|---|
| Feature status `ERROR` | Open `list_features` to see which. A bad sketch (open profile, overlapping shapes) breaks the extrude that uses it. Fix with `update_feature` or `delete_feature` and re-create. |
| `extrude` with `remove` changes nothing | A face sketch extrudes *out of* the part by default. Delete that feature and repeat with `opposite_direction=true`; confirm with `get_part_properties` (volume must drop). |
| Part is on the wrong axes / plane | `Top` = XY, `Front` = XZ, `Right` = YZ. Check `get_part_properties.bounding_box`. Run [`scripts/smoke_test.py`](../scripts/smoke_test.py) once - it verifies the mapping on your account. |
| Sketch on a face lands in the wrong place | Sketch axes follow the face's own orientation, not the world's. Re-check with `get_body_details` (hole centers) or a screenshot; adjust the coordinates. |
| `extrude` says no regions / `ERROR` | Shapes must form **closed** profiles. Rectangles, circles and closed polygons do; lines/arcs must meet end to end. Overlapping shapes create separate regions. |
| `fillet` fails | Radius too large for the geometry, or edge IDs are stale (IDs change when upstream features change). Re-run `get_body_details`. |
| `Cannot read 'x' as a length` | Lengths are numbers, `"12 mm"` strings, or (for extrude/fillet/chamfer/update) Onshape expressions like `#width`. Sketch geometry must be concrete numbers. |

## Naming, variables, configurations, assemblies

Details: [Parametric modeling](parametric.md).

| Symptom | Cause / fix |
|---|---|
| `429` saying the daily limit for THIS endpoint is used up | Onshape limits each endpoint per day (seen: `GET .../features`, about 22 h). Wait for the reset; other endpoints still work. Avoid polling `list_features`. |
| `Variable(s) #x are not defined` | Create it first with `set_variable`; a sketch size must be a plain `#name`. |
| `There is no configuration list input 'Size'` | Create it with `set_configuration_input` before `set_variable(by_option=...)`. |
| A mate is `OK` but nothing moved | Read the positions `add_mate` returns. The first part must be grounded (`fixed=True`), and each side needs an entity ID (circular edge for `center`, face for `centroid`); see [assemblies](parametric.md#assemblies). |
| `intersect` refused | An unscoped intersect deletes every part that does not overlap; pass `only_parts`. |

## Threads and custom features

Details and the full workflow: [Threads and custom features](threads.md).

| Symptom | Cause / fix |
|---|---|
| `list_custom_features` shows no ThreadCreator | The API does not list the scripts on your toolbar. Use the script once in that Part Studio, or pass `feature_studio_url`. |
| "needs a *version* URL" | A Feature Studio in another document must be given as `.../documents/<did>/v/<version>/e/<eid>`. |
| Thread feature is `ERROR` | Onshape gives no reason through the API. Check the face is a cylinder, then compare with a thread made by hand (`list_features(feature_id=...)`). |
| A nut's thread is too small / wrong size | The hole was modeled at the tap-drill size. ThreadCreator takes the selected face as the **major** diameter: cut a Ø10 hole for M10. |
| A bolt came out as an internal thread, or with an inch profile | `internalThreads` defaults to `true` and `threadProfile` to `ANSI_STD`. Pass `internalThreads: false` and `ISO_STD`. |

## Screenshots and exports

| Symptom | Cause / fix |
|---|---|
| Result has a file path but the model "can't see" the picture | Review is off. Pass `review=true` or set `ONSHAPE_SCREENSHOT_REVIEW=true`. |
| `Onshape returned no image` | The Part Studio is empty. Model something first. |
| Image is blank/tiny | Ask for a bigger `width`/`height`, or check the part isn't hidden in Onshape. |
| `path` is `null` | The output directory isn't writable. Set `ONSHAPE_OUTPUT_DIR` to a writable folder (in Docker, mount a volume). |
| `Export still running after 120s` | Very large model; retry, or export from Onshape's UI. |
| Export file is a `.zip` | Onshape zipped a multi-file result; the server detects that and names it `.zip`. |

## Still stuck?

1. Reproduce with `ONSHAPE_LOG_LEVEL=DEBUG`.
2. Try the raw API for the same operation via `onshape_api_request` - it shows Onshape's own
   response.
3. Run the smoke test and attach its output to an issue (it never prints your keys).
