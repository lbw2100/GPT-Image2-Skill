#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "openai>=1.55",
#     "python-dotenv>=1.0",
# ]
# ///
"""General-purpose CLI for OpenAI GPT Image 2.

Mirrors the two official endpoints from the OpenAI cookbook using the official
`openai` Python SDK:

    client.images.generate(...)   — text → image          (no  -i)
    client.images.edit(...)       — text + image(s) → image (with -i; mask via -m)

Every documented parameter is exposed as a flag. Sizes are validated locally
against the official gpt-image-2 constraints (16px edges, 1:3–3:1 aspect,
655,360–8,294,400 total pixels) before any API call, and the echoed response
size is checked so gateways that ignore --size are caught. Reads
OPENAI_API_KEY and OPENAI_BASE_URL from process env, then
~/.config/gpt-image/env, then .env, then ~/.env without overriding existing
env. Writes the returned PNG/JPEG/WebP bytes to disk and prints the output
path(s) on stdout.

Exit codes: 0 success, 1 API error, 2 bad args.

Examples:
    # Basic generate, auto filename, 1K square
    gpt-image -p "a cat astronaut on the moon"

    # Named output, portrait 2K, high quality
    gpt-image -p "Chinese tea poster" -f poster.png --size 2k --quality high

    # Edit existing image (colorize, restyle, translate text, etc.)
    gpt-image -p "colorize this manga page" -i page.jpg -f colored.png

    # Multi-reference edit (outfit transfer, pet + brand, etc.)
    gpt-image -p "77 × KFC collab poster" -i cat.png -i kfc_logo.png -f collab.png

    # Alpha-channel inpaint (mask opaque = keep, transparent = regenerate)
    gpt-image -p "replace sky with aurora" -i photo.jpg -m sky_mask.png -f aurora.png

    # Grid of 4, transparent background, webp
    gpt-image -p "isometric chair, minimalist" -n 4 --background transparent --format webp

    # Skill launcher (same implementation, installed skill-folder path)
    uv run "$SKILL_DIR/scripts/generate.py" -p "a cat astronaut on the moon"
"""
from __future__ import annotations

import argparse
import base64
import os
import re
import sys
import urllib.request
from datetime import datetime
import struct
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from openai import APIError, OpenAI


def _load_env_chain() -> None:
    """Resolve OPENAI_* settings without overriding runtime-provided env.

    Order: process env → ~/.config/gpt-image/env → ./.env → ~/.env. Existing
    process env wins so hosted agents or explicit shell exports are not replaced
    by local files. The dedicated config file comes first among the files so
    per-CLI settings are not shadowed by a generic .env meant for other tools.
    """
    load_dotenv(Path.home() / ".config" / "gpt-image" / "env", override=False)
    load_dotenv(Path.cwd() / ".env", override=False)
    load_dotenv(Path.home() / ".env", override=False)


SIZE_SHORTCUTS: dict[str, str] = {
    "1k": "1024x1024",
    "2k": "2048x2048",
    "4k": "3840x2160",
    "portrait": "1024x1536",
    "landscape": "1536x1024",
    "square": "1024x1024",
    "wide": "2048x1152",
    "tall": "2160x3840",
}

DEFAULT_MODEL = "gpt-image-2"
DEFAULT_SIZE = "1024x1024"
DEFAULT_MODERATION = "low"


def slugify(text: str, max_len: int = 30) -> str:
    s = re.sub(r"[^\w\s-]", "", text.lower()).strip()
    s = re.sub(r"[-\s]+", "-", s)[:max_len]
    return s or "image"


def default_output_path(prompt: str, extension: str) -> Path:
    cwd = Path.cwd()
    target_dir = cwd / "fig" if (cwd / "fig").is_dir() else cwd
    stamp = datetime.now().strftime("%Y-%m-%d-%H-%M-%S")
    return target_dir / f"{stamp}-{slugify(prompt)}.{extension}"


def resolve_size(value: str) -> str:
    return SIZE_SHORTCUTS.get(value.lower(), value)


# Official gpt-image-2 size constraints (developers.openai.com, image API reference):
# both edges divisible by 16; aspect ratio within 1:3..3:1; total pixels between
# 655,360 and 8,294,400; max supported resolution 3840x2160; >2560x1440 (2K)
# is officially "experimental".
MIN_PIXELS = 655_360
MAX_PIXELS = 8_294_400
MAX_EDGE = 3840
EXPERIMENTAL_PIXELS = 2_560 * 1_440


def validate_size(value: str, model: str) -> tuple[str, str | None]:
    """Resolve --size and enforce gpt-image-2 constraints locally.

    Returns (resolved_size, warning). Exits with code 2 and a message naming
    every violated constraint, so a bad size never reaches the API. Values for
    other models (dall-e-*, gpt-image-1*) pass through unvalidated because
    their size enums differ.
    """
    resolved = resolve_size(value)
    if not model.strip().lower().startswith("gpt-image-2"):
        return resolved, None
    if resolved == "auto":
        return resolved, None
    m = re.fullmatch(r"(\d+)[xX](\d+)", resolved)
    if not m:
        print(f"error: --size {value!r} is not WIDTHxHEIGHT, a shortcut, or 'auto'", file=sys.stderr)
        raise SystemExit(2)
    w, h = int(m[1]), int(m[2])
    problems: list[str] = []
    if w % 16 or h % 16:
        problems.append(f"both edges must be multiples of 16 (got {w}x{h})")
    if w * h < MIN_PIXELS:
        problems.append(f"total pixels must be >= {MIN_PIXELS:,} (got {w * h:,}; smallest square is 1024x1024)")
    if w * h > MAX_PIXELS or max(w, h) > MAX_EDGE:
        problems.append(f"max supported resolution is 3840x2160 / {MAX_PIXELS:,} px (got {w * h:,})")
    if min(w, h) * 3 < max(w, h):
        problems.append(f"aspect ratio must be within 1:3..3:1 (got {w}:{h})")
    if problems:
        print(f"error: --size {resolved} violates gpt-image-2 constraints: {'; '.join(problems)}", file=sys.stderr)
        raise SystemExit(2)
    warning = None
    if w * h > EXPERIMENTAL_PIXELS:
        warning = f"note: --size {resolved} is above 2560x1440 — OpenAI marks >2K output as experimental."
    return resolved, warning


def warn_size_mismatch(result: Any, requested: str) -> None:
    """Warn when the response reports a size different from the request.

    The official API echoes the honored size per image; gateways that route to
    backends without size semantics (e.g. the ChatGPT Codex backend) silently
    return their own resolution — surface that instead of shipping wrong
    dimensions unnoticed. Falls back to decoding the PNG IHDR when the SDK
    model does not expose a size field.
    """
    if requested == "auto":
        return
    returned = getattr(result, "size", None)
    if not returned:
        for item in (result.data or []):
            returned = getattr(item, "size", None)
            if returned:
                break
    if not returned:
        b64 = getattr((result.data or [None])[0], "b64_json", None) if result.data else None
        raw = base64.b64decode(b64) if b64 else b""
        if raw[:8] == b"\x89PNG\r\n\x1a\n" and len(raw) >= 24:
            w, h = struct.unpack(">II", raw[16:24])
            returned = f"{w}x{h}"
    if returned and returned != requested:
        print(
            f"warning: requested size {requested} but upstream returned {returned} — "
            "--size was ignored by the gateway/model; resize locally if exact "
            f"dimensions matter (sips -z H W <file>).",
            file=sys.stderr,
        )


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="gpt-image",
        description="Call OpenAI GPT Image 2 (generations or edits) via the official openai Python SDK.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("-p", "--prompt", required=True, help="Text prompt / edit instruction.")
    p.add_argument(
        "-f", "--file",
        help="Output path. Auto-generated as YYYY-MM-DD-HH-MM-SS-<slug>.<ext> if omitted "
             "(written to ./fig/ if that dir exists, else ./).",
    )
    p.add_argument(
        "-i", "--image", action="append", type=Path, default=None,
        help="Reference image path. Repeat flag for multi-reference edits. "
             "Presence of any -i switches endpoint to client.images.edit().",
    )
    p.add_argument(
        "-m", "--mask", type=Path, default=None,
        help="Alpha-channel PNG mask (opaque = preserved, transparent = regenerated). "
             "Edits endpoint only; requires -i.",
    )
    p.add_argument(
        "--model", default=os.environ.get("GPT_IMAGE_MODEL") or DEFAULT_MODEL,
        help=f"Model ID (default {DEFAULT_MODEL}, or $GPT_IMAGE_MODEL when set).",
    )
    p.add_argument(
        "--size", default=DEFAULT_SIZE,
        help="'auto', standard literals (1024x1024, 1536x1024, 1024x1536), or any WIDTHxHEIGHT "
             "with both edges divisible by 16, aspect within 1:3..3:1, and 655,360–8,294,400 "
             "total pixels (max 3840x2160; >2560x1440 is experimental). Shortcuts: 1k, 2k, 4k, "
             "portrait, landscape, square, wide, tall. Validated locally before calling the API. "
             "Default 1024x1024.",
    )
    p.add_argument(
        "--quality", default="high", choices=["auto", "low", "medium", "high"],
        help="Rendering fidelity / budget knob (cost scales ~10× per step). Default high. "
             "Use low for cheap drafts, medium for normal exploration, high for final text-heavy or shipping-facing assets.",
    )
    p.add_argument("-n", "--n", type=int, default=1, help="Number of images to return (1-10). Default 1.")
    p.add_argument(
        "--background", default=None, choices=["auto", "opaque", "transparent"],
        help="`transparent` yields an alpha channel (requires --format png or webp; "
             "preview on gpt-image-2). `opaque` disables transparency. Default API-side auto.",
    )
    p.add_argument(
        "--moderation", default=DEFAULT_MODERATION, choices=["auto", "low"],
        help="Generations only. Default low. Use `auto` if you want the stricter API-side default.",
    )
    p.add_argument(
        "--input-fidelity", dest="input_fidelity", default=None, choices=["low", "high"],
        help="Edits only. gpt-image-2 rejects this parameter, so the CLI drops it locally before calling the API.",
    )
    p.add_argument(
        "--format", dest="output_format", default=None,
        choices=["png", "jpeg", "webp"],
        help="Output encoding. Default png.",
    )
    p.add_argument(
        "--compression", dest="output_compression", type=int, default=None,
        help="0-100 compression level for jpeg/webp. Ignored for png.",
    )
    p.add_argument(
        "--user", default=None,
        help="Optional end-user identifier forwarded to OpenAI for abuse tracking.",
    )
    return p.parse_args()


def _filter_none(d: dict[str, Any]) -> dict[str, Any]:
    """Drop keys whose value is None — SDK treats missing vs None differently."""
    return {k: v for k, v in d.items() if v is not None}


def call_generate(client: OpenAI, args: argparse.Namespace) -> Any:
    return client.images.generate(**_filter_none({
        "model": args.model,
        "prompt": args.prompt,
        "size": args.size,
        "quality": args.quality,
        "n": args.n,
        "background": args.background,
        "moderation": args.moderation,
        "output_format": args.output_format,
        "output_compression": args.output_compression,
        "user": args.user,
    }))


def call_edit(client: OpenAI, args: argparse.Namespace) -> Any:
    for p in args.image:
        if not p.is_file():
            print(f"error: --image not found: {p}", file=sys.stderr)
            sys.exit(2)
    if args.mask and not args.mask.is_file():
        print(f"error: --mask not found: {args.mask}", file=sys.stderr)
        sys.exit(2)

    input_fidelity = args.input_fidelity
    if input_fidelity and model_rejects_input_fidelity(args.model):
        print(
            "note: dropping --input-fidelity because gpt-image-2 rejects that parameter.",
            file=sys.stderr,
        )
        input_fidelity = None

    image_handles = [p.open("rb") for p in args.image]
    mask_handle = args.mask.open("rb") if args.mask else None
    try:
        return client.images.edit(**_filter_none({
            "model": args.model,
            "image": image_handles,
            "mask": mask_handle,
            "prompt": args.prompt,
            "size": args.size,
            "quality": args.quality,
            "n": args.n,
            "background": args.background,
            "input_fidelity": input_fidelity,
            "output_format": args.output_format,
            "output_compression": args.output_compression,
            "user": args.user,
        }))
    finally:
        for h in image_handles:
            h.close()
        if mask_handle:
            mask_handle.close()


def write_outputs(data: list[Any], out_path: Path, n: int) -> list[Path]:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for i, item in enumerate(data):
        b64 = getattr(item, "b64_json", None)
        url = getattr(item, "url", None)
        if b64:
            raw = base64.b64decode(b64)
        elif url:
            with urllib.request.urlopen(url, timeout=300) as r:  # noqa: S310 — OpenAI-owned host
                raw = r.read()
        else:
            print(f"error: response item {i} has neither b64_json nor url", file=sys.stderr)
            sys.exit(1)

        if n == 1:
            target = out_path
        else:
            stem = out_path.with_suffix("")
            target = stem.parent / f"{stem.name}_{i}{out_path.suffix}"
        target.write_bytes(raw)
        written.append(target)
    return written


def main() -> int:
    _load_env_chain()
    args = parse_args()

    if not os.environ.get("OPENAI_API_KEY"):
        print(
            "error: OPENAI_API_KEY not set. Add it to env / ~/.config/gpt-image/env / .env / ~/.env, "
            "or use your host agent's native image tool.",
            file=sys.stderr,
        )
        return 2

    if args.mask and not args.image:
        print("error: --mask requires --image (edits endpoint only)", file=sys.stderr)
        return 2

    if not 1 <= args.n <= 10:
        print("error: -n/--n must be between 1 and 10", file=sys.stderr)
        return 2

    if args.background == "transparent" and (args.output_format or "png") == "jpeg":
        print("error: --background transparent requires --format png or webp", file=sys.stderr)
        return 2

    if args.output_compression is not None and (args.output_format or "png") == "png":
        print("note: dropping --compression — PNG output must not set output_compression.", file=sys.stderr)
        args.output_compression = None

    size, size_note = validate_size(args.size, args.model)
    if size_note:
        print(size_note, file=sys.stderr)
    args.size = size

    ext = args.output_format or "png"
    out_path = Path(args.file).expanduser().resolve() if args.file else default_output_path(args.prompt, ext)

    _ua = os.environ.get("GPT_IMAGE_USER_AGENT")
    # auto-reads OPENAI_API_KEY / OPENAI_BASE_URL
    client = OpenAI(default_headers={"User-Agent": _ua}) if _ua else OpenAI()

    try:
        result = call_edit(client, args) if args.image else call_generate(client, args)
        warn_size_mismatch(result, args.size)
    except APIError as e:
        print(f"error: {type(e).__name__}: {e}", file=sys.stderr)
        return 1

    data = result.data or []
    if not data:
        print(f"error: no image data in response: {result}", file=sys.stderr)
        return 1

    for p in write_outputs(data, out_path, args.n):
        print(p)
    return 0


if __name__ == "__main__":
    sys.exit(main())
