"""URFD 全量评测：横卧角度阈值扫描（按唯一场景口径）。

README 记录的基线（62°）：按唯一场景 召回 0.700 / 精确 0.750。
本脚本复跑全量，并加上 45~55° 几档，回答一个问题：
**把 torso_angle_threshold 从 62 降到 50，在 URFD 全量上的精确度代价是多少？**

做法：100 分卷各推理一次，特征缓存到 npz；之后重放阈值几乎零成本。
"""

from __future__ import annotations

import glob
import os
import pickle
import sys

import cv2
import numpy as np

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from backend.pose.config import DetectionConfig  # noqa: E402
from backend.pose.detector import FallDetector, extract_features  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, "urfd-features.pkl")
URFD = "tmp/urfd"


def pick_primary(result):
    if result.keypoints is None or result.keypoints.data is None:
        return None
    data = result.keypoints.data.cpu().numpy()
    if len(data) == 0:
        return None
    if result.boxes is not None and len(result.boxes) > 0:
        boxes = result.boxes.xyxy.cpu().numpy()
        areas = (boxes[:, 2] - boxes[:, 0]) * (boxes[:, 3] - boxes[:, 1])
        return data[int(np.argmax(areas))]
    return data[0]


def build_cache():
    from ultralytics import YOLO

    cfg = DetectionConfig()
    model = YOLO(cfg.model_path)
    clips = sorted(glob.glob(os.path.join(URFD, "*.mp4")))
    cache = {}
    for i, path in enumerate(clips, 1):
        name = os.path.basename(path)
        cap = cv2.VideoCapture(path)
        fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        rows = []
        idx = 0
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            h, w = frame.shape[:2]
            if w > cfg.resize_width:
                s = cfg.resize_width / w
                frame = cv2.resize(frame, (int(w * s), int(h * s)))
                h, w = frame.shape[:2]
            res = model.predict(frame, imgsz=cfg.imgsz, conf=cfg.conf,
                                device=cfg.device, verbose=False)
            f = extract_features(pick_primary(res[0]), width=w, height=h,
                                 index=idx, timestamp=idx / fps, cfg=cfg)
            rows.append((idx, idx / fps, int(f.present), f.torso_angle,
                         f.vertical_ratio, f.nose_y, f.hip_y))
            idx += 1
        cap.release()
        cache[name] = rows
        print(f"  [{i}/{len(clips)}] {name}  {len(rows)} 帧", flush=True)
    with open(CACHE, "wb") as fh:
        pickle.dump(cache, fh)
    print(f"特征已缓存：{CACHE}")
    return cache


def load_cache():
    if os.path.exists(CACHE):
        with open(CACHE, "rb") as fh:
            return pickle.load(fh)
    return None


def replay(cfg, rows) -> int:
    from backend.pose.detector import FrameFeatures

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
    cache = load_cache() or build_cache()

    scenes = {}
    for name, rows in cache.items():
        k = scene_key(name)
        scenes.setdefault(k, {"truth": "fall" if k.startswith("fall-") else "adl",
                              "clips": []})["clips"].append(rows)

    print(f"\n分卷 {len(cache)} 段 / 唯一场景 {len(scenes)} 个")

    thresholds = [62.0, 58.0, 55.0, 52.0, 50.0, 48.0, 45.0, 42.0, 40.0]
    print(f"\n=== 横卧角度阈值扫描（URFD 全量，按唯一场景）===")
    print(f"{'阈值':>5}{'TP':>4}{'FP':>4}{'FN':>4}{'TN':>4}"
          f"{'召回':>9}{'精确':>9}{'F1':>8}{'准确':>8}")
    best = None
    for t in thresholds:
        cfg = DetectionConfig()
        cfg.torso_angle_threshold = t
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
        recall = tp / (tp + fn) if (tp + fn) else 0.0
        prec = tp / (tp + fp) if (tp + fp) else 0.0
        f1 = 2 * recall * prec / (recall + prec) if (recall + prec) else 0.0
        acc = (tp + tn) / max(tp + fp + fn + tn, 1)
        print(f"{t:>5.0f}{tp:>4}{fp:>4}{fn:>4}{tn:>4}"
              f"{recall:>9.3f}{prec:>9.3f}{f1:>8.3f}{acc:>8.3f}")
        if best is None or f1 > best[1]:
            best = (t, f1, recall, prec)
    print(f"\n  F1 最优：阈值 {best[0]:.0f}°  F1={best[1]:.3f}"
          f"  召回={best[2]:.3f}  精确={best[3]:.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
