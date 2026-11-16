# Re-test checklist for when Onshape's daily limits are gone

> **Status (2026-11-16 night): done on a fresh account.** Sections 0, 1, 2 (except the reference
> studios), 3 (without threads) and the unblocked items of section 4 were run in full against a
> scratch document: 87 checks, all passed. The results are in
> [development](development.md#verification-status-read-this-before-trusting-a-tool) ("Re-test on a
> fresh account"). **Still open:** the threads on the M10 / M18 bolt and nut (a new account has no
> ThreadCreator: add it to the account's custom features first), the tight bounding box on a swept
> surface, the 5-minute spec cache, `boolean(operation="intersect", keep_tools=true)`, and the OAuth
> cases (one-hour expiry, revoked grant, enterprise URL). The reference studios were read from the
> old public document: `Circular Pattern + Configuration + Variable` holds only a number variable and an
> empty sketch, which is why it has no parts. Run the rest of this page only when one of those matters.

On 2026-11-16 a long live session used up two of Onshape's **per-endpoint daily limits** on the free
plan, so some things could not be checked live that day (details in
[parametric modeling](parametric.md#rate-limits)):

| Limited endpoint | Seen | Blocks |
|---|---|---|
| `GET .../partstudios/.../features` | `429`, `Retry-After` about 22 h, later 20 h | reading the feature list: `list_features`, `list_variables`, `set_variable`, `update_feature`, reading hand-made features |
| `POST .../partstudios/.../featurescript` | `429` with about 20 h left when first seen | every FeatureScript lookup: origin-plane names, `sketch:<id>` construction lines, tight bounding boxes, `#variable` values |

Both limits appear to reset at the same time of day (the remaining wait read 22.4 h, then 20.5 h, then
20.0 h over the session), so expect them back together; the check below tells you for sure.
This page lists what to run once they are gone, what the answer should be, and what to do if it is not.

## 0. Check that the limits are gone

Ask the assistant for both of these; each must succeed (a `429` message names the hours left):

1. `list_features` on any Part Studio, for example `M10 Fasteners`.
2. `run_featurescript` with `function(context is Context, queries) { return 1; }`.

Other endpoints (parts, mass properties, body details, bounding boxes, POST features, configuration)
were never limited, so they do not need this check.

**Spend the quota carefully.** The limit was exhausted by a long session in which every step started a
fresh server, and each server looks the origin planes up again (a plane ID is cached only inside one
running server). So: do the FeatureScript items below **in one session**, keep one scratch Part Studio
tab per family, and delete the tabs at the end. Origin-plane IDs seen live: Top `JDC`, Front `JCC`,
Right `JEC` (pass them as `plane` when the lookup itself is not what you are testing).

## 1. Needs FeatureScript (`POST .../featurescript`)

- [ ] **Construction line as the axis: `revolve` without `axis`.** On the Front plane make
  `create_sketch(plane="Front", shapes=[{"type":"rectangle","corner1":[10,0],"corner2":[20,30]},
  {"type":"line","start":[0,0],"end":[0,30],"construction":true}])`, then
  `revolve(sketch_feature_id=<that sketch>, part_name="Sleeve")`.
  **Expect** status `OK`, volume `28274.33 mm3` (= pi x (20^2 - 10^2) x 30), 1 part created.
  *If it fails:* the lookup (`fs.construction_edge_ids_script`, a `qConstructionFilter(qCreatedBy(...))`
  query) returned 0 or 2 edges. Print what it returns for the sketch; if Onshape does not tag
  construction edges that way, query the sketch's edges and filter by `isConstruction` instead.
- [ ] **Construction line as the axis: `circular_pattern(axis="sketch:<id>")`.** Hub (circle r 20,
  extrude 10, new), one spoke (rectangle `[15,-3]`-`[50,3]`, extrude 10, **add**), then a sketch on the
  Front plane with one construction line `[0,0]`-`[0,10]` (the world Z axis) and
  `circular_pattern(count=3, axis="sketch:<that sketch>", feature_ids=[<spoke extrude>])`.
  **Expect** `OK` and `17979.92 mm3` (hub 12566.37 + 3 x 1804.51).
  Also try a sketch with **no** construction line and one with **two**: both must be refused with
  "has 0 / 2 construction lines" and nothing added.
- [ ] **World directions by name.** Block `[20,0]`-`[30,10]`, extrude 5 (new); then
  `linear_pattern(part_ids=[block], count=4, distance=20, direction="x" | "y" | "z")`.
  **Expect** `OK`, volume x4 (`2000 mm3`) and: x -> x range 20..90; **y -> y range 0..70** (the Front
  plane's normal points to -Y, so the tool flips it: if the range is -60..10 the flip is wrong); z -> z
  range 0..65. Delete the feature between runs. An earlier set of `ERROR`s on exactly this path was
  most likely caused by a bad part ID in the test script (the same calls with plane IDs worked once the
  ID was fixed), so this is the first clean run of the lookup itself.
- [ ] **Plane names for `mirror`.** `mirror(part_ids=[block], plane="Right")` -> 2 parts, volume x2,
  x range -30..30; `plane="Front"` -> y range -10..10; `plane="Top"` -> z range -5..5.
- [ ] **Named sketch planes** (`create_sketch(plane="Top" | "Front" | "Right")`): the sketch lands on
  the right plane (bounding box of a small extrude). Write down the IDs the lookup returns; they should
  be `JDC`, `JCC`, `JEC`.
- [ ] **Tight bounding box.** `get_part_properties(part_id=<nut>)` on `M10 Fasteners` with the default
  `tight=true`: **expect** a z size of `8.4 mm`, no `bounding_box_note`, and a loose box (`tight=false`)
  that is larger (about 9.4 mm).
- [ ] **`#variables` in sketches** (`create_sketch` resolves their values with one FeatureScript call):
  after section 2's `set_variable`, a circle with `"diameter": "#d"` has the variable's size.

## 2. Needs the feature list (`GET .../features`)

- [ ] **`set_variable` single, multi and by option.** In a scratch tab: `set_variable(name="d", value=10)`
  (created), again with `value=12` (updated in place, same feature ID), then
  `set_variable(variables={"d": 14, "pitch": 1.5, "head_h": 6.4})`. **Expect** `OK` each time and, with
  `ONSHAPE_LOG_LEVEL=DEBUG`, **one** `features/updates` request for the multi call (not one per
  variable). Then `set_configuration_input(name="Size", options=["M10","M18"])` and
  `set_variable(name="d2", by_option={"Size": {"M10": 10, "M18": 18}})`.
- [ ] **`list_variables`.** Values in your unit; a configured variable shows `configured_by` and
  `by_option`. On an input made in Onshape's UI the **labels** appear, not the generated IDs.
- [ ] **`update_feature(updates=[...])`** changes two features in one request, and an extrude's
  automatic name (`Extrude 8 mm`) follows a changed depth.
- [ ] **Read the hand-made reference features** in `Claude Test` with `list_features` and
  `list_features(feature_id=...)`: tabs `Revolve`, `Mirror`, `Shell+LinearPattern` and
  `Circular Pattern + Configuration + Variable`. Compare each with what the matching tool sends
  (parameter IDs, enum values, query shapes). Things to look for: whether their circular pattern is a
  *feature* pattern with `fullFeaturePattern`, how the count is written (`#variable`?), how the
  configured variable is built, whether anything is suppressed or sits behind the rollback bar.
- [ ] **Why the configuration studio has no parts** in any variant (Default, 3Spoke, 6Spoke): a feature
  list will show whether it is unfinished, suppressed or rolled back.
- [ ] **The status read-back of `add_custom_feature`** (it re-reads the list because the add reply can
  say `OK` for a failing feature) and the 5-minute feature-spec cache: two `add_custom_feature` calls
  in a row should fetch `featurespecs` once.

## 3. Needs both: the full parametric builds

- [ ] **A spoke count driven by a configuration.** Scratch tab: input
  `set_configuration_input(name="Configuration", options=["Default","3Spoke","6Spoke"])`; variable
  `set_variable(name="spokes", kind="number", by_option={"Configuration": {"Default": 3, "3Spoke": 3,
  "6Spoke": 6}})`; hub and spoke as in section 1; `circular_pattern(count="#spokes", axis=<hub cylinder
  face>, feature_ids=[<spoke extrude>])`. **Expect** `get_part_properties(configuration={"Configuration":
  "3Spoke"})` = `17979.92 mm3` and `"6Spoke"` = `23393.46 mm3`. (A plain variable driving the count was
  checked live; one configured by an input was not.) Then rebuild the same in the style of the reference
  studio and compare the two.
- [ ] **The configurable M10 / M18 bolt and nut with threads** ([parametric](parametric.md#configurations-m10-or-m18)):
  variables per `Size`, bolt and nut, ThreadCreator on both. **Expect** under `Size=M18`: bolt shank
  O18 mm, hex across flats 27 mm, head 11.5 mm, nut 15.8 mm high; both thread features `OK`; the nut
  hole is the *major* diameter (O18). The M10 values (O10, 16 AF, 6.4 / 8.4 mm) must be unchanged.
  `export_model` has no `configuration` option yet; exporting a variant is a missing feature, not a
  retest.

## 4. Not blocked, just not run yet

These need no limited endpoint; run them in any scratch tab:

- [ ] `circular_pattern` / `linear_pattern` / `mirror` with `face_ids`.
- [ ] `merge_with` with a real merge (`operation="add"` copies overlapping a part, scoped to it).
- [ ] A second-direction **feature** pattern (grid of a feature).
- [ ] `boolean` `keep_tools=true` (subtract and intersect leave the tool part).
- [ ] `revolve(symmetric=true, angle=90)` and `shell(opposite_direction=true)`.
- [ ] `mirror(operation="add")` of a part that *touches* the plane: the copy should merge into one part.
- [ ] `insert_instance(configuration={"Configuration": "6Spoke"})` into an assembly: the variant that
  lands in the assembly (labels resolved to IDs).
- [ ] OAuth over a real one-hour expiry (renews on its own), after the grant is revoked in Onshape
  (a clear "sign in again" message) and with an enterprise `ONSHAPE_OAUTH_URL`.

## 5. After the re-test

1. Move each item that passed from "Not verified" to "Verified" in
   [development](development.md#verification-status-read-this-before-trusting-a-tool), and remove the
   "not yet checked live" notes in [how-to](how-to.md#repeat-mirror-revolve-shell-and-combine) and
   [parametric](parametric.md).
2. For anything that failed: fix the code, add a test that would have caught it, and record the cause
   (the earlier lessons - `fullFeaturePattern`, `massAsGroup=false` - came exactly this way).
3. Delete the scratch tabs, update the changelog, and tick this list.
4. Regenerate `docs/tools.md` (`uv run python scripts/gen_tools_doc.py`) if a tool description changed.
