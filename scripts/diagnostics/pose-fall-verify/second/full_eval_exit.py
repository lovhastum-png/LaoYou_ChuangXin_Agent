"""用已缓存的 URFD 特征，补验「沉出画面」补丁在全量上的代价。

不需要重新推理（cache 已在 urfd-features.pkl），秒出结果。
"""

from __future__ import annotations

import os
import pickle
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from backend.pose.config import DetectionConfig  # noqa: E402
from backend.pose.detector import FallDetector, FrameFeatures  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, "urfd-features.pkl")


def replay(cfg, rows) -> int:
    det = FallDetector(cfg)
    n = 0
    for idx, ts, present, angle, vr, nose, hip in rows:
        f = FrameFeatures(index=idx, timestamp=ts, present=bool(present),
                          torso_angle=angle, vertical_ratio=vr,
                          nose_y=nose, hip_y=hip)
        if det.feed(f).triggered:
            n += 1
    return n


def scene_key(name: str) -> str:
    return name.replace("-cam0.mp4", "").replace("-cam1.mp4", "")


def main() -> int:
    cache = pickle.load(open(CACHE, "rb"))
    scenes = {}
    for name, rows in cache.items():
        k = scene_key(name)
        scenes.setdefault(k, {"truth": "fall" if k.startswith("fall-") else "adl",
                              "clips": []})["clips"].append(rows)

    print(f"URFD 全量：分卷 {len(cache)} / 唯一场景 {len(scenes)}")
    print(f"\n{'角度':>5}{'沉出画面':>10}{'TP':>4}{'FP':>4}{'FN':>4}{'TN':>4}"
          f"{'召回':>9}{'精确':>9}{'F1':>8}")

    for angle in (62.0, 50.0):
        for exit_fall in (False, True):
            cfg = DetectionConfig()
            cfg.torso_angle_threshold = angle
            cfg.detect_frame_exit_fall = exit_fall
            tp = fp = fn = tn = 0
            for k, s in scenes.items():
                got = any(replay(cfg, clips) > 0 for clips in s["clips"])
                if s["truth"] == "fall" and got:
                    tp += 1
                elif s["truth"] == "fall":
                    fn += 1
                elif got:
                    fp += 1
                else:
                    tn += 1
            r = tp / (tp + fn) if (tp + fn) else 0.0
            p = tp / (tp + fp) if (tp + fp) else 0.0
            f1 = 2 * r * p / (r + p) if (r + p) else 0.0
            print(f"{angle:>5.0f}{('开' if exit_fall else '关'):>10}"
                  f"{tp:>4}{fp:>4}{fn:>4}{tn:>4}{r:>9.3f}{p:>9.3f}{f1:>8.3f}")

    # 「沉出画面」单独触发了哪些场景
    print("\n=== 沉出画面判据单独命中的场景（62° 下）===")
    cfg_off = DetectionConfig()
    cfg_off.torso_angle_threshold = 62.0
    cfg_on = DetectionConfig()
    cfg_on.torso_angle_threshold = 62.0
    cfg_on.detect_frame_exit_fall = True
    for k, s in sorted(scenes.items()):
        a = any(replay(cfg_off, c) > 0 for c in s["clips"])
        b = any(replay(cfg_on, c) > 0 for c in s["clips"])
        if a != b:
            print(f"  {k}  真值={s['truth']}  关={a} → 开={b}"
                  f"   {'← 新增误报' if s['truth'] == 'adl' else '← 捞回跌倒'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
