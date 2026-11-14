# Publishing

Checklist for taking onshape-jamcp from a local folder to a listed MCP server.

> **Heads-up on the URL.** <https://glama.ai/mcp/clients> lists MCP **clients** (apps that connect
> to servers, like Claude Desktop or Cursor). An MCP **server** like this one is listed at
> <https://glama.ai/mcp/servers>. Everything below targets the servers directory.

## 0. Before you publish

- [ ] Replace `YOUR-GITHUB-USERNAME` in [`glama.json`](../glama.json) with your GitHub username
      (the account that will own the listing).
- [ ] Decide the copyright holder in [`LICENSE`](../LICENSE) (currently "onshape-jamcp
      contributors") and add your name/organization to `authors` in `pyproject.toml`.
- [ ] Decide whether `.claude/settings.json` and `.github/hooks/` belong in the public repo. They
      are the personal "AI Helper" status-light hooks (copied from the esphome repo) and contain a
      private LAN address (`172.16.1.15`). They are harmless to others - the hooks never fail a turn
      and just time out when the device is absent - but you may prefer to add both paths to
      `.gitignore` before the first push. They are not part of the wheel, sdist or Docker image.
- [ ] Run `uv run python scripts/smoke_test.py` against your Onshape account - ship only what you
      verified. Update the "Status" note in the README accordingly.
- [ ] `uv run pytest` and `uv run ruff check src tests scripts` are green.

## 1. GitHub

Glama indexes servers from public GitHub repositories.

```bash
git init
git add .
git commit -m "Initial release"
git branch -M main
git remote add origin https://github.com/<you>/onshape-jamcp.git
git push -u origin main
```

Confirm `.env` is **not** committed (it is in `.gitignore`). Then add `[project.urls]` (Homepage,
Repository, Issues) to `pyproject.toml` with the real URLs. The CI workflow in
`.github/workflows/ci.yml` runs automatically.

## 2. PyPI (so `uvx onshape-jamcp` works)

```bash
uv build                       # dist/onshape_jamcp-0.1.0*.whl / .tar.gz
uv publish --token <pypi-token>
```

Check the name `onshape-jamcp` is free on PyPI first. After publishing, the config snippets in the
README work as written. (You can try the build before uploading:
`uvx --from dist/onshape_jamcp-0.1.0-py3-none-any.whl onshape-jamcp --version`.)

## 3. Glama (servers directory)

1. Open <https://glama.ai/mcp/servers> and use **Add Server**, giving your GitHub repository URL.
   Glama reads the repository (README, tool definitions, license) automatically.
2. **Claim the listing.** With `glama.json` in the repo root listing your GitHub username under
   `maintainers`, use *Claim ownership* on the listing. Re-run the claim flow after changing
   `glama.json` so it re-syncs. (A personal-account repo can alternatively be claimed by signing in
   with GitHub.)
3. **Dockerfile / release.** Glama builds the server in a container to introspect it and to run its
   checks. In the server's admin area configure the build from the repository's
   [`Dockerfile`](../Dockerfile), run the build test and **Make Release** - a GitHub release alone
   does not satisfy this. The server starts without API keys by design, so the build test works
   without secrets. Do **not** put real keys into the Glama form.
4. **Improve the score.** Glama grades license, tool-definition quality and maintenance. This repo
   is set up for it:
   - MIT `LICENSE` ✔
   - every tool has a description, typed parameters with descriptions, and annotations
     (`readOnlyHint` / `destructiveHint`) ✔
   - 22 tools - below the ~25 threshold where Glama starts to penalize tool count ✔
   - a README with install and configuration ✔
   - keep shipping releases and answering issues for the maintenance grade.

I could not exercise Glama's flows from this environment, so treat the Glama steps as the documented
process (see Glama's own [glama.json post](https://glama.ai/blog/2025-07-08-what-is-glamajson)) and
adjust if their UI has changed.

## 4. Optional: official MCP Registry

The [MCP Registry](https://github.com/modelcontextprotocol/registry) is a separate index many
clients read. Publishing there needs a `server.json` and, for PyPI packages, a verification marker
in the README - follow the registry's quickstart after your PyPI release.

## 5. Announce

README badges, a short demo (prompt → screenshot) and a post in the Onshape forum / MCP community
are the usual next steps.
