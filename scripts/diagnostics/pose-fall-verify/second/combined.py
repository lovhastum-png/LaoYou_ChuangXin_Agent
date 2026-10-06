"""组合方案验证：把两个修复叠加，看能否同时覆盖 A / B 两类机位。

  · 修复一：detect_frame_exit_fall（覆盖 A：扑倒后沉出画面）
  · 修复二：torso_angle_threshold 降到 50（覆盖 B：吊装机位躺地角度被压缩）

同时检查是否在 URFD 日常动作上新增误报。
"""

from __future__ import annotations

import glob
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from backend.pose.config import DetectionConfig  # noqa: E402
from backend.pose.detector import FallDetector  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sweep import extract_all  # noqa: E402


def replay(cfg, feats):
    det = FallDetector(cfg)
    alarms = []
    for f in feats:
        r = det.feed(f)
        if r.triggered:
            alarms.append((round(f.timestamp, 2), r.reason))
    return alarms


def main() -> int:
    from ultralytics import YOLO

    base = DetectionConfig()
    model = YOLO(base.model_path)

    cases = []
    for p in sorted(glob.glob("backend/pose/samples/*.mp4")):
        n = os.path.basename(p)
        if n.startswith(("fall-", "adl-")):
            cases.append((p, "fall" if n.startswith("fall-") else "adl", n))
    cases.append(("tmp/fall-verify/input.mp4", "fall", "A-走廊平视"))
    cases.append(("tmp/fall-verify2/input.mp4", "fall", "B-房间吊装"))

    print("推理中…")
    cache = [(truth, name, extract_all(model, base, path))
             for path, truth, name in cases]

    configs = {
        "基线 62° / 无沉出画面": dict(angle=62.0, exit=False),
        "仅沉出画面": dict(angle=62.0, exit=True),
        "仅角度 50°": dict(angle=50.0, exit=False),
        "两者都要（62→50 + 沉出画面）": dict(angle=50.0, exit=True),
    }

    results = {}
    for label, opt in configs.items():
        cfg = DetectionConfig()
        cfg.torso_angle_threshold = opt["angle"]
        cfg.detect_frame_exit_fall = opt["exit"]
        rows = []
        tp = fp = fn = tn = 0
        for truth, name, feats in cache:
            al = replay(cfg, feats)
            got = len(al) > 0
            rows.append((name, truth, al))
            if truth == "fall" and got:
                tp += 1
            elif truth == "fall":
                fn += 1
            elif got:
                fp += 1
            else:
                tn += 1
        recall = tp / (tp + fn) if (tp + fn) else 0.0
        prec = tp / (tp + fp) if (tp + fp) else 0.0
        results[label] = (rows, tp, fp, fn, tn, recall, prec)

    for label, (rows, tp, fp, fn, tn, recall, prec) in results.items():
        print(f"\n=== {label} ===")
        print(f"{'片段':<26}{'真值':<6}{'告警':<5}时刻")
        for name, truth, al in rows:
            mark = "" if (truth == "fall") == (len(al) > 0) else "   ← 不一致"
            times = ", ".join(f"{t}s" for t, _ in al[:3])
            print(f"{name:<26}{truth:<6}{len(al):<5}{times}{mark}")
        print(f"  TP={tp} FP={fp} FN={fn} TN={tn}"
              f"  |  召回={recall:.3f}  精确={prec:.3f}")

    # 组合方案下 A / B 的触发理由
    best = "两者都要（62→50 + 沉出画面）"
    print(f"\n=== 「{best}」触发理由 ===")
    for name, truth, al in results[best][0]:
        if al:
            print(f"  {name}: " + " | ".join(f"{t}s → {r}" for t, r in al))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
