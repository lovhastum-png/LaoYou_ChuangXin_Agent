"""URFD 全量批量评估 —— 跌倒检测 + 平安看护合并链路。

用法：
    backend/pose/.venv/Scripts/python.exe -X utf8 -m backend.pose.evaluate \
        --dir tmp/urfd --out tmp/urfd_eval_result.json

为什么不用 `run.py --eval`：
  1. 那个入口只跑跌倒检测，不含平安看护的四态判定；
  2. 它按「目录里所有 mp4」计数，而 URFD 的 fall 是 cam0/cam1 双机位，
     同一场景两条视频会被重复计数，虚增样本量；
  3. 它不检查视频是否可解码，坏文件会被当成"无告警"，
     直接算成漏检，污染指标。

本脚本的处理：
  - 逐段解码校验，打不开或读不到帧的直接剔除并单独列出，不混进指标；
  - 主口径按「唯一场景」算（fall 同场景的 cam0/cam1 任一台报警即算命中），
    同时输出按「分卷」算的口径，两个数都给出来，不挑对自己有利的那个；
  - 同时跑跌倒检测与平安看护，给出两套指标。
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import sys

import cv2
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from backend.pose.config import (  # noqa: E402
    DEFAULT_DETECTION,
    DEFAULT_PRESENCE,
    DetectionConfig,
    PresenceConfig,
)
from backend.pose.detector import FallDetector, extract_features  # noqa: E402
from backend.pose.presence import PresenceMonitor, PresenceState  # noqa: E402
from backend.pose.presence_run import _load_model, _pick_for_fall, _pick_primary  # noqa: E402
from backend.pose.presence import feature_from_frame  # noqa: E402


def decode_ok(path: str) -> tuple[bool, int, float]:
    """返回 (可解码, 帧数, fps)。读不出帧即视为坏文件。"""
    cap = cv2.VideoCapture(path)
    if not cap.isOpened():
        return False, 0, 0.0
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    ok, frame = cap.read()
    if not ok or frame is None:
        cap.release()
        return False, 0, fps
    n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    cap.release()
    return True, n, fps


def run_clip(path: str, model, det_cfg: DetectionConfig, pres_cfg: PresenceConfig):
    """跑一段视频，同时返回跌倒检测与平安看护的结论。"""
    cap = cv2.VideoCapture(path)
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    detector = FallDetector(det_cfg)
    monitor = PresenceMonitor(pres_cfg)

    fall_hit = False
    fall_frame = None
    fall_reason = ""
    states: list[str] = []
    idx = 0
    frames_processed = 0

    while True:
        ok, frame = cap.read()
        if not ok:
            break
        h0, w0 = frame.shape[:2]
        if w0 > pres_cfg.resize_width:
            scale = pres_cfg.resize_width / w0
            frame = cv2.resize(frame, (int(w0 * scale), int(h0 * scale)))
        w, h = frame.shape[:2]
        ts = idx / fps

        results = model.predict(
            frame, imgsz=pres_cfg.imgsz, conf=pres_cfg.conf,
            device=pres_cfg.device, verbose=False,
        )
        frames_processed += 1

        # --- 平安看护取人（严格门槛） ---
        kpts, center, _q = _pick_primary(results[0], pres_cfg)
        feat = extract_features(kpts, width=w, height=h, index=idx,
                               timestamp=ts, cfg=DEFAULT_DETECTION)

        # --- 摔倒检测取人（宽松，不套门槛） ---
        fall_kpts = _pick_for_fall(results[0], pres_cfg)
        ffeat = extract_features(fall_kpts, width=w, height=h, index=idx,
                                 timestamp=ts, cfg=DEFAULT_DETECTION)
        fres = detector.feed(ffeat)
        fallen, freason, fdetail = False, "", {}
        if fres.triggered:
            fallen, freason, fdetail = True, fres.reason, dict(fres.detail or {})
            if not fall_hit:
                fall_hit = True
                fall_frame = idx
                fall_reason = fres.reason

        pfeat = feature_from_frame(feat, center)
        pfeat.fallen, pfeat.fallen_reason, pfeat.fall_detail = fallen, freason, fdetail
        pres = monitor.feed(pfeat)
        states.append(pres.state.value)
        idx += 1

    cap.release()
    return {
        "fall_hit": fall_hit,
        "fall_frame": fall_frame,
        "fall_reason": fall_reason,
        "saw_fallen": PresenceState.FALLEN.value in states,
        "ever_in_view": PresenceState.IN_VIEW.value in states,
        "frames": frames_processed,
    }


def _scene_key(name: str) -> str:
    """fall-01-cam0.mp4 -> fall-01（用于双机位合并计数）。"""
    base = name[:-4]
    if base.endswith("-cam0") or base.endswith("-cam1"):
        return base[:-5]
    return base


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default="tmp/urfd")
    ap.add_argument("--out", default="tmp/urfd_eval_result.json")
    ap.add_argument("--limit", type=int, default=None, help="只跑前 N 段（调试用）")
    args = ap.parse_args()

    files = sorted(glob.glob(os.path.join(args.dir, "*.mp4")))
    if not files:
        print(f"目录内没有 mp4：{args.dir}")
        return 1

    print(f"发现 {len(files)} 个 mp4，先做解码校验…")
    good, bad = [], []
    for p in files:
        okd, n, fps = decode_ok(p)
        name = os.path.basename(p)
        if okd and n > 0:
            good.append((p, name, n, fps))
        else:
            bad.append(name)
    print(f"  可解码 {len(good)}，坏文件 {len(bad)}")
    for b in bad:
        print(f"    [坏] {b}")

    if args.limit:
        good = good[: args.limit]

    print(f"\n加载模型…")
    pres_cfg = DEFAULT_PRESENCE
    pres_cfg.frame_stride = 1
    det_cfg = DetectionConfig()
    det_cfg.frame_stride = 1
    model = _load_model(pres_cfg)

    rows = []
    for i, (p, name, n, fps) in enumerate(good, 1):
        r = run_clip(p, model, det_cfg, pres_cfg)
        truth = "fall" if name.startswith("fall-") else "adl"
        r.update({"video": name, "scene": _scene_key(name), "truth": truth,
                  "src_frames": n, "fps": round(fps, 1)})
        rows.append(r)
        mark = "命中" if r["fall_hit"] else "未报"
        print(f"  [{i:>3}/{len(good)}] {name:<24} 真值={truth:<5} "
              f"跌倒={mark:<4} 看护摔倒={str(r['saw_fallen']):<5} 帧={r['frames']}",
              flush=True)

    # ---------------- 分卷口径 ----------------
    tp = fp = fn = tn = 0
    for r in rows:
        if r["truth"] == "fall":
            if r["fall_hit"]:
                tp += 1
            else:
                fn += 1
        else:
            if r["fall_hit"]:
                fp += 1
            else:
                tn += 1

    # ---------------- 唯一场景口径 ----------------
    scenes: dict[str, dict] = {}
    for r in rows:
        key = r["scene"]
        s = scenes.setdefault(key, {"truth": r["truth"], "hit": False, "clips": 0})
        s["clips"] += 1
        s["hit"] = s["hit"] or r["fall_hit"]
    stp = sfp = sfn = stn = 0
    for key, s in sorted(scenes.items()):
        if s["truth"] == "fall":
            if s["hit"]:
                stp += 1
            else:
                sfn += 1
        else:
            if s["hit"]:
                sfp += 1
            else:
                stn += 1

    def metrics(a, b, c, d):
        recall = a / (a + c) if (a + c) else 0.0
        precision = a / (a + b) if (a + b) else 0.0
        acc = (a + d) / (a + b + c + d) if (a + b + c + d) else 0.0
        f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0
        return {
            "TP": a, "FP": b, "FN": c, "TN": d,
            "recall": round(recall, 4), "precision": round(precision, 4),
            "accuracy": round(acc, 4), "f1": round(f1, 4),
        }

    clip_m = metrics(tp, fp, fn, tn)
    scene_m = metrics(stp, sfp, sfn, stn)

    # 看护链路一致性：摔倒场景中，看护是否也进入 fallen
    fall_rows = [r for r in rows if r["truth"] == "fall"]
    fall_detected = [r for r in fall_rows if r["fall_hit"]]
    consistent = sum(1 for r in fall_detected if r["saw_fallen"])

    print("\n" + "=" * 62)
    print("评估结果（URFD 全量）")
    print("=" * 62)
    print(f"  可解码分卷：{len(rows)}    剔除坏文件：{len(bad)}")
    print(f"\n  【口径 A：按分卷计】共 {len(rows)} 段")
    print(f"    TP={clip_m['TP']} FP={clip_m['FP']} FN={clip_m['FN']} TN={clip_m['TN']}")
    print(f"    召回={clip_m['recall']:.3f}  精确={clip_m['precision']:.3f}"
          f"  准确={clip_m['accuracy']:.3f}  F1={clip_m['f1']:.3f}")
    print(f"\n  【口径 B：按唯一场景计】共 {len(scenes)} 个场景"
          f"（fall 双机位合并）")
    print(f"    TP={scene_m['TP']} FP={scene_m['FP']} FN={scene_m['FN']} TN={scene_m['TN']}")
    print(f"    召回={scene_m['recall']:.3f}  精确={scene_m['precision']:.3f}"
          f"  准确={scene_m['accuracy']:.3f}  F1={scene_m['f1']:.3f}")
    print(f"\n  看护链路一致性：{len(fall_detected)} 段检出的摔倒中，"
          f"{consistent} 段看护也进入 fallen 态"
          f"（{consistent / len(fall_detected):.1%}）" if fall_detected else "")

    if fn:
        print(f"\n  漏检（未报警的摔倒）：")
        for r in rows:
            if r["truth"] == "fall" and not r["fall_hit"]:
                print(f"    {r['video']}（{r['frames']} 帧）")
    if fp:
        print(f"\n  误报（日常动作被报成摔倒）：")
        for r in rows:
            if r["truth"] == "adl" and r["fall_hit"]:
                print(f"    {r['video']}  frame={r['fall_frame']}  {r['fall_reason']}")

    out = {
        "dataset": "URFD (University of Rzeszow Fall Detection Dataset)",
        "dir": args.dir,
        "decodable": len(rows),
        "bad_files": bad,
        "by_clip": clip_m,
        "by_scene": scene_m,
        "scene_count": len(scenes),
        "care_chain_consistency": {
            "detected_falls": len(fall_detected),
            "care_also_fallen": consistent,
        },
        "rows": rows,
    }
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=2)
    print(f"\n  结果已写入：{args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
