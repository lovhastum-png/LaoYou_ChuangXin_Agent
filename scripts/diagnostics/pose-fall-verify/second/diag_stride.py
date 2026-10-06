"""诊断：为什么隔帧时素材 B 检不出来？

打印 stride=1 / 2 / 3 下，素材 B 关键区间的角度序列与检测器逐帧判定，
定位卡在哪一步（坠落证据？横卧确认？）。
"""

from __future__ import annotations

import os
import sys

import cv2
import numpy as np

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from backend.pose.config import DetectionConfig  # noqa: E402
from backend.pose.detector import FallDetector, extract_features  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
B_VIDEO = os.path.join(ROOT, "tmp", "fall-verify2", "input.mp4")


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
    from ultralytics import YOLO

    cfg0 = DetectionConfig()
    model = YOLO(cfg0.model_path)

    for stride, angle_thr in ((1, 52.0), (2, 52.0), (3, 52.0)):
        cfg = DetectionConfig()
        cfg.frame_stride = stride
        cfg.torso_angle_threshold = angle_thr
        det = FallDetector(cfg)

        cap = cv2.VideoCapture(B_VIDEO)
        fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        print(f"\n=== stride={stride}  角度阈值={angle_thr:.0f} ===")
        print(f"{'idx':>5}{'t':>7}{'angle':>7}{'lying':>7}{'desc':>8}"
              f"{'fall':>6}{'state':>12}  reason")
        idx = 0
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            if idx % stride != 0:
                idx += 1
                continue
            h, w = frame.shape[:2]
            if w > cfg.resize_width:
                s = cfg.resize_width / w
                frame = cv2.resize(frame, (int(w * s), int(h * s)))
                h, w = frame.shape[:2]
            res = model.predict(frame, imgsz=cfg.imgsz, conf=cfg.conf,
                                device=cfg.device, verbose=False)
            f = extract_features(pick_primary(res[0]), width=w, height=h,
                                 index=idx, timestamp=idx / fps, cfg=cfg)
            r = det.feed(f)
            if 3.2 <= idx / fps <= 4.4:
                print(f"{idx:>5}{idx/fps:>7.2f}{f.torso_angle:>7.1f}"
                      f"{int(f.lying):>7}{f.descent_speed:>8.2f}"
                      f"{int(f.falling):>6}{r.state.value:>12}  {r.reason}")
            idx += 1
        cap.release()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
