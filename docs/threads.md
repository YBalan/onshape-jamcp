# Threads and custom features

Onshape's built-in features cannot make a helical thread. Threads come from a **custom feature**: a
Feature Script (usually *ThreadCreator*) that you add to your Onshape account. This server can list
and use any such script. This page is the one place for the thread workflow; the general mechanism
is in the [how-to](how-to.md#use-a-custom-feature-such-as-thread-creator).

- [What you need](#what-you-need)
- [Make the script visible to the assistant](#make-the-script-visible-to-the-assistant)
- [The one rule that matters: the face is the major diameter](#the-one-rule-that-matters-the-face-is-the-major-diameter)
- [Worked example: an M10 bolt and nut](#worked-example-an-m10-bolt-and-nut)
- [ThreadCreator options](#threadcreator-options)
- [Check that the thread is right](#check-that-the-thread-is-right)
- [When something goes wrong](#when-something-goes-wrong)
- [What has and has not been verified](#what-has-and-has-not-been-verified)

## What you need

1. **ThreadCreator in your Onshape account.** In a Part Studio, open the toolbar's *Custom features*
   drop-down; if *ThreadCreator* is not listed, choose *Add custom features* and pick it. Onshape
   stores this as an account setting - see Onshape's
   [Add Custom Features](https://cad.onshape.com/help/Content/customfeature.htm) help. Any copy of
   the ThreadCreator script works; this server was tested with two of them.
2. **A model with a cylindrical face to thread**: a shank for an external thread, a hole for an
   internal one (see [the rule below](#the-one-rule-that-matters-the-face-is-the-major-diameter)).
3. **Write access** (not `ONSHAPE_READ_ONLY=true`): adding a feature modifies the document.

## Make the script visible to the assistant

Onshape's API does not list the scripts on your toolbar, so `list_custom_features` shows ThreadCreator
only once the Part Studio *knows* it. Pick one:

- **Use it once by hand** in that Part Studio (click ThreadCreator, thread anything, you may delete the
  result). From then on `list_custom_features` finds it, with its exact namespace. Simplest.
- **Give the assistant the Feature Studio URL** as `feature_studio_url`: the browser URL of the tab
  that defines the script. If that tab is in a *different* document, it must be a **version** URL
  (`.../documents/<did>/v/<version>/e/<eid>`), because Onshape links custom features to a version.

Just tell the assistant, for example: *"Use ThreadCreator (feature studio
https://cad.onshape.com/documents/…/v/…/e/…) to thread the shank."*

## The one rule that matters: the face is the major diameter

ThreadCreator takes the **face you select as the thread's major diameter** (checked live). The
direction of the thread follows from `internalThreads`:

| You are making | Select | Model it at | `internalThreads` | What the script does |
|---|---|---|---|---|
| A bolt / screw (external) | the shank cylinder | the **major** Ø (Ø10 for M10) | `false` | cuts grooves inward |
| A nut / tapped hole (internal) | the hole | the **major** Ø (Ø10 for M10), **not** the tap-drill size | `true` | adds ridges inward |

So for an M10 × 1.5 nut, cut a **Ø10** hole. A Ø8.376 hole (the minor diameter) is accepted without
any error but yields an M8.4 thread. `majorDiameter` and `diameterType` made no difference once a
face was selected, so the face's diameter is what counts.

**Defaults you must override for metric work** (the script's own defaults are ANSI/1 mm/internal):
`threadProfile` → `ISO_STD`, `p` (pitch) → e.g. `1.5`, and `internalThreads` → `false` for a bolt.

## Worked example: an M10 bolt and nut

> *In "Thread test", make an M10 × 40 hex bolt (16 mm across flats, 6.4 mm head) and an M10 hex nut
> (16 mm across flats, 8.4 mm thick), threaded M10 × 1.5 with ThreadCreator. Next to each other.*

Bolt (all lengths mm):

1. `create_sketch` polygon, six points on a circle of radius 9.2376 (= 16 / √3) → `extrude` depth 6.4,
   `new` (the head).
2. `create_sketch` circle Ø10 on the same plane → `extrude` depth 40, `add`,
   `opposite_direction: true` (the shank, pointing the other way).
3. Optional: `chamfer` the tip edge.
4. `get_body_details` → the cylinder face of the shank.
5. `add_custom_feature`:

```
feature_type: "threadCreator"
parameters: { "refChoice": ["<shank face id>"], "threadProfile": "ISO_STD", "internalThreads": false,
              "p": 1.5, "lengthType": "FULL", "screwfriendlyname": "M10-1.5" }
```

Nut:

1. Hexagon, 16 across flats, `extrude` 8.4 (`new`), then a **Ø10 circle** → `extrude` 8.4 with `remove`.
2. `get_body_details` → the hole's cylinder face.
3. `add_custom_feature` with `"internalThreads": true`, the same profile, pitch and name, and that face.

Measured live: the nut thread, made through this server, came back `OK` with +96.5 mm³ of ridges. The
bolt thread (a thread made by hand in Onshape, then replayed through this server on an identical
Ø10 × 40 cylinder) came back `OK` and removed 551 mm³.

## ThreadCreator options

`list_custom_features` shows these live, with your unit. Lengths are in your configured unit (mm
by default), angles in degrees. Anything you leave out keeps the script's default.

| Parameter | In the Onshape dialog | Default | Notes (observed live) |
|---|---|---|---|
| `refChoice` | Face to Thread | - | list of face IDs from `get_body_details`; required |
| `threadProfile` | Thread Profile | `ANSI_STD` | `ISO_STD`, `ISO_SQUARE`, `ANSI_STD`, `ANSI_SQUARE`, `ACME`, `TRAPEZOIDAL` (ISO DIN 103). ISO and ANSI Standard gave identical geometry |
| `internalThreads` | Internal Threads | `true` | **set `false` for a bolt** |
| `p` | Pitch | 1 mm | spacing of adjacent threads: 1.5 for M10 coarse, 1.25 for fine |
| `leftHanded` | Left Hand Thread | `false` | `true` mirrors the helix |
| `numStarts` | Number of Starts | 1 | 2 doubles the lead (`p` stays the spacing between neighbouring threads) |
| `lengthType` | Fully Threaded / # Turns / Distance | `FULL` | `FULL`, `TURNS` (uses `numTurns`) or `LENGTH` (uses `length`) |
| `numTurns` | Num Turns | 1 | with `TURNS`; 4 turns of 1.5 mm = 6 mm |
| `length` | Thread Length | 25 mm | with `LENGTH` |
| `oppositeEnd` | Start At Opposite End | `false` | partial threads start at the face's other end |
| `taperFirstEnd` | Start Taper | `false` | accepted; effect is small |
| `taperFirstEndAngle` | End Taper Angle | 45 deg | with `taperFirstEnd` |
| `leadInPitches` | Lead in Pitches | 1 | accepted; effect is small |
| `screwfriendlyname` | (the dialog title, e.g. "M10-1.5") | - | the feature's label in the feature tree; Onshape's dialog seems to fill it in (e.g. `M10-1.5`), here you set it yourself |
| `majorDiameter`, `diameterType` | Diameter, Diameter Type | 25 mm, `MAJOR` | no effect while a face is selected |

Change a feature afterwards with `update_feature` (for example `{"p": 1.25}`); counts such as
`numStarts` are written as plain numbers.

## Check that the thread is right

"Status OK" only means Onshape did not report an error. Measure:

- **Volume.** `get_part_properties` before and after: an external thread *removes* material (the
  M10 × 40 shank lost ~550 mm³), an internal one *adds* ridges (the nut gained ~97 mm³). The wrong
  sign usually means the face was modeled at the wrong diameter.
- **Minor diameter.** `run_featurescript` with the nut's extrude feature ID (from `list_features`)
  and the hole's axis position; the distance from the axis to the nut's faces is the minor radius
  (4.25 mm, i.e. Ø8.5, for the nut above):

```
function(context is Context, queries) {
    var faces = qOwnedByBody(qCreatedBy(makeId("<nut extrude feature id>"), EntityType.BODY), EntityType.FACE);
    var axis = line(vector(0.04, 0, 0) * meter, vector(0, 0, 1));   // x = 40 mm: the nut's axis
    return evDistance(context, { "side0" : axis, "side1" : faces }).distance / millimeter;
}
```

- **Look.** `capture_screenshot`; threads are visible at isometric size.

## When something goes wrong

| Symptom | What to do |
|---|---|
| `list_custom_features` is empty / no `threadCreator` | The Part Studio does not know the script yet: [use it once by hand, or pass `feature_studio_url`](#make-the-script-visible-to-the-assistant). |
| "...needs a *version* URL" | The Feature Studio is in another document: open it at a version and use that URL. |
| `status: ERROR` | Onshape gives no reason through the API. Check the selected face is a cylinder and the values above; then create one thread **by hand**, read it with `list_features(feature_id=…)` and compare. |
| Thread has the wrong size | The face was modeled at the wrong diameter (see [the rule](#the-one-rule-that-matters-the-face-is-the-major-diameter)); for a nut, the hole must be the major Ø. |
| Bolt came out as an internal thread | `internalThreads` defaulted to `true`; pass `false`. |
| Metric thread has an inch-series profile | `threadProfile` defaulted to `ANSI_STD`; pass `ISO_STD`. |

## What has and has not been verified

Verified live against a real Onshape account (2026-11-16): discovery, both threads above, every
option in the table, and an exact replay of a thread made by hand in the Onshape UI. Not verified:
selecting several faces at once, and the non-default options on an *internal* thread. The measurements
behind this page are in [development](development.md#verification-status-read-this-before-trusting-a-tool).
