# Getting started

This page takes you from nothing to a first part modeled by an AI assistant.

## 1. What you need

- An Onshape account (the free plan works, but see the note on public documents below).
- An MCP client: Claude Desktop, Claude Code, Cursor, VS Code, or any client that can launch a
  stdio MCP server.
- [`uv`](https://docs.astral.sh/uv/) (recommended - it runs the server without a manual install), or
  Python 3.10+ with `pip`.

> **Free plan warning.** Onshape's free plan makes all documents public. `create_document` asks for
> a private document by default; on a free plan Onshape refuses (`409`), and the assistant is told
> to ask you before retrying with `public=true`. Do not model confidential work on a free account.

## 2. Create Onshape API keys

1. Sign in and open <https://dev-portal.onshape.com/keys>.
2. **Create new API key**.
3. Tick the scopes you want the assistant to have:
   - *Application can read your documents* - required.
   - *Application can write to your documents* - needed for every modeling tool.
   - *Application can delete your documents and workspaces* - only needed for `delete_feature`
     and for the smoke test's cleanup.
4. Copy the **access key** and the **secret key**. The secret is shown **once** and is exactly
   **48 characters** - make sure no trailing space or punctuation (for example a full stop from a
   pasted sentence) comes along, or Onshape treats you as anonymous.

Tip: want the assistant to look but never touch? Give the key *read* scope only (or run with
`ONSHAPE_READ_ONLY=true`).

## 3. Install in your MCP client

All clients need the same three things: a **command** that starts the server, **environment
variables** with your keys, and (optionally) settings from [configuration](configuration.md).

### Claude Desktop

Edit `claude_desktop_config.json` (Settings → Developer → Edit Config):

```json
{
  "mcpServers": {
    "onshape": {
      "command": "uvx",
      "args": ["onshape-jamcp"],
      "env": {
        "ONSHAPE_ACCESS_KEY": "your-access-key",
        "ONSHAPE_SECRET_KEY": "your-secret-key"
      }
    }
  }
}
```

Restart Claude Desktop. The first start downloads dependencies and can take a few seconds.

### Claude Code

```bash
claude mcp add onshape \
  -e ONSHAPE_ACCESS_KEY=your-access-key \
  -e ONSHAPE_SECRET_KEY=your-secret-key \
  -- uvx onshape-jamcp
```

### Cursor / VS Code / others

Use the client's MCP settings with the same `command`, `args` and `env` as above (VS Code calls the
top-level key `servers` instead of `mcpServers`).

### From a local checkout (before the package is on PyPI)

```bash
git clone <your repository url> onshape-jamcp
cd onshape-jamcp
uv sync            # creates .venv and installs the package
```

Then point the client at the checkout:

```json
{
  "mcpServers": {
    "onshape": {
      "command": "uv",
      "args": ["run", "--directory", "D:\\source\\repos\\onshape-jamcp", "onshape-jamcp"],
      "env": { "ONSHAPE_ACCESS_KEY": "...", "ONSHAPE_SECRET_KEY": "..." }
    }
  }
}
```

Or use the virtualenv's executable directly:
`"command": "D:\\source\\repos\\onshape-jamcp\\.venv\\Scripts\\onshape-jamcp.exe"`.

### With pip

```bash
pip install onshape-jamcp
```

then use `"command": "onshape-jamcp"` with no `args`.

### With Docker

```bash
docker build -t onshape-jamcp .
```

```json
{
  "mcpServers": {
    "onshape": {
      "command": "docker",
      "args": ["run", "-i", "--rm",
               "-e", "ONSHAPE_ACCESS_KEY", "-e", "ONSHAPE_SECRET_KEY",
               "-v", "/some/local/folder:/home/jamcp/output",
               "onshape-jamcp"],
      "env": { "ONSHAPE_ACCESS_KEY": "...", "ONSHAPE_SECRET_KEY": "..." }
    }
  }
}
```

The `-v` mount is how you get screenshots and exports out of the container.

## 4. Check the connection

Ask the assistant: *"Use the onshape tools to run whoami."* You should get your Onshape user name
back. If you get an authentication error, see [troubleshooting](troubleshooting.md).

Then, to verify the whole toolchain on your account, run the
[smoke test](../scripts/smoke_test.py):

```bash
cp .env.example .env     # add your keys
uv run python scripts/smoke_test.py
```

## 5. Your first part

Try this prompt:

> Create a new Onshape document named "Jamcp bracket" with a Part Studio. Model a 60 × 40 mm plate,
> 8 mm thick. Cut four 5 mm diameter holes, each 6 mm in from the corners. Round the four vertical
> edges with a 4 mm fillet. Then show me an isometric screenshot.

What the assistant does with the tools:

1. `get_settings` - learns that lengths are millimeters.
2. `create_document`, `create_element` - the document and a Part Studio.
3. `create_sketch` (rectangle on `Top`) → `extrude` (8 mm, `new`).
4. `get_body_details` - finds the top face's ID.
5. `create_sketch` on that face (four circles) → `extrude` with `remove` (the holes).
6. `get_body_details` → `fillet` on the vertical edges.
7. `list_features` - confirms every feature is `OK`.
8. `capture_screenshot` - looks at the result.

## Next

- [How-to recipes](how-to.md) for common tasks
- [Threads and custom features](threads.md) for bolts, nuts and other Feature Scripts
- [Parametric modeling](parametric.md) for names, variables, configurations and assemblies
- [Configuration](configuration.md) to switch to inches or turn off screenshot review
- [Screenshots & token cost](screenshots.md)
