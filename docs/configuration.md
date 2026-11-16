# Configuration

onshape-jamcp is configured with **environment variables**. In an MCP client these go in the
server's `env` block. A few have **command-line flags** (set in the client's `args`), which
override the environment.

```json
{
  "command": "uvx",
  "args": ["onshape-jamcp", "--units", "imperial", "--no-screenshot-review"],
  "env": { "ONSHAPE_ACCESS_KEY": "...", "ONSHAPE_SECRET_KEY": "..." }
}
```

Invalid values stop the server at startup with a message naming the variable (exit code 2). Missing
API keys (or a missing OAuth sign-in) do **not** stop it: the server starts and lists its tools, and
each tool call that needs Onshape reports what to set. Prefer signing in with your account over a
permanent key? See [OAuth](oauth.md).

## All settings

| Variable | Flag | Default | Description |
|---|---|---|---|
| `ONSHAPE_ACCESS_KEY` | | - | API access key (required for any tool that calls Onshape). |
| `ONSHAPE_SECRET_KEY` | | - | API secret key. |
| `ONSHAPE_OAUTH_CLIENT_ID` / `ONSHAPE_OAUTH_CLIENT_SECRET` | | - | Your own Onshape OAuth application, as an alternative to API keys. Then run `onshape-jamcp auth login` once. See [OAuth](oauth.md). |
| `ONSHAPE_OAUTH_PORT` | | `18339` | Port of the local sign-in callback (redirect URL `http://localhost:<port>/callback`, 1024-65535). |
| `ONSHAPE_OAUTH_TOKEN_FILE` | | per-user config folder | Where the OAuth tokens are stored. |
| `ONSHAPE_OAUTH_URL` | | `https://oauth.onshape.com` | Onshape's OAuth server (must be `https`). |
| `ONSHAPE_UNITS` | `--units` | `metric` | `metric` (millimeters) or `imperial` (inches). Aliases: `mm`, `inch`, `inches`, `in`. |
| `ONSHAPE_SCREENSHOT_REVIEW` | `--screenshot-review` / `--no-screenshot-review` | `true` | Send screenshots to the LLM for review. See [screenshots](screenshots.md). |
| `ONSHAPE_SCREENSHOT_WIDTH` | | `800` | Default screenshot width in px (64-2000). |
| `ONSHAPE_SCREENSHOT_HEIGHT` | | `600` | Default screenshot height in px (64-2000). |
| `ONSHAPE_OUTPUT_DIR` | | system temp `/onshape-jamcp` | Where screenshots (`screenshots/`) and exports (`exports/`) are saved. |
| `ONSHAPE_READ_ONLY` | `--read-only` | `false` | Do not register any tool that modifies documents; block non-GET `onshape_api_request`. |
| `ONSHAPE_BASE_URL` | | `https://cad.onshape.com` | Onshape origin. Enterprise accounts: `https://yourcompany.onshape.com`. Must be `https` (plain `http` only for localhost). |
| `ONSHAPE_API_VERSION` | | `v17` | REST API version, `v` + number. The tools were written against v17. |
| `ONSHAPE_AUTH_MODE` | | automatic | `hmac` signs each request (the secret is never transmitted). `basic` sends `access:secret` as HTTP Basic over HTTPS; Onshape documents it as lowest-security, "for local testing only", so use it only as a fallback if signing misbehaves. `oauth` uses the OAuth sign-in. Unset: `hmac`, unless only the OAuth client is configured (API keys win when both are set). |
| `ONSHAPE_TIMEOUT` | | `60` | Per-request timeout in seconds (1-600). Raise it for very large models. |
| `ONSHAPE_LOG_LEVEL` | | `WARNING` | `DEBUG`, `INFO`, `WARNING`, `ERROR`. Logs go to **stderr** (stdout is the MCP channel). `DEBUG` also logs HTTP requests (never credentials or bodies). |

Booleans accept `true/false`, `1/0`, `yes/no`, `on/off`.

## Units

The unit system is a single global setting.

| | `metric` (default) | `imperial` |
|---|---|---|
| Lengths in and out | millimeters (`mm`) | inches (`in`) |
| Areas | `mm^2` | `in^2` |
| Volumes | `mm^3` | `in^3` |
| Angles | degrees | degrees |
| Mass | kilograms (always) | kilograms (always) |

How it works:

- **Plain numbers are in the configured unit.** With metric, `depth: 10` means 10 mm; with imperial
  it means 10 in.
- **Explicit units always win.** Any length also accepts a string such as `"0.5 in"` or `"25 mm"`
  (units: `mm`, `cm`, `m`, `in`, `ft`, `yd`), so you can mix systems in one request.
- **Onshape expressions** are accepted where Onshape evaluates them (`extrude.depth`, `fillet.radius`,
  `chamfer.distance`, `update_feature` values): `"#width"`, `"#width / 2"`. Sketch geometry needs
  concrete numbers because the API stores it in meters.
- **Everything the server returns is converted** to the configured unit: bounding boxes, volumes,
  centroids, face origins, edge end points, assembly positions. `get_settings` tells the model which
  unit is active.
- Two things stay in raw SI because they are pass-through: results of `run_featurescript` (meters)
  and `onshape_api_request` (meters). Their tool descriptions say so.

Switching units never changes existing models - Onshape stores geometry in SI internally and only
the *input and output of this server* change.

## Screenshots

- `ONSHAPE_SCREENSHOT_REVIEW=true` (default): `capture_screenshot` saves the PNG **and** returns it
  to the model.
- `ONSHAPE_SCREENSHOT_REVIEW=false`: it saves the PNG and returns only the file path and metadata -
  **no image tokens**.
- Per call: `capture_screenshot(review=true|false)` overrides the setting for that call.

Details, token numbers and strategy: [screenshots.md](screenshots.md).

## Read-only mode

With `ONSHAPE_READ_ONLY=true` the modeling and document-creating tools (`create_document`,
`create_element`, `create_sketch`, `extrude`, `fillet`, `chamfer`, `update_feature`,
`delete_feature`, `add_feature`) are **not registered at all**, so the model cannot even try. Reading,
measuring, screenshots, export and `GET` requests through `onshape_api_request` keep working.
Note that export creates a temporary translation job on Onshape's side but does not change your
document.

## Precedence

command-line flag → environment variable → built-in default.

## Per-client notes

- **Claude Desktop** passes only the variables in `env` to the server - it does not inherit your
  shell environment. Put the keys in `env`.
- **Windows paths** in JSON need escaped backslashes (`"D:\\source\\repos\\..."`) or forward slashes.
- For local development a `.env` file is read by `scripts/smoke_test.py` only; the server itself
  reads the real process environment.
