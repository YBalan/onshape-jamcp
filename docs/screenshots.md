# Screenshots & token cost

An assistant that models CAD blind is guessing. `capture_screenshot` lets it *look* - but images
cost tokens. onshape-jamcp makes that an explicit choice.

## The toggle

| Mode | How to set | What `capture_screenshot` returns | Token cost |
|---|---|---|---|
| **Review on** (default) | `ONSHAPE_SCREENSHOT_REVIEW=true` or `--screenshot-review` | The image(s) **plus** a JSON summary (paths, size, estimated tokens). The model can see the part. | ≈ `width × height ÷ 750` per image |
| **Review off** | `ONSHAPE_SCREENSHOT_REVIEW=false` or `--no-screenshot-review` | A JSON summary with the **file path(s)** only. | none for the image |
| **Per call** | `capture_screenshot(review=true)` / `(review=false)` | Overrides the setting for that one call. | as above |

In **both** modes the PNG is saved to `<ONSHAPE_OUTPUT_DIR>/screenshots/`, so a human can always
open it - review only decides whether the *model* receives it.

## What it costs

Vision models bill roughly `width × height ÷ 750` tokens per image:

| Size | Tokens per image | 4 views |
|---|---|---|
| 400 × 300 | ~160 | ~640 |
| **800 × 600 (default)** | **~640** | **~2,560** |
| 1024 × 768 | ~1,050 | ~4,200 |
| 1600 × 1200 | ~2,560 | ~10,200 |

The tool's own summary reports `approx_image_tokens` when it sends images. (Some providers
downscale very large images, so big sizes also stop being useful.) A typical modeling session that
checks the part after each of ~10 steps costs ~6,400 tokens at the default size - usually small next
to the conversation itself, but it adds up across long sessions or many views.

## Strategy

- **Default (on) is right for interactive design:** the model catches misplaced holes, wrong
  orientation and inside-out cuts immediately.
- **Turn it off for batch/automated work** (generating many parts, running unattended): the model
  can still verify numerically with `get_part_properties` and `get_body_details`, which are far
  cheaper than an image.
- **Mixed:** keep the setting `false` and ask for a look at key moments: *"take one isometric
  screenshot with review on."* The assistant passes `review=true` for just that call.
- **Cheaper looks:** pass a smaller `width`/`height` (e.g. 400 × 300), ask for one view instead of
  four, or set `ONSHAPE_SCREENSHOT_WIDTH/HEIGHT` globally.
- **Numbers beat pictures for exact checks.** Use `get_part_properties` (size, volume) to verify
  dimensions; use screenshots to verify *shape and placement*.

## Views

`views` takes up to 6 of: `isometric` (default), `front`, `back`, `left`, `right`, `top`, `bottom`.
Each view is one image. Duplicate names are ignored. Other options: `part_id` (render one part),
`width`/`height` (64-2000 px), `show_edges` (default true).

Works for Part Studios and Assemblies. The camera auto-fits the model.

## Privacy

With review on, the image is sent to your LLM provider as part of the conversation, exactly like
text. With review off nothing visual leaves your machine except the request to Onshape itself.

## Where files go

`<ONSHAPE_OUTPUT_DIR>/screenshots/<element>_<view>_<timestamp>_<n>.png`. The default output
directory is `<system temp>/onshape-jamcp`. If the directory is not writable the screenshot is still
returned (when review is on) and `path` is `null`. In Docker, mount a volume to get the files out
(see [getting started](getting-started.md#with-docker)).
