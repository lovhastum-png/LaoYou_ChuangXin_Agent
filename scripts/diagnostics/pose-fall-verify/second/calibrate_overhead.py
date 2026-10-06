"""吊装机位标定搜索。

目标：找到一组参数，能稳定检出素材 B（房间吊装机位实录），
同时把在 URFD 上的精度损失压到最小。

评估集：
  · URFD 全量 100 分卷 / 70 唯一场景（从已有缓存重放，零推理成本）
  · 素材 A（走廊平视）、素材 B（房间吊装）—— 两段必须都检出

搜索维度：torso_angle_threshold × lying_confirm_frames × immobility_seconds
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
AB_CACHE = os.path.join(HERE, "ab-features.pkl")

CLIPS = [
    ("A-走廊平视", os.path.join(ROOT, "tmp", "fall-verify", "input.mp4")),
    ("B-房间吊装", os.path.join(ROOT, "tmp", "fall-verify2", "input.mp4")),
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


def build_ab_cache():
    from ultralytics import YOLO

    cfg = DetectionConfig()
    model = YOLO(cfg.model_path)
    out = {}
    for name, path in CLIPS:
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
        out[name] = rows
        print(f"  {name}: {len(rows)} 帧", flush=True)
    with open(AB_CACHE, "wb") as fh:
        pickle.dump(out, fh)
    return out


def replay(cfg, rows):
    """返回 (告警次数, 首次告警时刻)。"""
    det = FallDetector(cfg)
    n = 0
    first = None
    for idx, ts, present, angle, vr, nose, hip in rows:
        f = FrameFeatures(index=idx, timestamp=ts, present=bool(present),
                          torso_angle=angle, vertical_ratio=vr,
                          nose_y=nose, hip_y=hip)
        r = det.feed(f)
        if r.triggered:
            n += 1
            if first is None:
                first = round(ts, 2)
    return n, first


def scene_key(name: str) -> str:
    return name.replace("-cam0.mp4", "").replace("-cam1.mp4", "")


def main() -> int:
    if os.path.exists(AB_CACHE):
        ab = pickle.load(open(AB_CACHE, "rb"))
    else:
        print("缓存 A/B 素材特征…")
        ab = build_ab_cache()

    urfd = pickle.load(open(URFD_CACHE, "rb"))
    scenes = {}
    for name, rows in urfd.items():
        k = scene_key(name)
        scenes.setdefault(k, {"truth": "fall" if k.startswith("fall-") else "adl",
                              "clips": []})["clips"].append(rows)

    def urfd_metrics(cfg):
        tp = fp = fn = tn = 0
        for k, s in scenes.items():
            got = any(replay(cfg, c)[0] > 0 for c in s["clips"])
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
        return tp, fp, fn, tn, r, p, f1

    print("\n=== 搜索：必须同时检出 A 与 B，再看 URFD 代价 ===")
    print(f"{'角度':>5}{'确认帧':>7}{'静止秒':>7}   {'A':<10}{'B':<10}"
          f"{'URFD召回':>10}{'URFD精确':>10}{'URFD F1':>9}{'FP':>5}")

    rows_out = []
    for ang in (62.0, 58.0, 55.0, 52.0, 50.0, 48.0, 46.0, 44.0):
        for conf in (5, 4, 3):
            for immob in (0.0, 0.4, 0.8):
                cfg = DetectionConfig()
                cfg.torso_angle_threshold = ang
                cfg.lying_confirm_frames = conf
                cfg.immobility_seconds = immob

                a_n, a_t = replay(cfg, ab["A-走廊平视"])
                b_n, b_t = replay(cfg, ab["B-房间吊装"])
                ok = (a_n > 0) and (b_n > 0)
                tp, fp, fn, tn, r, p, f1 = urfd_metrics(cfg)

                rows_out.append((ok, ang, conf, immob, a_n, a_t, b_n, b_t,
                                 r, p, f1, fp))
                flag = "★" if ok else " "
                print(f"{ang:>5.0f}{conf:>7}{immob:>7.1f} {flag}"
                      f"{('✓' + str(a_t)) if a_n else '✗':<10}"
                      f"{('✓' + str(b_t)) if b_n else '✗':<10}"
                      f"{r:>10.3f}{p:>10.3f}{f1:>9.3f}{fp:>5}")

    ok_rows = [r_ for r_ in rows_out if r_[0]]
    if not ok_rows:
        print("\n没有同时检出 A 与 B 的配置。")
        return 1

    ok_rows.sort(key=lambda x: -x[10])
    print(f"\n=== 同时检出 A/B 的配置共 {len(ok_rows)} 组，按 URFD F1 排序前 8 ===")
    print(f"{'角度':>5}{'确认帧':>7}{'静止秒':>7}{'URFD召回':>10}{'URFD精确':>10}"
          f"{'URFD F1':>9}{'FP':>5}{'A 时刻':>9}{'B 时刻':>9}")
    for r_ in ok_rows[:8]:
        _, ang, conf, immob, a_n, a_t, b_n, b_t, r, p, f1, fp = r_
        print(f"{ang:>5.0f}{conf:>7}{immob:>7.1f}{r:>10.3f}{p:>10.3f}"
              f"{f1:>9.3f}{fp:>5}{str(a_t):>9}{str(b_t):>9}")

    print("\n=== 基线对照 ===")
    base = DetectionConfig()
    tp, fp, fn, tn, r, p, f1 = urfd_metrics(base)
    print(f"  基线 62°/5帧/0s：URFD 召回={r:.3f} 精确={p:.3f} F1={f1:.3f} FP={fp}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
