#!/usr/bin/env python3
"""train.py — Ultralytics training wrapper with an explicit plan gate.

Without --yes the script only PRINTS what it would do (including which
base model it would download). Training runs only when --yes is passed
AND ultralytics imports. Night/unattended runs must never surprise-
download models or occupy the GPU: run training deliberately, in the
day, with resources you own (ROADMAP §4, NIGHTOPS resource limits).
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path

DEFAULTS = {
    "model": "yolov8n.pt",   # nano first (轻量优先)
    "imgsz": 640,
    "epochs": 60,
    "batch": 8,
    "device": "cpu",         # opt into GPU explicitly; never by accident
}


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("data_yaml", nargs="?", help="dataset yaml (Ultralytics format); build datasets with prepare_dataset.py")
    p.add_argument("--model", default=DEFAULTS["model"], help=f"base model (default: {DEFAULTS['model']})")
    p.add_argument("--imgsz", type=int, default=DEFAULTS["imgsz"], help=f"training imgsz (default: {DEFAULTS['imgsz']})")
    p.add_argument("--epochs", type=int, default=DEFAULTS["epochs"], help=f"epochs (default: {DEFAULTS['epochs']})")
    p.add_argument("--batch", type=int, default=DEFAULTS["batch"], help=f"batch size (default: {DEFAULTS['batch']})")
    p.add_argument("--device", default=DEFAULTS["device"], help=f"torch device (default: {DEFAULTS['device']} — opt into GPU explicitly)")
    p.add_argument("--project", default="runs/vision", help="run output directory (git-ignored)")
    p.add_argument("--name", default=None, help="run name (default: timestamp)")
    p.add_argument("--yes", action="store_true", help="actually run (downloads the base model if missing)")
    p.add_argument("--selftest", action="store_true",
                   help="run the synthetic end-to-end check and exit")
    return p.parse_args()


def plan(args: argparse.Namespace) -> dict:
    return {
        "data": str(Path(args.data_yaml).resolve()),
        "model": args.model,
        "imgsz": args.imgsz,
        "epochs": args.epochs,
        "batch": args.batch,
        "device": args.device,
        "project": args.project,
        "will_download": args.model.startswith(("yolov8", "yolo11")),
        "runs_dir_git_ignored": True,
    }


def selftest() -> int:
    """The no-training surface through the REAL CLI: the dry plan's shape and
    determinism, the missing-data guard, and the --yes gate (refusal without
    ultralytics; honestly skipped when ultralytics IS present — a selftest
    must never kick off real training)."""
    import subprocess
    import tempfile

    ok = True

    def check(cond: bool, why: str) -> None:
        nonlocal ok
        if not cond:
            print(f"selftest FAIL: {why}")
            ok = False

    def run(*argv: str) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, str(Path(__file__).resolve()), *argv],
                              capture_output=True, text=True)

    with tempfile.TemporaryDirectory(prefix="train-selftest-") as td:
        data = Path(td) / "demo.yaml"
        data.write_text("train: images\nval: images\nnames:\n  0: rect_a\n", encoding="utf-8")

        proc = run(str(data))
        check(proc.returncode == 0 and "dry plan only" in proc.stdout,
              f"dry plan exit {proc.returncode}: {proc.stderr[-200:]}")
        # the plan is printed with indent=2, i.e. multiline — raw_decode the
        # first JSON value after the PLAN marker
        plan = {}
        marker = proc.stdout.find("train: PLAN ")
        if marker < 0:
            check(False, "plan marker missing from output")
        else:
            brace = proc.stdout.find("{", marker)
            try:
                plan, _ = json.JSONDecoder().raw_decode(proc.stdout[brace:])
            except Exception as e:
                check(False, f"plan JSON unparseable: {e}")
        check(plan.get("data", "").endswith("demo.yaml"), "plan must resolve the dataset path")
        check(plan.get("model") == DEFAULTS["model"], "default model wrong")
        check(plan.get("device") == DEFAULTS["device"], "default device must be cpu (never GPU by accident)")
        check(plan.get("will_download") is True, "yolov8* base models download — plan must say so")
        check(plan.get("runs_dir_git_ignored") is True, "runs dir must be git-ignored")
        proc2 = run(str(data))
        check(proc.stdout == proc2.stdout, "same args must produce an identical plan")

        proc = run(str(Path(td) / "missing.yaml"))
        check(proc.returncode == 2 and "data yaml not found" in proc.stderr
              and "Traceback" not in proc.stderr,
              f"missing-data guard: exit={proc.returncode} stderr={proc.stderr[-160:]}")

        has_ultra = importlib.util.find_spec("ultralytics") is not None
        if has_ultra:
            print("selftest note: ultralytics present — skipping the --yes refusal check "
                  "(a selftest never kicks off real training)")
        else:
            proc = run(str(data), "--yes")
            check(proc.returncode == 2 and "ultralytics is not installed" in proc.stderr
                  and "Traceback" not in proc.stderr,
                  f"--yes without ultralytics must refuse: exit={proc.returncode} stderr={proc.stderr[-160:]}")

    if ok:
        print("train selftest: PASS (plan shape+determinism, missing-data guard, "
              "--yes gate) — training itself stays a deliberate daytime action")
        return 0
    return 1


def main() -> int:
    args = parse_args()
    if getattr(args, "selftest", False):
        return selftest()
    if not args.data_yaml or not Path(args.data_yaml).is_file():
        print(f"train: ERROR data yaml not found: {args.data_yaml}", file=sys.stderr)
        return 2

    print("train: PLAN " + json.dumps(plan(args), indent=2))
    if not args.yes:
        print("train: dry plan only — pass --yes to execute (may download the base model)")
        return 0

    if importlib.util.find_spec("ultralytics") is None:
        print("train: ERROR ultralytics is not installed in this environment "
              "(pip install ultralytics); refusing to auto-install", file=sys.stderr)
        return 2

    from ultralytics import YOLO  # imported only after the gate

    model = YOLO(args.model)
    model.train(
        data=args.data_yaml,
        imgsz=args.imgsz,
        epochs=args.epochs,
        batch=args.batch,
        device=args.device,
        project=args.project,
        name=args.name,
    )
    print("train: done — export with export_onnx.py (weights stay out of Git)")
    return 0


if __name__ == "__main__":
    # selftest flag shared with the other tools' convention
    if "--selftest" in sys.argv:
        sys.exit(selftest())
    sys.exit(main())
