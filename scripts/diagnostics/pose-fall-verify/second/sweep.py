"""机位标定扫描：横卧角度阈值 与 角速度阈值 的取舍。

背景：两段真机位素材给出互相矛盾的需求 ——
  · 素材 A（走廊平视跟拍，人扑倒后沉出画面）：需要「沉出画面」判据，角度阈值无关
  · 素材 B（房间吊装高机位，人躺在地上）：躺下时躯干只投射到 40-60°，
    够不到按近景侧视标定的 62°，横卧只确认到 1 帧 → 漏报

本脚本把每段视频的姿态特征**推理一次并缓存**，然后快速重放多组阈值，
量化「降低角度阈值能捞回多少」与「会不会在 URFD 日常动作上制造误报」。

运行：
  backend/pose/.venv/Scripts/python.exe -X utf8 tmp/fall-verify2/sweep.py
"""

from __future__ import annotations

import glob
import os
import sys

import cv2
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


def extract_all(model, cfg, path):
    """推理一次，缓存特征。"""
    cap = cv2.VideoCapture(path)
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    feats = []
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
        feats.append(extract_features(pick_primary(res[0]), width=w, height=h,
                                      index=idx, timestamp=idx / fps, cfg=cfg))
        idx += 1
    cap.release()
    return feats


def replay(cfg, feats):
    det = FallDetector(cfg)
    return sum(1 for f in feats if det.feed(f).triggered)


def main() -> int:
    from ultralytics import YOLO

    base = DetectionConfig()
    model = YOLO(base.model_path)

    cases = []
    for p in sorted(glob.glob("backend/pose/samples/*.mp4")):
        n = os.path.basename(p)
        if n.startswith("fall-"):
            cases.append((p, "fall", n))
        elif n.startswith("adl-"):
            cases.append((p, "adl", n))
    cases.append(("tmp/fall-verify/input.mp4", "fall", "A-走廊平视(扑倒出画)"))
    cases.append(("tmp/fall-verify2/input.mp4", "fall", "B-房间吊装(躺地)"))

    print("推理中（每段只跑一次模型）…")
    cache = [(path, truth, name, extract_all(model, base, path))
             for path, truth, name in cases]

    # 每段视频的横卧角度峰值分布（帮助判断阈值该放哪）
    print("\n=== 各素材横卧角度峰值 / 持续（角度阈值不参与，仅统计）===")
    print(f"{'片段':<26}{'真值':<6}{'角度p50':>8}{'角度p90':>8}{'角度max':>9}"
          f"{'≥62帧数':>9}{'≥50帧数':>9}{'≥45帧数':>9}")
    for path, truth, name, feats in cache:
        ang = np.array([f.torso_angle for f in feats if f.present])
        if len(ang) == 0:
            print(f"{name:<26}{truth:<6}{'—':>8}{'—':>8}{'—':>9}"
                  f"{'0':>9}{'0':>9}{'0':>9}")
            continue
        print(f"{name:<26}{truth:<6}{np.percentile(ang,50):>8.1f}"
              f"{np.percentile(ang,90):>8.1f}{ang.max():>9.1f}"
              f"{int((ang>=62).sum()):>9}{int((ang>=50).sum()):>9}"
              f"{int((ang>=45).sum()):>9}")

    # 阈值组合扫描
    combos = []
    for ang_t in (62.0, 55.0, 50.0, 45.0, 40.0, 35.0):
        for angv_t in (600.0, 450.0, 300.0):
            combos.append((ang_t, angv_t))

    print("\n=== 阈值扫描（召回/精确按 9 段素材：6 fall + 3 adl）===")
    print(f"{'角度阈值':>9}{'角速度阈值':>11}{'TP':>4}{'FP':>4}{'FN':>4}{'TN':>4}"
          f"{'召回':>8}{'精确':>8}   明细")
    for ang_t, angv_t in combos:
        cfg = DetectionConfig()
        cfg.torso_angle_threshold = ang_t
        cfg.angular_velocity_threshold = angv_t
        tp = fp = fn = tn = 0
        detail = []
        for path, truth, name, feats in cache:
            got = replay(cfg, feats) > 0
            if truth == "fall" and got:
                tp += 1
                detail.append(f"{name[:6]}=✓")
            elif truth == "fall":
                fn += 1
                detail.append(f"{name[:6]}=✗")
            elif got:
                fp += 1
                detail.append(f"{name[:6]}=误报!")
            else:
                tn += 1
        r = tp / (tp + fn) if (tp + fn) else 0.0
        p = tp / (tp + fp) if (tp + fp) else 0.0
        print(f"{ang_t:>9.0f}{angv_t:>11.0f}{tp:>4}{fp:>4}{fn:>4}{tn:>4}"
              f"{r:>8.3f}{p:>8.3f}   {' '.join(detail)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
