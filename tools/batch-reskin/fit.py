#!/usr/bin/env python3
"""Force every rendered image back to its asset's exact original dimensions and compress.

Reads {stage}_output from the manifest and smart-crops (fills the target box,
cropping toward the salient region) each one to orig_width x orig_height via
vipsthumbnail. This is the step that restores exact irregular original sizes
gpt-image cannot generate directly -- icons below the API's minimum pixel
count, banners beyond its 3:1 aspect cap, and any size that isn't a 16px
multiple.

Defaults to lossless PNG output -- no compression unless you ask for it.
Pass --format webp/jpg (with --quality) to actually compress.

Requires libvips (`brew install vips` / `apt install libvips-tools`).

Usage:
  fit.py manifest.json --stage final --out-dir output/                       # exact size, lossless PNG
  fit.py manifest.json --stage final --out-dir output/ --format webp --quality 82   # + compressed
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("manifest", type=Path)
    ap.add_argument("--stage", required=True, choices=["draft", "final"])
    ap.add_argument("--out-dir", type=Path, required=True)
    ap.add_argument("--format", default="png", choices=["webp", "jpg", "png"],
                     help="default png = lossless, no compression. Pass webp/jpg to compress.")
    ap.add_argument("--quality", type=int, default=82, help="ignored for --format png (always lossless)")
    ap.add_argument("--smartcrop", default="attention", choices=["attention", "centre", "entropy", "none"],
                     help="crop strategy when the source aspect doesn't match the target box; 'none' pads/letterboxes instead")
    args = ap.parse_args()

    data = json.loads(args.manifest.read_text())
    args.out_dir.mkdir(parents=True, exist_ok=True)

    for asset in data["assets"]:
        src = asset.get(f"{args.stage}_output")
        if not src:
            continue
        out_path = (args.out_dir / f"{asset['id']}.{args.format}").resolve()
        size = f"{asset['orig_width']}x{asset['orig_height']}"
        # vipsthumbnail's -o/--path resolves a relative spec against the INPUT
        # file's directory, not cwd -- always pass an absolute path here.
        out_spec = str(out_path) if args.format == "png" else f"{out_path}[Q={args.quality}]"
        cmd = ["vipsthumbnail", src, "--size", size, "-o", out_spec]
        if args.smartcrop != "none":
            cmd += ["--smartcrop", args.smartcrop]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            print(f"error fitting {asset['id']}: {result.stderr.strip()}", file=sys.stderr)
            continue
        before = Path(src).stat().st_size
        after = out_path.stat().st_size
        print(f"{asset['id']}: -> {size}  {before/1024:.0f}KB -> {after/1024:.0f}KB", file=sys.stderr)
        asset["fit_output"] = str(out_path.resolve())

    args.manifest.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
