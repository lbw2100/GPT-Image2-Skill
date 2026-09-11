#!/usr/bin/env python3
"""Batch-generate reskinned images from a manifest produced by scan.py.

Each manifest entry needs a non-empty "prompt" (the agent fills these in
after reverse-engineering a shared style block from 2-3 representative
assets, then writing per-asset deltas on top of it -- see the pipeline
README). Calls the `gpt-image` CLI once per asset through the edits
endpoint (-i, reference-based) so layout/composition survives better than
plain text-to-image, at the size scan.py solved for that asset.

By default, appends a cleanup clause to every prompt instructing the model to
drop watermarks, source-site URLs, and other studios' logos/character art
from the background -- -i reference edits otherwise reproduce those verbatim
along with everything else in frame. Pass --keep-source-artifacts to disable
this and edit strictly as-is.

Usage:
  render.py manifest.json --stage draft --quality medium --out-dir drafts/
  render.py manifest.json --stage final --quality high --out-dir final_raw/ --only hero,icon_07
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

DEFAULT_CLEANUP_CLAUSE = (
    " Remove any watermarks, source-site URLs/handles, third-party studio logos, and other "
    "studios' distinctive character art or wordmarks visible in the reference image's "
    "background; do not reproduce them in the output."
)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("manifest", type=Path)
    ap.add_argument("--stage", required=True, choices=["draft", "final"])
    ap.add_argument("--model", default="gpt-image-2.5-sunburst")
    ap.add_argument("--quality", default="medium")
    ap.add_argument("--out-dir", type=Path, required=True)
    ap.add_argument("--only", help="comma-separated asset ids; default is every asset with a prompt set")
    ap.add_argument("--keep-source-artifacts", action="store_true",
                     help="don't append the default watermark/third-party-logo cleanup clause")
    args = ap.parse_args()

    data = json.loads(args.manifest.read_text())
    only = set(args.only.split(",")) if args.only else None
    args.out_dir.mkdir(parents=True, exist_ok=True)

    for asset in data["assets"]:
        if only is not None and asset["id"] not in only:
            continue
        if not asset.get("prompt"):
            print(f"skip {asset['id']}: no prompt set", file=sys.stderr)
            continue

        prompt = asset["prompt"]
        if not args.keep_source_artifacts:
            prompt += DEFAULT_CLEANUP_CLAUSE

        out_path = args.out_dir / f"{asset['id']}.png"
        cmd = [
            "gpt-image", "--model", args.model,
            "-p", prompt,
            "-i", asset["source"],
            "--size", asset["gen_size"],
            "--quality", args.quality,
            "-f", str(out_path),
        ]
        print(f"generating {asset['id']} ({args.stage}, {args.quality}, {args.model})...", file=sys.stderr)
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.stderr.strip():
            print(result.stderr.strip(), file=sys.stderr)  # surfaces warn_size_mismatch etc.
        if result.returncode != 0:
            asset[f"{args.stage}_error"] = result.stderr.strip() or f"exit {result.returncode}"
            continue
        asset[f"{args.stage}_output"] = str(out_path.resolve())
        asset["status"] = args.stage

    args.manifest.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
