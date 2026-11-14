# How this compares

Several MCP servers touch Onshape. This table is based on each project's own README/listing as of
November 2026; check them for current details.

| | **onshape-jamcp** (this) | [hedless/onshape-mcp](https://github.com/hedless/onshape-mcp) | [altendky/onshape-mcp](https://github.com/altendky/onshape-mcp) | [Onshape Labs FeatureScript MCP](https://www.onshape.com/en/blog/featurescript-mcp-server-enables-text-code-cad) |
|---|---|---|---|---|
| Language / runs | Python, local stdio | Python, local stdio | Rust, local stdio (+ experimental HTTP) | Hosted by Onshape (HTTP) |
| Install | `uvx` / `pip` / Docker | clone + venv | `npx` or binary | Onshape App Store subscription |
| Auth | API keys (HMAC; Basic as fallback) | API keys (Basic) | OAuth (default) or API keys | Your Onshape sign-in |
| Default units | **millimeters**, inches via setting | inches only | not stated | n/a |
| Screenshots | yes, with a **token-saving review toggle** | no | yes | no |
| Read-only mode | yes | no | - | - |
| Scope | documents, sketch/extrude/fillet/chamfer, edit, measure, faces/edges, assembly view, export, raw API | very broad modeling + assemblies/mates (45+ tools) | broad, endpoints discovered dynamically; FeatureScript authoring | generate/debug FeatureScript custom features only |
| Tool count | 22 | 45-48 | - | - |

## Where jamcp is deliberately different

- **Units are a first-class setting**, not an afterthought: millimeters by default, one switch to
  inches, explicit units always honored, every output converted.
- **Screenshot cost is under your control**: review on/off globally and per call, with token
  estimates reported.
- **Smaller tool surface.** Twenty-two well-described tools plus escape hatches, rather than 45+.
  Fewer tools means less context spent on the tool list and fewer wrong choices by the model.
  (Glama's quality scoring also penalizes very large tool counts.)
- **Nothing hard-coded that Onshape can tell us.** Origin-plane IDs are looked up with FeatureScript
  at runtime instead of being baked in as constants. (One existing server hard-codes
  Front/Top/Right as three-letter IDs; that mapping could not be confirmed independently, and a
  wrong constant would silently put sketches on the wrong plane.)
- **Safe to explore**: read-only mode removes write tools entirely.

## Where others are ahead (today)

- **hedless/onshape-mcp** has assembly mates, positioning, interference checks, patterns, revolve,
  thicken, variables and a much larger test and example base. If you need to build assemblies with
  an AI right now, it covers more. jamcp's `add_feature` and `onshape_api_request` can reach those
  features but without the ergonomic helpers.
- **altendky/onshape-mcp** supports OAuth (no long-lived secret keys) and a hosted HTTP mode, and
  it discovers API endpoints dynamically rather than shipping a fixed tool list.
- **Onshape's FeatureScript MCP** is the right tool for *authoring custom features*; it is
  complementary to this server, not a replacement.

## Acknowledgement

The Onshape feature JSON conventions (BTM types, sketch entity layout, sketch-region queries) were
cross-checked against hedless/onshape-mcp (MIT) and Onshape's public OpenAPI specification. No code
was copied.
