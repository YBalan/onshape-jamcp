# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

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
