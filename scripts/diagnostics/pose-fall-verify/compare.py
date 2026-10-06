"""对比评测：分别用「基线」与「开启沉出画面判据」跑同一批样本。

用来回答两个问题：
  1. 新判据能不能捞回 1280x720 走廊那条漏报？
  2. 它会不会在 URFD 的日常动作（adl-*）上制造新的误报？
"""

from __future__ import annotations

import glob
import os
import sys

import numpy as np

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from backend.pose.config import DetectionConfig  # noqa: E402
from backend.pose.detector import FallDetector, extract_features  # noqa: E402


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


def run_clip(model, cfg, path):
    import cv2

    cap = cv2.VideoCapture(path)
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    detector = FallDetector(cfg)
    alarms = []
    idx = 0
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        if idx % cfg.frame_stride != 0:
            idx += 1
            continue
        h, w = frame.shape[:2]
        if w > cfg.resize_width:
            s = cfg.resize_width / w
            frame = cv2.resize(frame, (int(w * s), int(h * s)))
            h, w = frame.shape[:2]
        res = model.predict(frame, imgsz=cfg.imgsz, conf=cfg.conf,
                            device=cfg.device, verbose=False)
        kpts = pick_primary(res[0])
        feat = extract_features(kpts, width=w, height=h, index=idx,
                                timestamp=idx / fps, cfg=cfg)
        det = detector.feed(feat)
        if det.triggered:
            alarms.append((round(feat.timestamp, 2), det.reason))
        idx += 1
    cap.release()
    return alarms


def main() -> int:
    from ultralytics import YOLO

    probe = DetectionConfig()
    model = YOLO(probe.model_path)

    samples = sorted(glob.glob("backend/pose/samples/*.mp4"))
    extra = ["tmp/fall-verify/input.mp4"]
    cases = [(p, "fall") for p in samples if os.path.basename(p).startswith("fall-")]
    cases += [(p, "adl") for p in samples if os.path.basename(p).startswith("adl-")]
    cases += [(p, "fall") for p in extra]

    print(f"{'片段':<34}{'真值':<7}{'基线告警':<12}{'新判据告警':<12}")
    print("-" * 66)
    stat = {"base": [0, 0, 0, 0], "new": [0, 0, 0, 0]}  # tp fp fn tn
    for path, truth in cases:
        name = os.path.basename(path)
        if path.startswith("tmp/"):
            name = "【微信视频】" + name
        cfg_a = DetectionConfig()
        al_a = run_clip(model, cfg_a, path)
        cfg_b = DetectionConfig()
        cfg_b.detect_frame_exit_fall = True
        al_b = run_clip(model, cfg_b, path)
        print(f"{name:<34}{truth:<7}{len(al_a):<12}{len(al_b):<12}")

        for tag, al in (("base", al_a), ("new", al_b)):
            got = len(al) > 0
            i = 0 if (truth == "fall" and got) else 1 if (truth == "adl" and got) \
                else 2 if (truth == "fall") else 3
            stat[tag][i] += 1

    print("-" * 66)
    for tag, label in (("base", "基线"), ("new", "新判据")):
        tp, fp, fn, tn = stat[tag]
        recall = tp / (tp + fn) if (tp + fn) else 0.0
        prec = tp / (tp + fp) if (tp + fp) else 0.0
        acc = (tp + tn) / max(tp + fp + fn + tn, 1)
        print(f"  {label}: TP={tp} FP={fp} FN={fn} TN={tn}"
              f"  |  召回={recall:.3f}  精确={prec:.3f}  准确={acc:.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
