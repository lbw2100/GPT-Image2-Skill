# batch-reskin

Batch pipeline on top of the `gpt-image` CLI: reverse-engineer a game's art
style, regenerate a whole asset folder under a new brand/theme, and restore
every image to its own original (often irregular) pixel dimensions.

Not upstream code — lives outside `skills/` and `src/` on purpose so a future
`git fetch upstream && git checkout <sha> -- .` sync never has a path
collision with it. Requires the `gpt-image` CLI on `PATH` (from this repo)
and [libvips](https://www.libvips.org/) (`brew install vips` /
`apt install libvips-tools`) for `vipsheader`/`vipsthumbnail`.

## Why this exists

`gpt-image-2`/`2.5` can only generate sizes that satisfy the API's hard
constraints (16px-multiple edges, 655,360–8,294,400 total pixels, aspect
ratio ≤ 3:1, max edge 3840px — see `skills/gpt-image/references/models.md`).
Real asset packs don't respect any of that: 64×64 icons, 3000×200 banners,
373×211 odd crops. This pipeline generates at the nearest API-legal size and
then force-fits the result back to the asset's *exact* original dimensions
with `vipsthumbnail --smartcrop`, so what comes out the other end matches
what went in, pixel for pixel.

## Workflow

```
scan.py  →  (you/agent fill in prompts)  →  render.py --stage draft
    → (human picks keepers)  →  render.py --stage final --only ...
    → fit.py
```

1. **`scan.py SRC_DIR -o manifest.json`** — walks `SRC_DIR`, records each
   asset's real `orig_width`/`orig_height`, and solves a compliant
   `gen_size` for it. Aspect ratios beyond 3:1 are capped for generation
   (flagged `aspect_capped: true`) and restored to the true ratio by
   `fit.py`'s crop in the last step.

2. **Fill in prompts.** Reverse-engineer 2–3 *representative* assets (not
   every one — one game's pack shares an art direction) using the
   `get-prompt-from-image` skill to build one shared style block, then for
   each manifest entry write `"prompt"` as that shared block plus whatever
   differs for that asset plus the reskin brief (new brand name/text, theme
   changes). This step is manual/agent-driven — there's no API for it.

3. **`render.py manifest.json --stage draft --quality medium --out-dir drafts/`**
   — one `gpt-image -i <source> --model gpt-image-2.5-sunburst --size
   <gen_size>` call per asset with a prompt set. `-i` (edit endpoint, not
   text-to-image) keeps layout/composition close to the source — measurably
   closer than pure text-to-image, which tends to drop secondary elements
   (subtitles, side ornaments). Note `-i` also preserves *everything else*
   in the source, including any third-party watermarks or other studios'
   icons still in frame — say so explicitly in the prompt if those need to
   go, don't assume the model drops them on its own.
   Cheap `--quality low/medium` first; a full pack at `high` is real money.

4. **Human review.** Pick the keepers by asset id.

5. **`render.py manifest.json --stage final --quality high --out-dir final_raw/ --only id1,id2,...`**
   — re-render only the selected assets at full quality.

6. **`fit.py manifest.json --stage final --out-dir output/ --format webp --quality 82`**
   — smart-crops each `final_raw` image to its manifest `orig_width` x
   `orig_height` (exact) and writes it compressed. `--smartcrop attention`
   (default) crops toward the salient region when the generated aspect
   doesn't match the target box; pass `--smartcrop none` to letterbox
   instead of cropping when nothing may be lost off-canvas (e.g. UI icons
   with meaningful corners).

Re-run step 6 with `--stage draft --format png` any time on the drafts to
preview at exact size before spending on a `final` pass.

## Gateway size drift

Your API gateway may ignore `--size` outright (observed: requested
`1024x1024`, got back `1254x1254` — not even a 16-multiple). That's fine —
`render.py` doesn't trust the request size, it only records whatever file
`gpt-image` actually wrote; `fit.py` re-measures and force-fits from there.
This is the normal path in this pipeline, not an edge case.

## Manifest schema

```jsonc
{
  "assets": [
    {
      "id": "icon_07",
      "source": "/abs/path/src/icon_07.png",
      "orig_width": 373, "orig_height": 211,
      "gen_size": "1088x624", "aspect_capped": false,
      "prompt": "...",                 // you fill this in
      "status": "final",               // pending -> draft -> final
      "draft_output": "/abs/.../drafts/icon_07.png",
      "final_output": "/abs/.../final_raw/icon_07.png",
      "fit_output": "/abs/.../output/icon_07.webp"
    }
  ]
}
```
