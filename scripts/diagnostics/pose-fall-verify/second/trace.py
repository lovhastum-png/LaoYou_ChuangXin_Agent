"""逐帧追踪一段视频的跌倒检测特征，输出 CSV + 摘要。

不做注入，纯检测。用于人工核对阈值红线落在哪一帧。
"""

from __future__ import annotations

import argparse
import csv
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


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", required=True)
    ap.add_argument("--out", default=None)
    ap.add_argument("--stride", type=int, default=1)
    ap.add_argument("--exit-fall", action="store_true",
                    help="开启「坠落并沉出画面」补充判据")
    ap.add_argument("--angle", type=float, default=None,
                    help="躯干横卧角度阈值（默认取配置值 62）")
    args = ap.parse_args()

    from ultralytics import YOLO

    cfg = DetectionConfig()
    cfg.frame_stride = args.stride
    cfg.detect_frame_exit_fall = args.exit_fall
    if args.angle is not None:
        cfg.torso_angle_threshold = args.angle
    model = YOLO(cfg.model_path)

    cap = cv2.VideoCapture(args.video)
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    print(f"视频 {os.path.basename(args.video)}: {total} 帧 @ {fps:.2f}fps "
          f"= {total / fps:.2f}s")

    detector = FallDetector(cfg)
    rows = []
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
        ts = idx / fps
        feat = extract_features(kpts, width=w, height=h, index=idx,
                                timestamp=ts, cfg=cfg)
        det = detector.feed(feat)
        rows.append({
            "idx": idx,
            "t": round(ts, 3),
            "state": det.state.value,
            "present": int(feat.present),
            "torso_angle": round(feat.torso_angle, 1),
            "vr": round(feat.vertical_ratio, 3),
            "nose_y": round(feat.nose_y, 3),
            "hip_y": round(feat.hip_y, 3),
            "descent": round(feat.descent_speed, 3),
            "ang_vel": round(feat.angular_velocity, 1),
            "motion": round(feat.motion, 4),
            "lying": int(feat.lying),
            "falling": int(feat.falling),
            "jump": int(feat.angle_jump),
            "alarm": int(det.triggered),
            "reason": det.reason,
        })
        if det.triggered:
            print(f"  [ALARM] idx={idx} t={ts:.2f}s :: {det.reason}")
            print(f"          {det.detail}")
        idx += 1
    cap.release()

    out = args.out or os.path.join(os.path.dirname(args.video), "trace.csv")
    with open(out, "w", newline="", encoding="utf-8") as fh:
        wr = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        wr.writeheader()
        wr.writerows(rows)

    alarms = [r for r in rows if r["alarm"]]
    present = [r for r in rows if r["present"]]
    lying = [r for r in rows if r["lying"]]
    falling = [r for r in rows if r["falling"]]

    print("\n=== 摘要 ===")
    print(f"  处理帧数        : {len(rows)}")
    print(f"  检出人体帧数    : {len(present)} ({len(present)/max(len(rows),1)*100:.0f}%)")
    print(f"  横卧帧数        : {len(lying)}")
    print(f"  坠落信号帧数    : {len(falling)}")
    print(f"  告警次数        : {len(alarms)}")
    if present:
        print(f"  躯干角范围      : "
              f"{min(r['torso_angle'] for r in present):.0f}° ~ "
              f"{max(r['torso_angle'] for r in present):.0f}°")
        print(f"  下降速度峰值    : "
              f"{max(r['descent'] for r in present):.3f} 画面高/秒 "
              f"(阈值 {cfg.descent_speed_threshold})")
        print(f"  角速度峰值      : "
              f"{max(r['ang_vel'] for r in present):.0f} °/秒 "
              f"(阈值 {cfg.angular_velocity_threshold})")
    print(f"  逐帧明细        : {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
