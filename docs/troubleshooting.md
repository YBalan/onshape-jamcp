# Troubleshooting

Start with two checks: ask the assistant to run **`get_settings`** (shows what the server believes
its configuration is) and **`whoami`** (proves the API keys work).

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
