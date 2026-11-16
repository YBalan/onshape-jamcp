# How-to recipes

Each recipe shows the prompt you would give and the tool calls behind it, so you can steer the
assistant when it goes off course. Lengths below are millimeters (the default); see
[configuration](configuration.md#units) for inches.

- [Model a plate with holes and rounded corners](#model-a-plate-with-holes-and-rounded-corners)
- [Sketch on an existing face](#sketch-on-an-existing-face)
- [Change a dimension after the fact](#change-a-dimension-after-the-fact)
- [Work in inches](#work-in-inches)
- [Control screenshot review (save tokens)](#control-screenshot-review-save-tokens)
- [Measure and verify](#measure-and-verify)
- [Export STEP / STL](#export-step--stl)
- [Browse safely with read-only mode](#browse-safely-with-read-only-mode)
- [Use a custom feature such as Thread Creator](#use-a-custom-feature-such-as-thread-creator)
- [Do something the tools don't cover (revolve, patterns, holes, ...)](#do-something-the-tools-dont-cover)
- [Use an enterprise Onshape](#use-an-enterprise-onshape)
- [Recover from a failed feature](#recover-from-a-failed-feature)

---

## Model a plate with holes and rounded corners

> *Create a document "Plate". In a Part Studio, make a 60 × 40 mm plate 8 mm thick, four Ø5 mm
> holes 6 mm from the corners, and 4 mm fillets on the vertical edges.*

1. `create_document(name="Plate")` → `document_id`, `workspace_id`, and its elements. If there is no
   Part Studio, `create_element(document_id, name="Plate", kind="part_studio")`.
2. `create_sketch(plane="Top", shapes=[{"type":"rectangle","corner1":[0,0],"corner2":[60,40]}])`
   → `feature_id` (the sketch).
3. `extrude(sketch_feature_id=…, depth=8, operation="new")`.
4. `get_body_details` → find the face whose `outward_normal` is `[0,0,1]` (top) - note its `id`.
5. `create_sketch(plane=<top face id>, shapes=[four circles with "center": [x, y], "diameter": 5])`.

   Sketch axes on a face follow that face's own orientation, so the model should check placement
   (step 7) rather than assume.
6. `extrude(sketch_feature_id=…, depth=8, operation="remove", opposite_direction=true)`. A sketch on a
   part's face extrudes *out of* the part by default, so cutting **into** it needs
   `opposite_direction=true` (verified on live Onshape). Without it nothing is cut: check that
   `get_part_properties` shows the volume dropped, and if not, `delete_feature` and redo it flipped.
7. `get_body_details` again → the four vertical edges (start/end share x,y; z differs) →
   `fillet(edge_ids=[…], radius=4)`.
8. `list_features` → every `status` should be `OK`. `capture_screenshot` to look.

## Sketch on an existing face

`create_sketch`'s `plane` accepts `"Top"`, `"Front"`, `"Right"` **or any planar face/plane ID**. Get
face IDs from `get_body_details` (each face lists `type`, `origin`, `outward_normal`, `area`).
Choose the face by its normal and position, e.g. *largest-z face with normal +Z* for the top.

## Change a dimension after the fact

> *Make the plate 12 mm thick instead of 8.*

`list_features` → find the extrude → `update_feature(feature_id, parameters={"depth": 12})`.
Everything downstream (holes, fillets) rebuilds automatically. To discover a feature's parameter IDs,
call `list_features(feature_id=…)` and read the `parameterId` fields. Number values are lengths in
your unit (or degrees for angle parameters); strings can carry units (`"0.5 in"`) or reference
variables (`"#thickness"`); booleans and enum names are set as-is.

## Work in inches

Set `ONSHAPE_UNITS=imperial` (or add `--units imperial` to the client's `args`) and restart the
client. From then on `depth: 2` means 2 in and results come back in inches. You can also mix inside a
metric setup by using explicit units: `depth: "0.5 in"`. See [configuration](configuration.md#units).

## Control screenshot review (save tokens)

- Never send images by default: `ONSHAPE_SCREENSHOT_REVIEW=false` (or `--no-screenshot-review`).
- Ask for a look anyway: *"take one isometric screenshot with review on"* → the assistant calls
  `capture_screenshot(review=true)`.
- Smaller images: `capture_screenshot(width=400, height=300)`.

Full details: [screenshots.md](screenshots.md).

## Measure and verify

- `get_part_properties` → bounding box (min/max/size), volume, surface area, centroid, mass.
  Compare against what you asked for. Pass `part_id` (from `list_parts`) for one part.
- `get_body_details` → faces and edges with coordinates, to confirm hole positions and find edges.
- A bounding box that has the right numbers on the **wrong axes** means the sketch plane was wrong.

## Export STEP / STL

> *Export the plate as STEP.*

`export_model(format="STEP", filename="plate")` → saved under
`<ONSHAPE_OUTPUT_DIR>/exports/plate.step`; the tool returns the path. Formats: `STEP`, `STL`,
`PARASOLID`, `IGES`. STL from a Part Studio is written in your configured unit (mm or in). Use
`part_ids` to export only some parts.

## Browse safely with read-only mode

Set `ONSHAPE_READ_ONLY=true`. All tools that create or change things disappear from the tool list,
and `onshape_api_request` only allows `GET`. Great for *"summarize what's in my documents"* or
*"review this model"* sessions. Pair it with an API key that only has the *read* scope for a hard
guarantee.

## Repeat, mirror, revolve, shell and combine

> *Give this hub 6 spokes.* / *Make a row of 4 ribs 20 mm apart.* / *Mirror the left half.* /
> *Revolve this profile into a pulley.* / *Hollow this box, open at the top.*

```
create_sketch + extrude "new"  part_name="Hub"          # the hub
create_sketch + extrude "add"                           # ONE spoke, merged into the hub -> its feature_id
get_body_details                                        # the hub's cylindrical face id (type "cylinder")
circular_pattern(count="#spokes", axis=<cylinder face id>, feature_ids=[<spoke extrude>])
```

| Tool | Repeats / does | Key inputs |
|---|---|---|
| `circular_pattern` | copies around an axis | `count`, `axis`, `angle` (default 360) |
| `linear_pattern` | copies along a direction; a second direction makes a grid | `count`, `distance` (spacing), `direction`, `count_2` / `distance_2` / `direction_2` |
| `mirror` | copies across a plane | `plane` (`Top` / `Front` / `Right` or a planar face ID) |
| `revolve` | turns a sketch region about an axis | `sketch_feature_id`, `axis`, `angle`, `operation` (`new` / `add` / `remove`) |
| `shell` | hollows a part | `thickness`, `remove_face_ids` (open box) or `hollow_part_ids` (closed void) |
| `boolean` | joins, cuts or intersects parts | `operation`, `tools`, `targets` (subtract) |

- **What to repeat.** Exactly one of `feature_ids` (the feature that makes one spoke or hole: the
  copies merge or cut the way it does), `part_ids` (separate copies; use `operation="add"` to merge
  them into the parts they touch) or `face_ids`. Pattern the *feature* for a wheel's spokes; patterning
  the whole part with `operation="add"` also works but repeats the hub too. A feature pattern is sent
  as Onshape's *full* pattern; its "faster" mode fails for a feature merged into a part.
- **Axis and direction.** The ID of a cylindrical or conical face, of a circular or straight edge
  (all from `get_body_details`), or `sketch:<feature_id>` for the construction line drawn in that
  sketch (`create_sketch` with a `line` shape and `construction=true`). Linear patterns also take
  `x`, `y` or `z`; they run toward +X, +Y and +Z. `revolve` without `axis` uses the construction line of
  its own sketch. (The construction-line lookup is new and **not yet checked live**; an explicit edge or
  face ID is the proven route. See [development](development.md#verification-status-read-this-before-trusting-a-tool).)
- **Counts are variables.** `count="#spokes"` follows the variable (and a configuration that drives
  it, see [parametric modeling](parametric.md#configurations-m10-or-m18)). A count includes the original.
- **The reply is the check.** Each call returns `effect` (parts and total volume before and after), the
  parts it created or removed, and a **warning when the Part Studio did not change**: Onshape accepts
  some features that do nothing (patterning a *sketch* is one). `verify=false` skips the two
  measuring requests. Part IDs change when parts merge or split, so call `list_parts` afterwards.
- **Revolve and shell numbers.** A full revolve of a 10 x 30 mm profile at radius 10 to 20 measured
  exactly pi x (20^2 - 10^2) x 30 = 28274 mm3; a 40 x 30 x 20 mm box shelled 2 mm with its top face
  removed measured 7152 mm3 (a closed hollow leaves 9024 mm3, because the top and bottom walls count).

## Use a custom feature such as Thread Creator

> *Thread the shank of this bolt M10 × 1.5 with ThreadCreator.*

**Threads have their own page: [Threads and custom features](threads.md)** - what you need, the rule
for which diameter to model (it decides whether a nut comes out right), a worked bolt and nut, every
ThreadCreator option, and how to check the result. The rest of this recipe is the general mechanism.

Custom features (Feature Scripts) are written in a Feature Studio tab and are not part of Onshape's
standard library. Two tools handle them:

1. `list_custom_features(document_id, element_id)` → every custom feature this Part Studio can use:
   its `feature_type`, `namespace` and each parameter's `id`, `type`, `unit`, `default` and
   `options`. It also lists custom features the Part Studio already contains
   (`used_in_this_part_studio`).
2. `add_custom_feature(feature_type="…", parameters={…})`. Lengths are numbers in your unit,
   angles are degrees, enums take one of the listed `options`, selections (`type: "query"`) are
   lists of face/edge IDs from `get_body_details`. Anything you leave out keeps the script's own
   default. The new feature's `status` tells you whether Onshape accepted it; change it afterwards
   with `update_feature`.

**If the script you added to your account is not listed.** Onshape stores the toolbar's Feature
Scripts as an *account setting*, and a Part Studio's feature specs do not include them (checked
live), so a script only shows up once the Part Studio uses it. Either:

- click it once in that Part Studio's *Custom features* toolbar in Onshape (and delete the feature if
  you did not want it), then call `list_custom_features` again; or
- give the assistant the URL of the Feature Studio tab that defines it as `feature_studio_url`. For a
  Feature Studio in *another* document it must be a **version** URL
  (`…/documents/<did>/v/<version>/e/<eid>`): Onshape links custom features to a version. The tool
  then addresses the script as `d<doc>::v<version>::e<element>::m<microversion>`, which Onshape
  resolves (checked live).

**Check the result.** Onshape's reply to the add call can still say `OK` for a feature that fails
when it is regenerated, so `add_custom_feature` reads the status back and reports the real one.
The REST API gives only the status, not Onshape's error text.

**If a feature comes back `ERROR`.** Onshape gives no reason through the API, and a script's own checks
may live in code you cannot read. Create **one instance by hand** in Onshape with the settings you
want: `list_features(feature_id=…)` shows exactly what Onshape produces, to compare against or to
replay with `add_feature`. This is how the ThreadCreator replay was verified (see
[development](development.md#verification-status-read-this-before-trusting-a-tool)).

Threads in particular (bolt and nut examples, the major-diameter rule for internal threads, the
ThreadCreator options and their defaults) are covered in [Threads and custom features](threads.md).

## Do something the tools don't cover

The curated tools cover the common 80%. For holes, sweeps, lofts, thicken, draft and the rest there
are three escape hatches, in increasing order of power:

1. **Copy a feature the UI made.** Create the feature once in Onshape's UI (say a revolve), then
   `list_features(feature_id=…)` to read its JSON and `add_feature(feature=…)` to replay it with
   changes. This is the most reliable way to get the exact parameter IDs right.
2. **Look up the feature spec.** `onshape_api_request(method="GET",
   path="/partstudios/d/<did>/w/<wid>/e/<eid>/featurespecs")` lists the parameters of every feature
   type.
3. **Call any endpoint.** `onshape_api_request` reaches the whole [REST
   API](https://cad.onshape.com/glassworks/explorer). It is the raw API: **lengths are meters**, and
   writes are blocked in read-only mode.

For *queries* (counting faces, measuring distances, checking a selection) use `run_featurescript`.

## Use an enterprise Onshape

Set `ONSHAPE_BASE_URL=https://yourcompany.onshape.com` and create API keys in your company's
developer portal. Everything else is identical.

## Recover from a failed feature

Modeling tools return `status`. If it is `ERROR` or `WARNING`, the response includes a `warning`.

1. `list_features` to see which features are unhealthy (a broken sketch makes its extrude fail too).
2. Fix the cause: `update_feature` for a wrong number, or `delete_feature` and re-create.
3. Re-check with `list_features` and a screenshot.

Onshape keeps full version history, so you can also restore from the *Document history* UI.
