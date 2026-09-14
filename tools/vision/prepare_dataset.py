#!/usr/bin/env python3
"""prepare_dataset.py — validate a YOLO-style dataset and emit its manifest.

Stdlib only, no heavy dependencies. Layout:

    <dataset_root>/
      images/  *.png|*.jpg|*.jpeg|*.bmp
      labels/  *.txt   (one per image, YOLO: class cx cy w h, normalized)

Outputs (next to --out):
    <out>/<name>.manifest.json   dataset manifest (source, split, counts)
    <out>/<name>_train.txt       image list for training
    <out>/<name>_val.txt         image list for validation
"""

from __future__ import annotations

import argparse
import json
import random
import subprocess
import sys
from pathlib import Path

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".bmp"}


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("root", nargs="?", help="dataset root containing images/ and labels/")
    p.add_argument("--name", required=True, help="dataset name (used in output file names)")
    p.add_argument("--out", default="datasets/manifests", help="output directory (default: datasets/manifests)")
    p.add_argument("--val-ratio", type=float, default=0.2, help="validation fraction in (0, 1) (default: 0.2)")
    p.add_argument("--seed", type=int, default=7, help="shuffle seed (default: 7)")
    p.add_argument("--resolution", default="unknown", help="primary capture resolution, e.g. 1920x1080")
    p.add_argument("--aspect", default="16:9", help="primary aspect ratio (default: 16:9)")
    p.add_argument("--annotation-version", default="v1", help="annotation version tag (default: v1)")
    p.add_argument("--selftest", action="store_true",
                   help="run the synthetic end-to-end check and exit")
    return p.parse_args()


def fail(msg: str) -> "NoReturn":  # type: ignore[name-defined]
    print(f"prepare_dataset: ERROR: {msg}", file=sys.stderr)
    sys.exit(2)


def main() -> int:
    args = parse_args()
    root = Path(args.root) if args.root else None
    if root is None:
        fail("provide a dataset root containing images/ and labels/ (or --selftest)")
    images_dir, labels_dir = root / "images", root / "labels"
    if not images_dir.is_dir() or not labels_dir.is_dir():
        fail(f"{root} must contain images/ and labels/")

    images = sorted(p for p in images_dir.iterdir() if p.suffix.lower() in IMAGE_EXTS)
    if not images:
        fail(f"no images found under {images_dir}")

    # Labels are matched by image stem: two images sharing a stem would both
    # bind to the same label file and silently skew the train/val counts.
    seen_stems: dict[str, Path] = {}
    for img in images:
        key = img.stem.lower()
        if key in seen_stems:
            fail(f"duplicate image stem {img.stem!r} ({seen_stems[key].name} vs "
                 f"{img.name}) — labels are matched by stem, this would poison the split")
        seen_stems[key] = img

    missing_labels, bad_labels = [], []
    for img in images:
        label = labels_dir / (img.stem + ".txt")
        if not label.is_file():
            missing_labels.append(img.name)
            continue
        for lineno, line in enumerate(label.read_text(encoding="utf-8").splitlines(), 1):
            parts = line.split()
            if not parts:
                continue
            if len(parts) != 5:
                bad_labels.append(f"{label.name}:{lineno} (want 'class cx cy w h')")
                continue
            try:
                cx, cy, w, h = (float(v) for v in parts[1:])
            except ValueError:
                bad_labels.append(f"{label.name}:{lineno} (cx cy w h must be numeric)")
                continue
            try:
                cls = int(parts[0])
            except ValueError:
                bad_labels.append(f"{label.name}:{lineno} (class must be an integer index)")
                continue
            if cls < 0:
                bad_labels.append(f"{label.name}:{lineno} (class must be a non-negative index)")
                continue
            if not (0.0 <= cx <= 1.0 and 0.0 <= cy <= 1.0 and 0.0 < w <= 1.0 and 0.0 < h <= 1.0):
                bad_labels.append(f"{label.name}:{lineno} (boxes must be normalized 0..1)")

    if bad_labels:
        fail("invalid label lines: " + "; ".join(bad_labels[:5]) + (" ..." if len(bad_labels) > 5 else ""))

    usable = [img for img in images if (labels_dir / (img.stem + ".txt")).is_file()]
    if missing_labels:
        print(f"prepare_dataset: WARNING {len(missing_labels)} images without labels are excluded "
              f"(first: {missing_labels[0]})")
    if len(usable) < 2:
        fail("need at least 2 labeled images to split train/val")

    if not (0.0 < args.val_ratio < 1.0):
        fail("--val-ratio must be in (0, 1)")

    rng = random.Random(args.seed)
    shuffled = usable[:]
    rng.shuffle(shuffled)
    val_count = max(1, round(len(shuffled) * args.val_ratio))
    val, train = shuffled[:val_count], shuffled[val_count:]

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    train_list = out_dir / f"{args.name}_train.txt"
    val_list = out_dir / f"{args.name}_val.txt"
    train_list.write_text("\n".join(str(p.resolve()) for p in train) + "\n", encoding="utf-8")
    val_list.write_text("\n".join(str(p.resolve()) for p in val) + "\n", encoding="utf-8")

    classes = sorted({int(line.split()[0]) for img in usable
                      for line in (labels_dir / (img.stem + ".txt")).read_text(encoding="utf-8").splitlines()
                      if line.strip()})
    manifest = {
        "schema_version": 1,
        "name": args.name,
        "root": str(root.resolve()),
        "resolution": args.resolution,
        "aspect": args.aspect,
        "annotation_version": args.annotation_version,
        "images_total": len(images),
        "images_usable": len(usable),
        "images_train": len(train),
        "images_val": len(val),
        "classes": classes,
        "notes": "Images/labels stay external to Git; this manifest is the tracked reference.",
    }
    manifest_path = out_dir / f"{args.name}.manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    print(f"prepare_dataset: OK {len(train)} train / {len(val)} val images, classes={classes}")
    print(f"prepare_dataset: manifest {manifest_path}")
    return 0


def selftest() -> int:
    """A synthetic labeled dataset through the REAL CLI (subprocess, operator
    path): happy-path manifest/split/determinism, then every refusal guard —
    including the three that used to crash or slip through (non-numeric
    coordinates, negative class index, duplicate image stems)."""
    import tempfile

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import pnglite

    ok = True

    def check(cond: bool, why: str) -> None:
        nonlocal ok
        if not cond:
            print(f"selftest FAIL: {why}")
            ok = False

    def run(*argv: str) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, str(Path(__file__).resolve()), *argv],
                              capture_output=True, text=True)

    def build(root: Path, labels: dict[str, str | None], stems: dict[str, str] | None = None) -> None:
        (root / "images").mkdir(parents=True)
        (root / "labels").mkdir(parents=True)
        names = dict(stems or {})
        w = h = 8
        pix = bytes((60, 60, 60, 255)) * (w * h)
        for i in range(len(labels)):
            name = names.get(f"img{i}", f"img{i}")
            pnglite.write_png(root / "images" / f"{name}.png", w, h, pix)
        for i, txt in enumerate(labels.values()):
            if txt is not None:
                name = names.get(f"img{i}", f"img{i}")
                (root / "labels" / f"{name}.txt").write_text(txt, encoding="utf-8")

    with tempfile.TemporaryDirectory(prefix="prep-ds-selftest-") as td:
        root_dir = Path(td)

        # ---- happy path: 3 labeled + 1 unlabeled, deterministic split ----
        ds = root_dir / "ds"
        build(ds, {
            "img0": "0 0.5 0.5 0.2 0.2\n\n",   # blank line tolerated
            "img1": "1 0.25 0.25 0.1 0.1\n",
            "img2": "0 0.75 0.75 0.5 0.5\n",
            "img3": None,                       # unlabeled → excluded with warning
        })
        out = root_dir / "manifests"
        proc = run(str(ds), "--name", "self", "--out", str(out))
        check(proc.returncode == 0, f"happy path exit {proc.returncode}: {proc.stderr[-300:]}")
        if proc.returncode == 0:
            man = json.loads((out / "self.manifest.json").read_text(encoding="utf-8"))
            check(man["images_total"] == 4, f"images_total {man['images_total']} != 4")
            check(man["images_usable"] == 3, f"images_usable {man['images_usable']} != 3")
            check(man["images_val"] == 1, f"images_val {man['images_val']} != 1")
            check(man["images_train"] == 2, f"images_train {man['images_train']} != 2")
            check(man["classes"] == [0, 1], f"classes {man['classes']} != [0, 1]")
            train = (out / "self_train.txt").read_text(encoding="utf-8").split()
            val = (out / "self_val.txt").read_text(encoding="utf-8").split()
            check(len(train) == 2 and len(val) == 1, "split list lengths wrong")
            check(not (set(train) & set(val)), "train/val lists overlap")
            check(all(Path(p).is_file() for p in train + val), "split lists name missing files")
            # determinism: same seed → byte-identical lists
            proc2 = run(str(ds), "--name", "self2", "--out", str(out))
            check(proc2.returncode == 0, "second run failed")
            check((out / "self_train.txt").read_bytes() == (out / "self2_train.txt").read_bytes()
                  and (out / "self_val.txt").read_bytes() == (out / "self2_val.txt").read_bytes(),
                  "same seed must produce identical lists")
            check("WARNING" in proc.stdout, "unlabeled image must warn")

        # ---- guards: exit 2, clean message, never a traceback ----
        def expect_fail(why: str, *argv: str, needle: str = "ERROR") -> None:
            proc = run(*argv)
            check(proc.returncode == 2,
                  f"{why}: expected exit 2, got {proc.returncode} (stderr: {proc.stderr[-200:]})")
            check(needle in proc.stderr,
                  f"{why}: message missing ({proc.stderr[-200:]})")
            check("Traceback" not in proc.stderr, f"{why}: raw traceback leaked")

        bad = root_dir / "bad"
        nolabels = root_dir / "nolabels"
        nolabels.mkdir()
        (nolabels / "images").mkdir()

        expect_fail("no-images", str(nolabels), "--name", "x", "--out", str(out))
        expect_fail("missing-layout", str(root_dir / "missing"), "--name", "x", "--out", str(out))
        expect_fail("no-root-given", "--name", "x", "--out", str(out))

        cases = [
            ("field-count", {"img0": "0 0.5 0.5\n", "img1": "1 0.25 0.25 0.1 0.1\n"},
             "want 'class cx cy w h'"),
            ("non-numeric-coords", {"img0": "0 0.5 abc 0.2 0.2\n", "img1": "1 0.25 0.25 0.1 0.1\n"},
             "must be numeric"),
            ("non-integer-class", {"img0": "x 0.5 0.5 0.2 0.2\n", "img1": "1 0.25 0.25 0.1 0.1\n"},
             "integer index"),
            ("negative-class", {"img0": "-1 0.5 0.5 0.2 0.2\n", "img1": "1 0.25 0.25 0.1 0.1\n"},
             "non-negative index"),
            ("out-of-range-box", {"img0": "0 0.5 0.5 1.5 0.2\n", "img1": "1 0.25 0.25 0.1 0.1\n"},
             "normalized 0..1"),
        ]
        for why, labels, needle in cases:
            if bad.exists():
                import shutil as _sh
                _sh.rmtree(bad)
            build(bad, labels)
            expect_fail(why, str(bad), "--name", "x", "--out", str(out), needle=needle)

        # duplicate stems: a.png + a.jpg both present, both labeled
        dup = root_dir / "dup"
        (dup / "images").mkdir(parents=True)
        (dup / "labels").mkdir()
        pix = bytes((60, 60, 60, 255)) * 64
        pnglite.write_png(dup / "images" / "a.png", 8, 8, pix)
        (dup / "images" / "a.jpg").write_bytes(b"jpg bytes")
        (dup / "labels" / "a.txt").write_text("0 0.5 0.5 0.2 0.2\n", encoding="utf-8")
        expect_fail("duplicate-stems", str(dup), "--name", "x", "--out", str(out),
                    needle="duplicate image stem")

        # single usable image cannot be split
        solo = root_dir / "solo"
        build(solo, {"img0": "0 0.5 0.5 0.2 0.2\n"})
        expect_fail("single-usable", str(solo), "--name", "x", "--out", str(out),
                    needle="at least 2 labeled images")

        # val-ratio bounds
        for ratio in ("0", "1.5"):
            expect_fail(f"val-ratio-{ratio}", str(ds), "--name", "x", "--out", str(out),
                        "--val-ratio", ratio, needle="val-ratio")

    if ok:
        print("prepare_dataset selftest: PASS (manifest counts, deterministic split, "
              "9 refusal guards incl. non-numeric/negative-class/duplicate-stem)")
        return 0
    return 1


if __name__ == "__main__":
    # selftest flag shared with the other tools' convention
    if "--selftest" in sys.argv:
        sys.exit(selftest())
    sys.exit(main())
