#!/usr/bin/env python3
"""learn_route.py — NC9 learning-pipeline skeleton: frames → scene
segments → route-learning draft (ROADMAP §3 NC9).

Input is a directory of numbered PNG frames — what `controller --record`
produces, or any extraction the user brings (ffmpeg one-liners live in
tools/vision/README.md). Everything here is stdlib-only (no PIL/numpy/
ffmpeg at run time), downsamples hard before thinking, and emits a
schema'd draft whose anchor coordinates are NORMALIZED 0..1 — the
resolution-independence rule (NIGHTOPS hard constraint) applies to
learned data too, not just runtime transforms.

What this milestone deliberately does NOT do: OCR, YOLO, semantic
labels, real-game anything. It produces the structural draft that later
NC9 milestones annotate into SkillDefinitions (NC3 schema).

Resource shape: frames are decoded ONE at a time and only the grids the
draft actually needs stay in memory (one per scene-cut boundary plus the
running latest) — memory scales with scene count, not capture length.
Processing state is a plain JSON dict, so a long capture can be paused
(`stop_after` / `--checkpoint-interval`) and resumed (`--resume`) with a
byte-identical final draft (NC9 acceptance: 批处理可中断、可恢复).
A 10-minute 1fps capture (~600 frames) stays in the seconds-to-
tens-of-seconds range on CPU.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import tempfile
from pathlib import Path

import pnglite

DEFAULT_MAX_SIDE = 64       # downsampled grid cap (largest dimension)
DEFAULT_THRESHOLD = 6.0     # global mean-abs gray-diff (0..255) for a cut
DEFAULT_BLOCK_THRESHOLD = 24.0  # peak block diff — catches localized UI pops
DEFAULT_BLOCK = 8           # anchor localizer grid (blocks per axis)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("frames", nargs="?", help="directory of numbered PNG frames")
    p.add_argument("--out", help="draft JSON path (default: <frames>/route-draft.json)")
    p.add_argument("--max-side", type=int, default=DEFAULT_MAX_SIDE,
                   help=f"downsample cap, largest side (default {DEFAULT_MAX_SIDE})")
    p.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD,
                   help=f"scene-cut global mean-abs-diff threshold (default {DEFAULT_THRESHOLD})")
    p.add_argument("--block-threshold", type=float, default=DEFAULT_BLOCK_THRESHOLD,
                   help=f"scene-cut peak-block-diff threshold — catches localized UI "
                        f"changes like dialogs (default {DEFAULT_BLOCK_THRESHOLD})")
    p.add_argument("--inspect", action="store_true",
                   help="print the human-readable segment timeline")
    p.add_argument("--checkpoint-interval", type=int, default=100,
                   help="persist a resumable checkpoint every N frames, 0 disables "
                        "(default 100)")
    p.add_argument("--resume", action="store_true",
                   help="resume from the checkpoint next to the draft output")
    p.add_argument("--selftest", action="store_true",
                   help="run the synthetic end-to-end check and exit")
    return p.parse_args()


def downsample_gray(width: int, height: int, channels: int, pix: bytes, max_side: int):
    """Average-pool the image into a grid whose largest side is <= max_side;
    returns (gw, gh, luminance bytes)."""
    sx = max(1, width // max_side)
    sy = max(1, height // max_side)
    gw = max(1, width // sx)
    gh = max(1, height // sy)
    gray = bytearray(gw * gh)
    for gy in range(gh):
        for gx in range(gw):
            acc = n = 0
            for py in range(gy * sy, min((gy + 1) * sy, height), max(1, sy // 4)):
                row = py * width
                for px in range(gx * sx, min((gx + 1) * sx, width), max(1, sx // 4)):
                    o = (row + px) * channels
                    # luminance approximation, integer weights
                    acc += (pix[o] * 299 + pix[o + 1] * 587 + pix[o + 2] * 114) // 1000
                    n += 1
            gray[gy * gw + gx] = acc // max(1, n)
    return gw, gh, bytes(gray)


def block_diffs(gw: int, gh: int, a: bytes, b: bytes, blocks: int):
    """Per-block mean abs diff between two grids; returns
    [(score, bx, by, bx1, by1)] in grid cells."""
    bw = max(1, gw // blocks)
    bh = max(1, gh // blocks)
    out = []
    for by in range(0, gh, bh):
        for bx in range(0, gw, bw):
            acc = n = 0
            for y in range(by, min(by + bh, gh)):
                for x in range(bx, min(bx + bw, gw)):
                    i = y * gw + x
                    acc += abs(a[i] - b[i])
                    n += 1
            out.append((acc / max(1, n), bx, by, min(bx + bw, gw), min(by + bh, gh)))
    return out


def anchor_between(gw: int, gh: int, a: bytes, b: bytes, blocks: int) -> dict | None:
    """Localize WHERE two segment representatives differ: bounding box of
    the hot blocks, in NORMALIZED coordinates (0..1)."""
    scores = block_diffs(gw, gh, a, b, blocks)
    if not scores:
        return None
    peak = max(s[0] for s in scores)
    if peak <= 0:
        return None
    hot = [s for s in scores if s[0] >= peak * 0.5]
    x0 = min(s[1] for s in hot) / gw
    y0 = min(s[2] for s in hot) / gh
    x1 = max(s[3] for s in hot) / gw
    y1 = max(s[4] for s in hot) / gh
    return {
        "normalized": True,
        "x": round(x0, 4), "y": round(y0, 4),
        "w": round(x1 - x0, 4), "h": round(y1 - y0, 4),
        "peak_block_score": round(peak, 2),
    }


def dominant_color(frame_path: Path, anchor: dict) -> list[int] | None:
    """Average RGB inside the anchor region of one frame (normalized coords
    → pixels). Gives the probe layer a concrete color target for this step."""
    try:
        w, h, ch, pix = pnglite.read_png(frame_path)
    except Exception:
        return None
    if ch < 3:
        return None
    x0 = max(0, int(anchor["x"] * w))
    y0 = max(0, int(anchor["y"] * h))
    x1 = min(w, max(x0 + 1, int((anchor["x"] + anchor["w"]) * w)))
    y1 = min(h, max(y0 + 1, int((anchor["y"] + anchor["h"]) * h)))
    step = max(1, (x1 - x0) * (y1 - y0) // 4096)
    rs = gs = bs = n = 0
    for y in range(y0, y1):
        for x in range(x0, x1):
            if step > 1 and (x * 31 + y * 17) % step != 0:
                continue
            o = (y * w + x) * ch
            rs += pix[o]
            gs += pix[o + 1]
            bs += pix[o + 2]
            n += 1
    if n == 0:
        return None
    return [rs // n, gs // n, bs // n]


def _grid_to_json(g) -> list | None:
    return None if g is None else [g[0], g[1], base64.b64encode(g[2]).decode("ascii")]


def _grid_from_json(j):
    return None if j is None else (j[0], j[1], base64.b64decode(j[2]))


def checkpoint_dumps(st: dict) -> str:
    """Serialize a learn() processing state to JSON (grids b64-encoded)."""
    out = dict(st)
    out["retained"] = {k: _grid_to_json(g) for k, g in st["retained"].items()}
    out["last_grid"] = _grid_to_json(st["last_grid"])
    return json.dumps(out, sort_keys=True)


def checkpoint_loads(s: str) -> dict:
    """The inverse of checkpoint_dumps — grids decode back to bytes."""
    st = json.loads(s)
    st["retained"] = {k: _grid_from_json(g) for k, g in st["retained"].items()}
    st["last_grid"] = _grid_from_json(st["last_grid"])
    return st


def learn(frames_dir: Path, max_side: int = DEFAULT_MAX_SIDE,
          threshold: float = DEFAULT_THRESHOLD,
          block_threshold: float = DEFAULT_BLOCK_THRESHOLD, *,
          state: dict | None = None,
          stop_after: int | None = None,
          progress_every: int | None = None,
          on_progress=None) -> tuple[dict | None, dict]:
    """Learn a route draft from a frame directory — STREAMED.

    Frames are decoded one at a time and only the grids the draft needs
    stay in memory (one per scene-cut boundary plus the running latest),
    so memory scales with scene count, not capture length. The whole
    processing state is a plain dict: pass a previously returned state
    back via `state=` to resume an interrupted run, or set `stop_after=N`
    to process at most N more frames and pause — the call then returns
    (None, state); persist it (checkpoint_dumps) and hand it to a later
    call. That is the NC9 acceptance contract 批处理可中断、可恢复.
    `progress_every` + `on_progress` expose the live state every N frames
    for callers that persist periodic checkpoints.

    Returns (draft, final_state); draft is None when the run paused early.
    """
    frames = sorted(p for p in frames_dir.iterdir()
                    if p.is_file() and p.suffix.lower() == ".png")
    if len(frames) < 2:
        raise SystemExit(f"learn_route: need >= 2 PNG frames in {frames_dir}, found {len(frames)}")

    params = {"max_side": max_side, "threshold": threshold,
              "block_threshold": block_threshold}
    if state is None:
        st = {
            "version": 1,
            "params": params,
            "frame_count": len(frames),
            "first_frame": frames[0].name,
            "src_size": None,
            "grid_size": None,
            "next_index": 0,
            "pair_stats": [],   # [mean, peak] per processed adjacent pair
            "cuts": [],         # frame index where a new segment starts
            "retained": {},     # segment-boundary frame index -> its grid
            "last_grid": None,
        }
    else:
        st = state
        if st.get("version") != 1:
            raise SystemExit("learn_route: checkpoint version mismatch")
        if st.get("params") != params:
            raise SystemExit("learn_route: checkpoint params differ from this run's "
                             "--max-side/--threshold/--block-threshold")
        if st.get("frame_count") != len(frames) or st.get("first_frame") != frames[0].name:
            raise SystemExit("learn_route: frames directory changed since the checkpoint")

    grid_size = st.get("grid_size")
    target = len(frames)
    if stop_after is not None:
        target = min(target, st["next_index"] + max(0, stop_after))

    since_progress = 0
    while st["next_index"] < target:
        k = st["next_index"]
        w, h, ch, pix = pnglite.read_png(frames[k])
        g = downsample_gray(w, h, ch, pix, st["params"]["max_side"])
        if grid_size is None:
            grid_size = [g[0], g[1]]
            st["grid_size"] = grid_size
        elif [g[0], g[1]] != grid_size:
            raise SystemExit("learn_route: frames differ in size after downsampling")
        gw, gh = grid_size
        last = st["last_grid"]
        if last is not None:
            blocks = block_diffs(gw, gh, last[-1], g[-1], DEFAULT_BLOCK)
            mean = sum(s[0] for s in blocks) / max(1, len(blocks))
            peak = max(s[0] for s in blocks)
            # Round BEFORE the cut test — the original implementation compared
            # the stored rounded values, and comparing raw ones would flip
            # cuts for boundary values (e.g. 6.0004 vs threshold 6.0).
            mean = round(mean, 2)
            peak = round(peak, 2)
            st["pair_stats"].append([mean, peak])
            if mean > st["params"]["threshold"] or peak > st["params"]["block_threshold"]:
                st["cuts"].append(k)                     # frame k starts a new segment
                st["retained"][str(k - 1)] = last        # that segment's representative grid
        st["last_grid"] = [g[0], g[1], g[2]]
        st["src_size"] = [w, h]
        st["next_index"] = k + 1
        since_progress += 1
        if progress_every and on_progress and since_progress >= progress_every:
            on_progress(st)
            since_progress = 0

    if st["next_index"] != len(frames):
        if on_progress:
            on_progress(st)  # persist progress at the pause point too
        return None, st

    # ---- complete: build the draft from the retained state ----
    retained = {int(i): g for i, g in st["retained"].items()}
    retained[len(frames) - 1] = st["last_grid"]
    cuts = st["cuts"]
    pair_stats = st["pair_stats"]
    bounds = [0] + cuts + [len(frames)]

    segments = []
    for si in range(len(bounds) - 1):
        start, end = bounds[si], bounds[si + 1]
        if start >= end:
            continue
        rep = retained[end - 1]
        # Anchor = WHAT CHANGED when entering this segment (previous
        # segment's representative vs this one) — that difference is the
        # route step. Segment 0 has no entry, so no anchor.
        if start > 0:
            prev_rep = retained[start - 1]
            anchor = anchor_between(gw, gh, prev_rep[-1], rep[-1], DEFAULT_BLOCK) or {}
            if anchor:
                # The probe layer needs a concrete color to watch for: sample
                # the CURRENT segment's look at that region (re-reads only
                # this segment's representative PNG).
                color = dominant_color(frames[end - 1], anchor)
                if color:
                    anchor["dominant_rgb"] = color
        else:
            anchor = {}
        mean_at_entry, peak_at_entry = pair_stats[start - 1] if start > 0 else (0.0, 0.0)
        segments.append({
            "index": len(segments),
            "start_frame": start,
            "end_frame": end - 1,
            "frames": end - start,
            "entry_mean_diff": mean_at_entry,
            "entry_peak_diff": peak_at_entry,
            "anchor": anchor,
        })

    draft = {
        "schema_version": 1,
        "kind": "route-learning-draft",
        "source": {
            "frames_dir": str(frames_dir.resolve()),
            "frame_count": len(frames),
            "frame_size": list(st["src_size"] or (0, 0)),
        },
        "params": {"max_side": max_side, "threshold": threshold,
                   "block_threshold": block_threshold, "block": DEFAULT_BLOCK},
        "grid": {"w": gw, "h": gh},
        "segment_count": len(segments),
        "segments": segments,
    }
    return draft, st


def selftest() -> int:
    """Synthetic two-scene sequence must split at the cut, anchor near the
    painted region's normalized center; an identical-frame sequence must
    not split at all."""
    def frame(w: int, h: int, paint: bool) -> bytes:
        buf = bytearray(w * h * 4)
        for y in range(h):
            for x in range(w):
                o = (y * w + x) * 4
                buf[o:o + 4] = (24, 24, 28, 255)
        if paint:
            x0, y0, x1, y1 = int(w * 0.6), int(h * 0.6), int(w * 0.8), int(h * 0.8)
            for y in range(y0, y1):
                for x in range(x0, x1):
                    buf[(y * w + x) * 4:(y * w + x) * 4 + 4] = (200, 40, 16, 255)
        return bytes(buf)

    with tempfile.TemporaryDirectory(prefix="learn-route-selftest-") as td:
        d = Path(td)
        w, h = 160, 120
        seq = [frame(w, h, False)] * 8 + [frame(w, h, True)] * 8
        for i, pix in enumerate(seq):
            pnglite.write_png(d / f"frame_{i:05d}.png", w, h, pix)
        draft = learn(d, max_side=DEFAULT_MAX_SIDE, threshold=DEFAULT_THRESHOLD,
                      block_threshold=DEFAULT_BLOCK_THRESHOLD)[0]

        ok = True
        if draft["segment_count"] != 2:
            print(f"selftest FAIL: expected 2 segments, got {draft['segment_count']}")
            ok = False
        cut = draft["segments"][1] if draft["segment_count"] == 2 else {}
        if cut.get("start_frame") != 8:
            print(f"selftest FAIL: cut expected at frame 8, got {cut.get('start_frame')}")
            ok = False
        anchor = cut.get("anchor") or {}
        cx = anchor.get("x", 0) + anchor.get("w", 0) / 2
        cy = anchor.get("y", 0) + anchor.get("h", 0) / 2
        if abs(cx - 0.7) > 0.15 or abs(cy - 0.7) > 0.15:
            print(f"selftest FAIL: anchor center ({cx:.2f},{cy:.2f}) not near (0.70,0.70)")
            ok = False

        # checkpoint/resume equivalence (NC9 acceptance: 批处理可中断、可恢复):
        # pause after 9 frames (past the cut at 8, so a boundary grid is
        # retained), round-trip the state through JSON like a real checkpoint
        # file, resume — the final draft must be byte-identical to the
        # one-shot draft above.
        part, half = learn(d, max_side=DEFAULT_MAX_SIDE, threshold=DEFAULT_THRESHOLD,
                           block_threshold=DEFAULT_BLOCK_THRESHOLD, stop_after=9)
        if part is not None or half["next_index"] != 9:
            print(f"selftest FAIL: stop_after=9 must pause at 9 "
                  f"(finished={part is not None}, at={half['next_index']})")
            ok = False
        resumed, _ = learn(d, max_side=DEFAULT_MAX_SIDE, threshold=DEFAULT_THRESHOLD,
                           block_threshold=DEFAULT_BLOCK_THRESHOLD,
                           state=checkpoint_loads(checkpoint_dumps(half)))
        if resumed is None:
            print("selftest FAIL: resumed run did not complete")
            ok = False
        elif json.dumps(resumed, sort_keys=True) != json.dumps(draft, sort_keys=True):
            print("selftest FAIL: resumed draft differs from the one-shot draft")
            ok = False

        # checkpoint guards: a params mismatch or a changed frames directory
        # must be refused, never silently accepted
        good = checkpoint_loads(checkpoint_dumps(half))
        bad_params = {**good, "params": {"max_side": 32, "threshold": 6.0, "block_threshold": 24.0}}
        bad_frames = {**good, "frame_count": 999}
        for bad_st, why in ((bad_params, "params"), (bad_frames, "frames directory")):
            try:
                learn(d, max_side=DEFAULT_MAX_SIDE, threshold=DEFAULT_THRESHOLD,
                      block_threshold=DEFAULT_BLOCK_THRESHOLD, state=bad_st)
                print(f"selftest FAIL: {why} mismatch must be refused")
                ok = False
            except SystemExit:
                pass

        same = d / "same"
        same.mkdir()
        for i in range(10):
            pnglite.write_png(same / f"frame_{i:05d}.png", w, h, frame(w, h, False))
        draft2 = learn(same, max_side=DEFAULT_MAX_SIDE, threshold=DEFAULT_THRESHOLD,
                       block_threshold=DEFAULT_BLOCK_THRESHOLD)[0]
        if draft2["segment_count"] != 1:
            print(f"selftest FAIL: static sequence must not split, got {draft2['segment_count']}")
            ok = False

        # PNG round-trip check: our own writer → our own reader, byte-equal
        probe = frame(37, 23, True)
        p = d / "probe.png"
        pnglite.write_png(p, 37, 23, probe)
        rw, rh, rc, rpix = pnglite.read_png(p)
        if (rw, rh, rc, rpix) != (37, 23, 4, probe):
            print("selftest FAIL: PNG round-trip mismatch")
            ok = False

        if ok:
            print("learn_route selftest: PASS (2-segment split at 8, anchor ~(0.7,0.7), static no-split, "
                  "resume==one-shot draft, checkpoint guards, PNG round-trip)")
            return 0
        return 1


def main() -> int:
    args = parse_args()
    if args.selftest:
        return selftest()
    frames_dir = Path(args.frames) if args.frames else None
    if frames_dir is None or not frames_dir.is_dir():
        print("learn_route: ERROR provide a frames directory (or --selftest)", file=sys.stderr)
        return 2
    out = Path(args.out) if args.out else frames_dir / "route-draft.json"
    ckpt = out.with_name(out.stem + ".checkpoint.json")

    state = None
    if args.resume:
        if not ckpt.exists():
            print(f"learn_route: ERROR --resume but no checkpoint at {ckpt}", file=sys.stderr)
            return 2
        state = checkpoint_loads(ckpt.read_text(encoding="utf-8"))
        print(f"learn_route: resuming from frame {state['next_index']}/{state['frame_count']}")

    def save_checkpoint(st: dict) -> None:
        tmp = ckpt.with_suffix(".tmp")
        tmp.write_text(checkpoint_dumps(st), encoding="utf-8")
        os.replace(tmp, ckpt)

    draft, st = learn(frames_dir, args.max_side, args.threshold, args.block_threshold,
                      state=state,
                      progress_every=(args.checkpoint_interval or None),
                      on_progress=(save_checkpoint if args.checkpoint_interval else None))
    if draft is None:
        print(f"learn_route: interrupted at frame {st['next_index']}/{st['frame_count']} — "
              f"resume with --resume (checkpoint {ckpt})", file=sys.stderr)
        return 3
    if ckpt.exists():
        ckpt.unlink()
    out.write_text(json.dumps(draft, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"learn_route: {draft['source']['frame_count']} frames -> "
          f"{draft['segment_count']} segment(s), grid {draft['grid']['w']}x{draft['grid']['h']}")
    print(f"learn_route: draft {out}")
    if args.inspect:
        for s in draft["segments"]:
            a = s.get("anchor") or {}
            print(f"  [{s['start_frame']:>5}..{s['end_frame']:>5}] "
                  f"entry(mean/peak)={s['entry_mean_diff']:>6}/{s['entry_peak_diff']:>6} "
                  f"anchor=({a.get('x', 0):.2f},{a.get('y', 0):.2f} {a.get('w', 0):.2f}x{a.get('h', 0):.2f})")
    print("next: annotate segments into SkillDefinition drafts (NC9 -> NC3), "
          "or validate a UI page with L0 probes on the anchor region")
    return 0


if __name__ == "__main__":
    sys.exit(main())
