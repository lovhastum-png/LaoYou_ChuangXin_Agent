"""鲁棒性测试：候选标定配置在丢帧情况下还能不能检出 B。

真实部署会掉帧、降分辨率。这里用 frame_stride（每 N 帧推理一次）
模拟丢帧，检查各候选配置的检出余量。

同时打印 B 躺下平台的实测角度区间，用于判断阈值余量。
"""

from __future__ import annotations

import os
import sys

import cv2
import numpy as np

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from backend.pose.config import DetectionConfig  # noqa: E402
from backend.pose.detector import FallDetector, FrameFeatures, extract_features  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
B_VIDEO = os.path.join(ROOT, "tmp", "fall-verify2", "input.mp4")

CANDIDATES = [
    (62.0, 5, "基线"),
    (58.0, 4, "候选1"),
    (55.0, 5, "候选2"),
    (52.0, 5, "候选3"),
    (50.0, 5, "候选4"),
]


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


def extract(stride: int, resize: int):
    from ultralytics import YOLO

    cfg = DetectionConfig()
    cfg.frame_stride = stride
    cfg.resize_width = resize
    model = YOLO(cfg.model_path)

    cap = cv2.VideoCapture(B_VIDEO)
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    rows = []
    idx = 0
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        if idx % stride != 0:
            idx += 1
            continue
        h, w = frame.shape[:2]
        if w > resize:
            s = resize / w
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
    return rows


def replay(cfg, rows):
    det = FallDetector(cfg)
    for idx, ts, present, angle, vr, nose, hip in rows:
        f = FrameFeatures(index=idx, timestamp=ts, present=bool(present),
                          torso_angle=angle, vertical_ratio=vr,
                          nose_y=nose, hip_y=hip)
        r = det.feed(f)
        if r.triggered:
            return round(ts, 2)
    return None


def main() -> int:
    variants = [
        (1, 960, "全帧 / 960宽（默认）"),
        (2, 960, "隔帧 / 960宽"),
        (3, 960, "3帧取1 / 960宽"),
        (1, 640, "全帧 / 640宽"),
        (2, 640, "隔帧 / 640宽"),
    ]

    results = {}
    for stride, resize, label in variants:
        rows = extract(stride, resize)
        results[label] = rows
        # 打印躺下平台的角度范围
        lie = [a for _, ts, p, a, vr, _, _ in rows if p and 3.4 < ts < 4.0]
        if lie:
            print(f"  {label:<22} 躺下平台({len(lie)}帧) 角度 "
                  f"{min(lie):.1f}~{max(lie):.1f}°  "
                  f"中位 {np.median(lie):.1f}°", flush=True)

    print(f"\n=== 各配置在丢帧/降分辨率下的检出情况（B 素材）===")
    print(f"{'配置':<10}{'角度':>5}{'确认帧':>7}   " +
          "".join(f"{v[2][:12]:>14}" for v in variants))
    for ang, conf, tag in CANDIDATES:
        cfg = DetectionConfig()
        cfg.torso_angle_threshold = ang
        cfg.lying_confirm_frames = conf
        cells = []
        for stride, resize, label in variants:
            t = replay(cfg, results[label])
            cells.append("✓" + str(t) if t else "✗")
        print(f"{tag:<10}{ang:>5}{conf:>7}   " +
              "".join(f"{c:>14}" for c in cells))

    print("\n判读：全列都是 ✓ 的配置才有实用余量。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
