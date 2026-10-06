"""鲁棒标定搜索：要求素材 B 在 stride=1/2/3 下都能检出。

上一轮搜索只要求 stride=1 检出，得到的配置在丢帧下全废。
根因是素材 B 的坠落信号是单帧尖峰，隔帧采样把下降速度稀释到阈值以下。

因此把 descent_speed_threshold 与 angular_velocity_threshold 也纳入搜索，
硬性要求三种采样率下都能检出 B，再看 URFD 全量代价。
"""

from __future__ import annotations

import os
import pickle
import sys

import cv2
import numpy as np

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from backend.pose.config import DetectionConfig  # noqa: E402
from backend.pose.detector import FallDetector, FrameFeatures, extract_features  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
URFD_CACHE = os.path.join(HERE, "urfd-features.pkl")
B_CACHE = os.path.join(HERE, "b-strides.pkl")
B_VIDEO = os.path.join(ROOT, "tmp", "fall-verify2", "input.mp4")
STRIDES = (1, 2, 3)


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


def extract(stride: int):
    from ultralytics import YOLO

    cfg = DetectionConfig()
    cfg.frame_stride = stride
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


def scene_key(name: str) -> str:
    return name.replace("-cam0.mp4", "").replace("-cam1.mp4", "")


def main() -> int:
    if os.path.exists(B_CACHE):
        bmap = pickle.load(open(B_CACHE, "rb"))
    else:
        print("缓存 B 素材 (stride 1/2/3)…", flush=True)
        bmap = {s: extract(s) for s in STRIDES}
        pickle.dump(bmap, open(B_CACHE, "wb"))

    urfd = pickle.load(open(URFD_CACHE, "rb"))
    scenes = {}
    for name, rows in urfd.items():
        k = scene_key(name)
        scenes.setdefault(k, {"truth": "fall" if k.startswith("fall-") else "adl",
                              "clips": []})["clips"].append(rows)

    def urfd_metrics(cfg):
        tp = fp = fn = 0
        for k, s in scenes.items():
            got = any(replay(cfg, c) for c in s["clips"])
            if s["truth"] == "fall" and got:
                tp += 1
            elif s["truth"] == "fall":
                fn += 1
            elif got:
                fp += 1
        r = tp / (tp + fn) if (tp + fn) else 0.0
        p = tp / (tp + fp) if (tp + fp) else 0.0
        f1 = 2 * r * p / (r + p) if (r + p) else 0.0
        return r, p, f1, fp

    print("\n=== 要求 B 在 stride 1/2/3 全部检出 ===")
    print(f"{'角度':>5}{'确认帧':>7}{'下降阈':>8}{'角速阈':>8}"
          f"{'B@1':>8}{'B@2':>8}{'B@3':>8}{'URFD召回':>10}{'精确':>8}{'F1':>8}{'FP':>5}")

    ok_rows = []
    for ang in (62.0, 58.0, 55.0, 52.0, 50.0):
        for conf in (5, 4, 3):
            for dth in (0.8, 0.6, 0.45):
                for ath in (600.0, 400.0):
                    cfg = DetectionConfig()
                    cfg.torso_angle_threshold = ang
                    cfg.lying_confirm_frames = conf
                    cfg.descent_speed_threshold = dth
                    cfg.angular_velocity_threshold = ath
                    res = [replay(cfg, bmap[s]) for s in STRIDES]
                    robust = all(x for x in res)
                    r, p, f1, fp = urfd_metrics(cfg)
                    if robust:
                        ok_rows.append((ang, conf, dth, ath, res, r, p, f1, fp))
                        print(f"{ang:>5.0f}{conf:>7}{dth:>8.2f}{ath:>8.0f}"
                              f"{str(res[0]):>8}{str(res[1]):>8}{str(res[2]):>8}"
                              f"{r:>10.3f}{p:>8.3f}{f1:>8.3f}{fp:>5}")

    print(f"\n  鲁棒配置共 {len(ok_rows)} 组")
    if ok_rows:
        ok_rows.sort(key=lambda x: -x[7])
        print("\n=== 按 URFD F1 排序前 10 ===")
        print(f"{'角度':>5}{'确认帧':>7}{'下降阈':>8}{'角速阈':>8}"
              f"{'URFD召回':>10}{'精确':>8}{'F1':>8}{'FP':>5}  B 触发时刻")
        for ang, conf, dth, ath, res, r, p, f1, fp in ok_rows[:10]:
            print(f"{ang:>5.0f}{conf:>7}{dth:>8.2f}{ath:>8.0f}"
                  f"{r:>10.3f}{p:>8.3f}{f1:>8.3f}{fp:>5}  {res}")

    base = DetectionConfig()
    r, p, f1, fp = urfd_metrics(base)
    print(f"\n  基线对照 62°/5帧/0.8/600：召回={r:.3f} 精确={p:.3f} F1={f1:.3f} FP={fp}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
