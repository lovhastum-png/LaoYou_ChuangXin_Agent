"""批量统计各样本的横卧持续时长与运动学峰值。

用途：为 config.DetectionConfig 的阈值标定提供实测依据。
不是产品代码，仅作为评测工具保留。

运行：
  backend/pose/.venv/Scripts/python.exe -X utf8 backend/pose/calibrate.py
"""

from __future__ import annotations

import glob
import os
import sys

import cv2
from ultralytics import YOLO

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from backend.pose.config import DetectionConfig  # noqa: E402
from backend.pose.detector import extract_features  # noqa: E402


def scan(path: str, cfg: DetectionConfig, model) -> dict:
    cap = cv2.VideoCapture(path)
    if not cap.isOpened():
        return {"error": "无法读取"}
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0

    best_lying = 0.0
    best_lying_at = 0.0
    cur = 0
    cur_start = 0.0
    peak_desc = 0.0
    peak_desc_at = 0.0
    peak_ang = 0.0
    peak_ang_at = 0.0
    peak_angle = 0.0
    frames_present = 0
    prev = None
    idx = 0

    while True:
        ok, frame = cap.read()
        if not ok:
            break
        h, w = frame.shape[:2]
        res = model.predict(
            frame, imgsz=cfg.imgsz, conf=cfg.conf, device=cfg.device, verbose=False
        )[0]
        data = (
            res.keypoints.data.cpu().numpy()
            if res.keypoints is not None and res.keypoints.data is not None
            else None
        )
        kpts = data[0] if data is not None and len(data) else None
        ts = idx / fps
        ft = extract_features(
            kpts, width=w, height=h, index=idx, timestamp=ts, cfg=cfg
        )

        if prev is not None and prev.present and ft.present:
            dt = ts - prev.timestamp
            if dt > 1e-6:
                if ft.nose_y > 0 and prev.nose_y > 0:
                    v = (ft.nose_y - prev.nose_y) / dt
                    if v > peak_desc:
                        peak_desc, peak_desc_at = v, ts
                a = abs(ft.torso_angle - prev.torso_angle) / dt
                if a > peak_ang:
                    peak_ang, peak_ang_at = a, ts

        if ft.present:
            frames_present += 1
            peak_angle = max(peak_angle, ft.torso_angle)

        if ft.lying:
            if cur == 0:
                cur_start = ts
            cur += 1
            # 用「已经横卧了多少秒」衡量持续时长。
            # 注意不能用 ts - cur_start，因为 ts 是秒而横卧起点可能落在同一帧，
            # 首帧差值为 0，容易让人误读为"从未横卧"。
            dur = cur / fps
            if dur > best_lying:
                best_lying, best_lying_at = dur, cur_start
        else:
            cur = 0

        prev = ft
        idx += 1

    cap.release()
    return {
        "fps": dps_round(fps),
        "frames": idx,
        "frames_present": frames_present,
        "max_lying_seconds": dps_round(best_lying),
        "max_lying_at": dps_round(best_lying_at),
        "peak_torso_angle": dps_round(peak_angle),
        "peak_descent_speed": dps_round(peak_desc),
        "peak_descent_at": dps_round(peak_desc_at),
        "peak_angular_velocity": dps_round(peak_ang),
        "peak_angular_at": dps_round(peak_ang_at),
    }


def dps_round(x: float) -> float:
    return round(float(x), 3)


def main() -> int:
    cfg = DetectionConfig()
    model = YOLO(cfg.model_path)
    samples = sorted(glob.glob(os.path.join(os.path.dirname(__file__), "samples", "*.mp4")))
    if not samples:
        print("samples/ 下没有 mp4")
        return 1

    header = (
        f"{'片段':<24}{'横卧':>7}{'横卧@t':>9}{'峰值角':>8}"
        f"{'峰值下降':>9}{'下降@t':>9}{'峰值角速度':>11}"
    )
    print(header)
    print("-" * len(header))
    rows = []
    for p in samples:
        r = scan(p, cfg, model)
        name = os.path.basename(p)
        rows.append((name, r))
        if "error" in r:
            print(f"{name:<24}  {r['error']}")
            continue
        print(
            f"{name:<24}{r['max_lying_seconds']:>7}{r['max_lying_at']:>9}"
            f"{r['peak_torso_angle']:>8}{r['peak_descent_speed']:>9}"
            f"{r['peak_descent_at']:>9}{r['peak_angular_velocity']:>11}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
