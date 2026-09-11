#!/usr/bin/env python3
"""Scan a source image directory and build a manifest for the batch reskin pipeline.

For each image, records its exact original dimensions (fit.py restores this
exact size at the end of the pipeline) and solves a gpt-image-2/2.5-compliant
generation size: edges are multiples of 16, aspect ratio <=3:1, total pixels
655,360-8,294,400, max edge 3840. Source aspect ratios beyond 3:1 (the API
cannot generate them at all) are capped for generation and restored to the
true ratio later by fit.py's smart-crop.

Usage: scan.py SRC_DIR -o manifest.json
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

MIN_PX = 655_360
MAX_PX = 8_294_400
MAX_EDGE = 3840
MULTIPLE = 16
MAX_ASPECT = 3.0
REFERENCE_EDGE = 1024
EXTS = {".png", ".jpg", ".jpeg", ".webp"}


def image_size(path: Path) -> tuple[int, int]:
    w = int(subprocess.run(["vipsheader", "-f", "width", str(path)], capture_output=True, text=True, check=True).stdout.strip())
    h = int(subprocess.run(["vipsheader", "-f", "height", str(path)], capture_output=True, text=True, check=True).stdout.strip())
    return w, h


def round_multiple(v: float, multiple: int = MULTIPLE) -> int:
    return max(multiple, round(v / multiple) * multiple)


def solve_size(orig_w: int, orig_h: int) -> dict:
    """Solve a valid gpt-image-2/2.5 generation size for one asset.

    Does not try to match orig_w x orig_h exactly -- fit.py's smart-crop
    handles that later. Only needs to (a) respect the API's hard constraints
    and (b) be at least as detailed as the source so downscaling in fit.py
    doesn't upscale a blurry result.
    """
    aspect = orig_w / orig_h
    capped = False
    if aspect > MAX_ASPECT:
        aspect, capped = MAX_ASPECT, True
    elif aspect < 1 / MAX_ASPECT:
        aspect, capped = 1 / MAX_ASPECT, True

    target_edge = max(REFERENCE_EDGE, min(max(orig_w, orig_h), MAX_EDGE))
    if aspect >= 1:
        w, h = target_edge, target_edge / aspect
    else:
        h, w = target_edge, target_edge * aspect
    w, h = round_multiple(w), round_multiple(h)

    def total() -> int:
        return w * h

    if total() < MIN_PX:
        scale = (MIN_PX / total()) ** 0.5
        w, h = round_multiple(w * scale), round_multiple(h * scale)
    elif total() > MAX_PX:
        scale = (MAX_PX / total()) ** 0.5
        w, h = round_multiple(w * scale), round_multiple(h * scale)

    # Multiple-of-16 rounding can undershoot/overshoot a scale correction by
    # one step, so nudge in 16px increments (both edges, to hold aspect)
    # until the bounds actually hold rather than trust the arithmetic above.
    guard = 0
    while total() < MIN_PX and guard < 50:
        w, h = w + MULTIPLE, h + MULTIPLE
        guard += 1
    while (total() > MAX_PX or max(w, h) > MAX_EDGE) and guard < 100:
        w, h = max(MULTIPLE, w - MULTIPLE), max(MULTIPLE, h - MULTIPLE)
        guard += 1

    return {"gen_width": w, "gen_height": h, "aspect_capped": capped}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("src_dir", type=Path)
    ap.add_argument("-o", "--out", type=Path, default=Path("manifest.json"))
    args = ap.parse_args()

    assets = []
    for path in sorted(args.src_dir.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in EXTS:
            continue
        w, h = image_size(path)
        solved = solve_size(w, h)
        assets.append({
            "id": path.stem,
            "source": str(path.resolve()),
            "orig_width": w,
            "orig_height": h,
            "gen_size": f"{solved['gen_width']}x{solved['gen_height']}",
            "aspect_capped": solved["aspect_capped"],
            "prompt": None,
            "status": "pending",
        })
        flag = " [aspect capped -- fit.py will crop further]" if solved["aspect_capped"] else ""
        print(f"{path.name}: {w}x{h} -> gen {solved['gen_width']}x{solved['gen_height']}{flag}", file=sys.stderr)

    args.out.write_text(json.dumps({"assets": assets}, ensure_ascii=False, indent=2))
    print(f"wrote {len(assets)} assets to {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
