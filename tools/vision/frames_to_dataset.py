#!/usr/bin/env python3
"""frames_to_dataset.py — turn a controller --record frame directory into
a dataset layout ready for annotation and prepare_dataset.py.

The controller records numbered PNGs (frame_00000.png...). This script
copies them into `<out>/images/`, creates one empty YOLO label file per
image under `<out>/labels/` (fill them in with your annotation tool:
`class cx cy w h`, normalized 0..1), and writes a source manifest.

Bridge: controller --record → THIS → annotate → prepare_dataset.py →
train.py → export_onnx.py → controller --model-path (the full NC1/§4
loop, training data flowing one way, weights never entering Git).
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pnglite

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".bmp"}


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("frames", nargs="?", help="directory of frame_*.png from `controller --record`")
    p.add_argument("--out", required=True, help="dataset root to create (images/ + labels/)")
    p.add_argument("--name", default="recorded", help="dataset name recorded in the manifest")
    p.add_argument("--source-session", default="", help="session TSV path for traceability")
    p.add_argument("--resolution", default="unknown", help="capture resolution, e.g. 1920x1080")
    p.add_argument("--aspect", default="16:9", help="aspect tag (default: 16:9)")
    p.add_argument("--move", action="store_true", help="move frames instead of copying")
    p.add_argument("--selftest", action="store_true",
                   help="run the synthetic end-to-end check and exit")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    frames_dir = Path(args.frames) if args.frames else None
    if frames_dir is None or not frames_dir.is_dir():
        print(f"frames_to_dataset: ERROR not a directory: {frames_dir}", file=sys.stderr)
        return 2
    images = sorted(
        p for p in frames_dir.iterdir()
        if p.is_file() and p.suffix.lower() in IMAGE_EXTS
    )
    if not images:
        print("frames_to_dataset: ERROR no image frames found", file=sys.stderr)
        return 2

    # Labels are matched by image stem one-to-one; two images sharing a stem
    # (frame_0.png + frame_0.jpg) would silently collapse into one label file
    # and double-count an under-labeled dataset. Refuse instead.
    seen_stems: dict[str, Path] = {}
    for img in images:
        key = img.stem.lower()
        if key in seen_stems:
            print(f"frames_to_dataset: ERROR duplicate image stem {img.stem!r} "
                  f"({seen_stems[key].name} vs {img.name}) — labels are matched by "
                  f"stem, this would collide", file=sys.stderr)
            return 2
        seen_stems[key] = img

    out = Path(args.out)
    if out.exists():
        print(f"frames_to_dataset: ERROR output exists: {out} (refusing to merge)", file=sys.stderr)
        return 2
    (out / "images").mkdir(parents=True)
    (out / "labels").mkdir(parents=True)

    for src in images:
        dst = out / "images" / src.name
        if args.move:
            shutil.move(str(src), str(dst))
        else:
            shutil.copy2(str(src), str(dst))
        (out / "labels" / (dst.stem + ".txt")).write_text("", encoding="utf-8")

    manifest = {
        "schema_version": 1,
        "name": args.name,
        "root": str(out.resolve()),
        "resolution": args.resolution,
        "aspect": args.aspect,
        "annotation_version": "unannotated",
        "source": {
            "kind": "controller-record",
            "frames": len(images),
            "session_tsv": args.source_session,
        },
        "notes": "Labels are empty placeholders; annotate before prepare_dataset.py.",
    }
    manifest_path = out / f"{args.name}.source-manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    print(f"frames_to_dataset: {len(images)} frames -> {out}")
    print(f"frames_to_dataset: manifest {manifest_path}")
    print("next: annotate labels/ (YOLO txt), then validate with prepare_dataset.py")
    return 0


def selftest() -> int:
    """Synthetic frames through the REAL CLI (subprocess, operator path):
    happy path layout + manifest, every refusal guard, and --move."""
    ok = True

    def check(cond: bool, why: str) -> None:
        nonlocal ok
        if not cond:
            print(f"selftest FAIL: {why}")
            ok = False

    def run(*argv: str) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, str(Path(__file__).resolve()), *argv],
                              capture_output=True, text=True)

    with tempfile.TemporaryDirectory(prefix="f2d-selftest-") as td:
        root = Path(td)
        frames = root / "frames"
        frames.mkdir()
        w, h = 8, 6
        for i in range(3):
            pix = bytes((i * 40 % 256, 40, 40, 255)) * (w * h)
            pnglite.write_png(frames / f"frame_{i:05d}.png", w, h, pix)

        out = root / "ds"
        proc = run(str(frames), "--out", str(out), "--name", "self", "--resolution", "8x6")
        check(proc.returncode == 0, f"happy path exit {proc.returncode}: {proc.stderr[-300:]}")
        imgs = sorted((out / "images").iterdir()) if (out / "images").is_dir() else []
        lbls = sorted((out / "labels").iterdir()) if (out / "labels").is_dir() else []
        check(len(imgs) == 3, f"expected 3 copied images, got {len(imgs)}")
        check(len(lbls) == 3, f"expected 3 empty label files, got {len(lbls)}")
        check(all(p.read_text(encoding="utf-8") == "" for p in lbls),
              "label placeholders must be empty")
        man = out / "self.source-manifest.json"
        check(man.is_file(), "source manifest missing")
        if man.is_file():
            m = json.loads(man.read_text(encoding="utf-8"))
            check(m.get("annotation_version") == "unannotated", "manifest must say unannotated")
            check(m.get("source", {}).get("frames") == 3, "manifest frame count wrong")
            check(m.get("resolution") == "8x6", "manifest resolution wrong")

        # guards — each must exit 2 with a clean message, never a traceback
        proc = run(str(frames), "--out", str(out))
        check(proc.returncode == 2 and "output exists" in proc.stderr,
              f"existing-output refusal: exit={proc.returncode} err={proc.stderr[-200:]}")
        empty = root / "empty"
        empty.mkdir()
        proc = run(str(empty), "--out", str(root / "ds2"))
        check(proc.returncode == 2 and "no image frames" in proc.stderr,
              f"no-images refusal: exit={proc.returncode} err={proc.stderr[-200:]}")
        proc = run(str(root / "missing"), "--out", str(root / "ds3"))
        check(proc.returncode == 2 and "not a directory" in proc.stderr,
              f"not-a-directory refusal: exit={proc.returncode} err={proc.stderr[-200:]}")
        dup = root / "dup"
        dup.mkdir()
        pnglite.write_png(dup / "frame_0.png", w, h, pix)
        (dup / "frame_0.jpg").write_bytes(b"not really a jpg, but the stem collides")
        proc = run(str(dup), "--out", str(root / "ds4"))
        check(proc.returncode == 2 and "duplicate image stem" in proc.stderr,
              f"duplicate-stem refusal: exit={proc.returncode} err={proc.stderr[-200:]}")

        # --move relocates instead of copying
        src2 = root / "frames2"
        src2.mkdir()
        pnglite.write_png(src2 / "frame_00000.png", w, h, pix)
        out2 = root / "ds5"
        proc = run(str(src2), "--out", str(out2), "--move")
        check(proc.returncode == 0, f"--move exit {proc.returncode}: {proc.stderr[-300:]}")
        check(not list(src2.iterdir()), "--move must empty the source dir")
        check((out2 / "images" / "frame_00000.png").is_file(), "--move image missing")

    if ok:
        print("frames_to_dataset selftest: PASS (layout+manifest, move, and "
              "output-exists/no-images/not-a-directory/duplicate-stem refusals)")
        return 0
    return 1


if __name__ == "__main__":
    # selftest flag shared with the other tools' convention
    if "--selftest" in sys.argv:
        sys.exit(selftest())
    sys.exit(main())
