# How this compares

Several MCP servers touch Onshape. This table is based on each project's own README/listing as of
November 2026; check them for current details.

| | **onshape-jamcp** (this) | [hedless/onshape-mcp](https://github.com/hedless/onshape-mcp) | [altendky/onshape-mcp](https://github.com/altendky/onshape-mcp) | [Onshape Labs FeatureScript MCP](https://www.onshape.com/en/blog/featurescript-mcp-server-enables-text-code-cad) |
|---|---|---|---|---|
| Language / runs | Python, local stdio | Python, local stdio | Rust, local stdio (+ experimental HTTP) | Hosted by Onshape (HTTP) |
| Install | `uvx` / `pip` / Docker | clone + venv | `npx` or binary | Onshape App Store subscription |
| Auth | API keys (HMAC; Basic as fallback) or your own OAuth application | API keys (Basic) | OAuth (default) or API keys | Your Onshape sign-in |
| Default units | **millimeters**, inches via setting | inches only | not stated | n/a |
| Screenshots | yes, with a **token-saving review toggle** | no | yes | no |
| Read-only mode | yes | no | - | - |
| Scope | documents, sketch/extrude/revolve, patterns, mirror, shell, boolean, fillet/chamfer, edit, measure, faces/edges, **account Feature Scripts (e.g. Thread Creator)**, variables, configurations, part naming, assemblies + mates, export, raw API | very broad modeling + assemblies/mates (45+ tools) | broad, endpoints discovered dynamically; FeatureScript authoring | generate/debug FeatureScript custom features only |
| Tool count | 37 | 45-48 | - | - |

## Where jamcp is deliberately different

- **Units are a first-class setting**, not an afterthought: millimeters by default, one switch to
  inches, explicit units always honored, every output converted.
- **Screenshot cost is under your control**: review on/off globally and per call, with token
  estimates reported.
- **Smaller tool surface.** 37 well-described tools plus escape hatches, rather than 45+. Fewer
  tools means less context spent on the tool list and fewer wrong choices by the model. (Glama's
  quality scoring penalizes large tool counts; at 37 this server is already above its ~25
  threshold, see [publishing](publishing.md#3-glama-servers-directory), so new tools are weighed
  against that.)
- **Your own Feature Scripts are first-class.** `list_custom_features` / `add_custom_feature` use
  scripts already added to your account (Thread Creator, for one), including from another
  document's version, and report the real feature status and result instead of trusting a reply
  that says `OK`. See [threads](threads.md).
- **Parametric by default.** Parts and tabs are named, key sizes go into Part Studio variables, and
  extrudes carry their size in the name; configurations (such as M10 / M18) and assemblies are used
  when you ask for them. See [parametric modeling](parametric.md).
- **Fewer requests.** Variables and feature edits can be batched into one Onshape request, and
  Onshape's per-endpoint daily limits are reported as such rather than retried.
- **Checks results, not replies.** Mates return the solved positions, and part size is the tight
  bounding box (the loose one made a correct nut look as if it stuck out).
- **Nothing hard-coded that Onshape can tell us.** Origin-plane IDs are looked up with FeatureScript
  at runtime instead of being baked in as constants. (One existing server hard-codes
  Front/Top/Right as three-letter IDs; that mapping could not be confirmed independently, and a
  wrong constant would silently put sketches on the wrong plane.)
- **Safe to explore**: read-only mode removes write tools entirely.

## Where others are ahead (today)

- **hedless/onshape-mcp** has interference checks, thicken, more assembly helpers (jamcp has
  `insert_instance` and `add_mate`, with all eight mate types) and a much larger test and example
  base. (Patterns, mirror, revolve, shell and boolean are covered here too, and each reply measures
  what the feature changed.) If you need that breadth right now, it covers more. jamcp's `add_feature`
  and `onshape_api_request` can reach those features but without the ergonomic helpers.
- **altendky/onshape-mcp** has OAuth as its default sign-in and a hosted HTTP mode, and it discovers
  API endpoints dynamically rather than shipping a fixed tool list. jamcp's OAuth is a local
  sign-in for one user with an application you register yourself (see [OAuth](oauth.md)), and it
  has no hosted mode.
- **Onshape's FeatureScript MCP** is the right tool for *authoring custom features*; it is
  complementary to this server, not a replacement.

## Acknowledgement

The Onshape feature JSON conventions (BTM types, sketch entity layout, sketch-region queries) were
cross-checked against hedless/onshape-mcp (MIT) and Onshape's public OpenAPI specification. No code
was copied.
