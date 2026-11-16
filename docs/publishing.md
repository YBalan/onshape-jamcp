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
- [ ] Run `uv run python scripts/smoke_test.py` against your Onshape account - ship only what you
      verified. Update the "Status" note in the README accordingly.
- [ ] `uv run pytest` and `uv run ruff check src tests scripts` are green, and
      `uv run python scripts/release.py check` says OK.

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

Publishing and every later update are automated by
[`.github/workflows/release.yml`](../.github/workflows/release.yml) using PyPI **Trusted
Publishing** (OpenID Connect): GitHub proves its identity to PyPI for each run, so **no API token
is ever stored** in the repository or in GitHub secrets.

### One-time setup

1. **Push the repository to GitHub** (section 1) so `release.yml` exists on the default branch.
2. **Register a pending publisher on PyPI** at <https://pypi.org/manage/account/publishing/>
   ("Add a new pending publisher"). It lets the first release create the project:

   | Field | Value |
   |---|---|
   | PyPI project name | `onshape-jamcp` (free on PyPI as of 2026-11-14) |
   | Owner | your GitHub user or organization (`glama.json` lists `YBalan`; use whatever owns the repo) |
   | Repository name | the GitHub repository, e.g. `onshape-jamcp` |
   | Workflow name | `release.yml` |
   | Environment name | `pypi` |

   Every field must match exactly (case-sensitive), otherwise PyPI refuses with `invalid-publisher`.
3. **Optional but recommended - TestPyPI** for rehearsals: the same form at
   <https://test.pypi.org/manage/account/publishing/> with environment name `testpypi`.
4. **Create the GitHub environments** (repository → *Settings → Environments*): `pypi` and
   `testpypi`. On `pypi`, add yourself under *Required reviewers* - every real release then waits
   for your one-click approval - and limit deployment branches/tags to `v*`.

### Rehearse on TestPyPI

*Actions → Release → Run workflow → target `testpypi`*. It runs the full verification, builds the
package and uploads to TestPyPI (re-running is fine; existing files are skipped). Check the page at
`https://test.pypi.org/project/onshape-jamcp/`, and if you want to try the install, do it in a
throwaway virtualenv:

```bash
pip install --index-url https://test.pypi.org/simple/ --extra-index-url https://pypi.org/simple/ onshape-jamcp
onshape-jamcp --version
```

### Release (first publish and every update)

**One VS Code task does all of it:** *Terminal → Run Task… → `Release`* (defined in
[`.vscode/tasks.json`](../.vscode/tasks.json)). It asks three questions:

| Prompt | Choices |
|---|---|
| **Which version?** | `current` - release the version already in the files (use this for the first release, `0.1.0`) · `patch` · `minor` · `major` |
| **One-line release note** | Type a sentence, or leave it **empty** if the notes are already under `## [Unreleased]` in `CHANGELOG.md`, or you chose `current`. |
| **Preview or release?** | **Preview** (the default): runs every check and shows the plan, changes nothing · **RELEASE for real** |

Then, for a real release, it does everything in order and stops at the first problem:

1. **Plan and preflight** (read-only): works out the new version and tag, checks that the changelog
   has notes, that nothing is uncommitted outside the release files, that **no file is untracked**
   (a forgotten `git add` - say, the workflow itself - would otherwise publish nothing), that the tag
   is new and that a remote exists (`origin`, or the only remote, whatever it is called).
2. **Bump** the version in `__init__.py`, `pyproject.toml` and `CHANGELOG.md` (the changelog entry
   comes from `## [Unreleased]`, which is promoted to the new version and left empty, else from your
   typed note) and refresh `uv.lock`.
3. **Verify**: lint, format, the full test suite, docs-in-sync, version/changelog check, clean build
   with `twine check`. **If anything fails, every release file is put back exactly as it was** and
   nothing is committed.
4. **Commit** only the release files, **tag** `vX.Y.Z`, push the branch and then the tag.
5. **GitHub Actions** takes over and publishes to PyPI (below). The task prints the Actions link.

**Everyday workflow:** while you work, jot user-facing changes under `## [Unreleased]` in
`CHANGELOG.md` (Keep a Changelog style). When you want to ship, run the `Release` task, choose
`patch`/`minor`/`major`, leave the note empty, and pick *Preview* first.

The same from a terminal (preview unless you add `--confirm yes`):

```bash
uv run python scripts/release.py release --bump patch                 # preview
uv run python scripts/release.py release --bump patch --confirm yes   # for real
uv run python scripts/release.py release --bump minor --notes "Added revolve" --confirm yes
```

It is built to fail safe, and every point below is covered by tests that run real git against a
throw-away repository with a local "remote":

- it never acts unless the confirmation is exactly `yes` (the prompt defaults to *Preview*);
- everything that can be checked is checked **before** anything changes;
- a failure at any point before the commit restores the release files **byte for byte**, including
  your own uncommitted changelog edits;
- the commit contains only `__init__.py`, `pyproject.toml`, `CHANGELOG.md` (and `uv.lock` if it
  changed), even if you had staged other files;
- the branch is pushed **first** and the tag only if that succeeds, so a rejected push can never
  start a release from a commit that is not on the branch - if it is rejected, the message says what
  is left over locally and how to finish.

Optional extras, outside the task because they are not needed for every release:

```bash
uv run python scripts/smoke_test.py --public     # live check on your Onshape account (free plan)
gh workflow run release.yml -f target=testpypi   # rehearse the upload on TestPyPI (or use the
                                                 # Actions tab: Release -> Run workflow)
```

The individual steps also exist as subcommands (`check`, `notes`, `bump`, `verify`, `preflight`,
`commit`, `tag`, `push`, `publish`) - see `python scripts/release.py --help`.

What the tag triggers:

| Job | What it does |
|---|---|
| **verify** | `release.py check --tag vX.Y.Z` (tag = `pyproject` = `__init__` = newest `CHANGELOG` heading, dated, no TODO), ruff, the full test suite, docs-in-sync check |
| **build** | `python -m build`, `twine check`, installs the built wheel in a clean venv and runs `onshape-jamcp --version` |
| **publish-pypi** | uploads to PyPI via Trusted Publishing (waits for your approval if you set required reviewers) |
| **github-release** | creates the GitHub Release with the `CHANGELOG.md` section as its notes and the wheel/sdist attached |

Anything that fails in *verify* or *build* stops the release before anything is uploaded.

### Good to know

- **PyPI versions are immutable.** A published version can never be replaced; fix forward with a new
  patch version. (A bad release can be *yanked* in the PyPI UI so installers skip it.)
- `release.py` never uploads to PyPI itself - pushing the tag is what starts the workflow that does.
  Its git steps (`commit`, `tag`, `push`, `publish`) only run on request and refuse to proceed
  without `--confirm yes`. `python scripts/release.py --help` lists everything.
- **Manual fallback** (no GitHub Actions): `uv run python scripts/release.py build`, then
  `uv publish --token <token>` with a token created at <https://pypi.org/manage/account/token/>.
- Add `[project.urls]` (Homepage, Repository, Issues) to `pyproject.toml` with your real URLs so the
  PyPI page links back to the repository.
- After the first release the install lines in the README work as written. Glama's maintenance
  grade also rewards regular releases.

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
   - 31 tools - ABOVE the ~25 threshold where Glama starts to penalize tool count (a trade-off
     made for variables, configurations, naming and assemblies; consider optional tool groups)
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
