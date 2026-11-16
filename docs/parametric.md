# Parametric modeling: names, variables, configurations, assemblies

Two habits are on **by default** for every model; two features are **only used when you ask**.

| | When | Tools |
|---|---|---|
| [Names](#names) | always | `rename`, `extrude(part_name=…)` |
| [Variables](#variables) | always, for the key dimensions | `set_variable`, `list_variables`, `#name` in sizes |
| [Configurations](#configurations-m10-or-m18) | only if you ask (e.g. "make it selectable M10 / M18") | `set_configuration_input`, `set_variable(by_option=…)`, `configuration=` |
| [Assemblies](#assemblies) | only if you ask | `insert_instance`, `add_mate` |

The assistant is told this in the server's instructions, so you do not have to repeat it. Say the
opposite ("plain numbers, no variables", "call the part X") and it follows you.

## Names

A part still called `Part 2` or a tab still called `Part Studio 1` says nothing about what it is.

- `get_document` and `list_parts` flag placeholders with `has_default_name: true`.
- `extrude(operation="new", part_name="M10x40 Hex Bolt")` names the new part in the same call and
  reports it as `parts_created`; without a name the reply carries a note asking for one.
- `rename(element_id, name)` renames a tab (a Part Studio or Assembly); add `part_id` to rename one
  part. Onshape also renames the matching *BOM* tab of an assembly.
- **Extrudes are named with their size**: `Extrude 8 mm`, `Cut 3 mm` (operation `remove`),
  `Extrude 8 mm (symmetric)`. With a variable depth the name shows the variable in brackets
  (`Extrude (head_h)`), so it stays true when the variable changes. Never `#head_h`: in a feature
  name Onshape reads `#word` as a parameter of that feature and shows `?` (variables themselves are
  named after the variable, `d`, not `#d`). **Holes** get no size: pass `hole=true` and the extrude
  is named `Hole`. When you change an automatically named extrude's depth with `update_feature`
  (one feature at a time), its name is refreshed too.
- Names you chose yourself are kept, and an explicit `name` always wins.

## Variables

Key dimensions live in **variables** (Onshape's `#name`), so one change reshapes the model.

```
set_variable(name="shank_d", value=10)              # a length in your unit (mm by default)
set_variable(name="pitch", value="1.5 mm")
set_variable(name="half_d", value="#shank_d / 2")   # may use other variables
list_variables()                                     # name, expression, current value
```

Use `"#name"` wherever a size is needed:

| Where | Example |
|---|---|
| `create_sketch` circle | `{"type": "circle", "center": [0, 0], "diameter": "#shank_d"}` (or `radius`) |
| `create_sketch` hexagon | `{"type": "hexagon", "center": [0, 0], "across_flats": "#head_af"}` |
| `create_sketch` rectangle | `{"type": "rectangle", "corner1": [0, 0], "width": "#w", "height": "#h"}` |
| `extrude` depth, `fillet` radius, `chamfer` distance | `"depth": "#length"` |
| thread pitch (ThreadCreator `p`) | `"p": "#pitch"` |

A sketch size must be a plain `#name` (it carries a dimension constraint on that variable); for
arithmetic make a derived variable (`half_d` above). Points and positions stay plain numbers. The
`hexagon` shape is a *regular* hexagon with flats on top and bottom, fully constrained, so its
across-flats size follows the variable.

## Configurations (M10 or M18)

Only when you ask for a selectable variant, for example *"make the bolt selectable as M10 or M18, and
let me choose the length"*.

```
set_configuration_input(name="Size", kind="list", options=["M10", "M18"])
set_configuration_input(name="Length", kind="length", default=40, minimum=20, maximum=100)

set_variable(name="d",       by_option={"Size": {"M10": 10,  "M18": 18}})
set_variable(name="pitch",   by_option={"Size": {"M10": 1.5, "M18": 2.5}})
set_variable(name="head_af", by_option={"Size": {"M10": 16,  "M18": 27}})
set_variable(name="head_h",  by_option={"Size": {"M10": 6.4, "M18": 11.5}})
set_variable(name="nut_h",   by_option={"Size": {"M10": 8.4, "M18": 15.8}})
```

(ISO 4017 / 4032 coarse values.) A number input such as `Length` can be used directly as `#Length`.
Every option of a list input needs a value. Then model as above with `#d`, `#head_af`, … .

A count can be configured too: a `number` variable (`set_variable(name="spokes", kind="number", by_option=...)`)
used as `count="#spokes"` in `circular_pattern` makes the number of spokes follow the selected variant.
A plain variable driving the count was checked live (3 and 6 spokes); a variable configured *by an
input* driving it was not.

Look at one variant without changing the default: `get_part_properties`, `get_body_details` and
`capture_screenshot` take `configuration={"Size": "M18"}`; `insert_instance` takes the same to put
that variant into an assembly. `get_configuration` lists the inputs.

**Inputs made in Onshape's own UI** work the same way. Onshape gives them a generated ID
(`List_gfWXlOaT2Zmfyo`) that differs from the label you see (`Configuration`), and their options
have IDs too (`_3Spoke` for `3Spoke`). `get_configuration` shows the label and, beside it, the `id`
and `option_ids`; every tool that takes a configuration or `by_option` accepts the labels (any
letter case) or the IDs, translates them for Onshape, and refuses an unknown name with the list of
valid ones instead of silently measuring the default variant:

```
get_part_properties(..., configuration={"Configuration": "3Spoke"})
set_variable(name="spokes", kind="number", by_option={"Configuration": {"Default": 0, "3Spoke": 3, "6Spoke": 6}})
```

**Checked live:** a list input, a variable that depends on it, and the geometry following
`configuration=Size=M18` (a circle driven by that variable measured 10 mm by default and 18 mm for
M18); and an input shaped like a UI-made one (generated ID, different label, `_3Spoke` option IDs),
with a configured variable built the way `set_variable` builds it: volumes per variant, asked for by
label and by ID, matched pi*r^2*h exactly (785.4 / 1570.8 / 2356.2 mm3), and a wrong option was
refused. `set_variable` itself could not run live that day because it reads the feature list first
(see [rate limits](#rate-limits)); on a fresh account it then did, including a variable configured by
an input, a spoke count driven by a configuration (17979.92 / 23393.46 mm3) and the whole bolt and nut
(without threads) under `Size=M10`, `M18` and `Length=60`. **Not checked live:** the threads on that
bolt and nut under M18 (a new account has no ThreadCreator), exporting a variant (`export_model` has
no `configuration` option yet), and a number input used inside an expression.

## Assemblies

Only when you ask for one.

```
insert_instance(assembly_id, source_element_id, part_id=<bolt>, fixed=True)   # ground the first part
insert_instance(assembly_id, source_element_id, part_id=<nut>)
add_mate(assembly_id, mate_type="cylindrical",
         first  = {"instance_id": <bolt instance>, "entity_id": <circular end edge>, "at": "center"},
         second = {"instance_id": <nut instance>,  "entity_id": <a nut face>,        "at": "centroid"})
```

- `insert_instance` inserts one part (or the whole Part Studio) and returns the new instance IDs.
  Ground the first part with `fixed=True` so the second one is the part that moves.
- `add_mate` joins two instances. Mate types: `fastened`, `revolute`, `slider`, `planar`,
  `cylindrical` (a nut that can turn and slide on a bolt), `pin_slot`, `ball`, `parallel`. `flip`
  turns the second part around; `offset=[x, y, z]` separates the connectors.
- Each side is a spot on a part instance: an `entity_id` (a face, edge or vertex ID from
  `get_body_details` of its Part Studio) and `at` - `center` of a circular edge, `centroid` of a
  face, `point`, `mid_point`, `origin` (the part's origin, no entity needed) or a cylinder's
  `top_axis` / `mid_axis` / `bottom_axis`. `center` and `centroid` are the ones checked live.
- The reply carries the **real status** and both instances' **positions**: read them. Accepting a
  mate says nothing about it working (see below).
- `get_assembly` lists instances, placements and mates.

**Checked live** (bolt and nut of this project): a hand-made cylindrical mate was read back and
replayed; `fastened` from the bolt's origin to the nut's top face put that face at z = 0.000 mm,
`offset=[0, 0, 5]` moved it exactly 5.000 mm, and `cylindrical` aligned the axes (tilt 0.0000°).
A threaded face's **centroid can sit about 0.2 mm off the thread's axis** (the helix makes the face
asymmetric), so a nut mated on its threaded face is that far off-axis; mate on a plain face or the
part's origin when that matters.

**Why my first mates silently did nothing.** Onshape accepted them with status `OK` and moved
nothing. A real mate embeds two *implicit mate connectors* and points at them by feature ID; I had
put the geometry queries straight into the mate. The opaque compressed query the UI also stores is
not needed. `add_mate` builds the real structure (`mates.py`).

## Fewer requests (batching)

Onshape has **no general batch endpoint**, and no way to add several Part Studio features in one
request. It does have one multi-edit call, `POST …/features/updates`, which changes **existing**
features in a single request (checked live: two variables changed in one request and the whole model
followed; only the supplied parameters are touched, and a `name` sent along is ignored). The server
uses it where it fits:

| You want | Call | Onshape requests |
|---|---|---|
| Set several variables | `set_variable(variables={"d": 10, "pitch": 1.5, "head_h": 6.4})` | 1 read + 1 update for existing ones + 1 per **new** variable |
| Change several features | `update_feature(updates=[{"feature_id": …, "parameters": {…}}, …])` | 2 in total (1 read, 1 update), however many |
| Use `#variables` in a sketch | `create_sketch` | 1 FeatureScript call; the feature list is only read to explain a failure |
| Add ThreadCreator several times | `add_custom_feature` | the 9 MB spec download is reused for 5 minutes |

New features (sketches, extrudes, variables, threads) still cost one request each; that is Onshape's
limit, not the server's. The batch form of `update_feature` does not rename features; a single update
does.

## Rate limits

Onshape limits **each endpoint separately per day**. Seen live on a free plan: after many test runs,
`GET .../partstudios/.../features` answered `429` with `Retry-After: 80721` (about 22 hours) while every
other endpoint still allowed 3000 calls. `POST .../featurescript` ran out the same way after a long
test session (every origin-plane lookup, `create_sketch` on a named plane, tight bounding boxes and
variable evaluation use it); plane IDs are cached only inside one running server. The server now stops retrying such a limit and says when it
resets. To use fewer of those calls: do not poll `list_features` after every step (modeling tools
already report each feature's status), and prefer one `list_variables` over several reads.
