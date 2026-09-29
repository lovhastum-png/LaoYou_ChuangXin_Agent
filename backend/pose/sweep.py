"""阈值扫描：在 URFD 全量上找出更稳的「横卧确认」参数。

用法：
    backend/pose/.venv/Scripts/python.exe -X utf8 -m backend.pose.sweep

只扫两个参数（避免过拟合）：
  - torso_angle_threshold：多少度算横卧
  - lying_confirm_frames ：连续多少帧才确认

评估口径统一用「按唯一场景」（fall 双机位合并），
因为这才是真实部署形态（一个房间一台摄像头）。

换机位/换数据集后重标定时用这个，不要手调阈值。
"""
from __future__ import annotations

import glob
import os
import sys

import cv2
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from backend.pose.config import DEFAULT_DETECTION, DEFAULT_PRESENCE, DetectionConfig  # noqa: E402
from backend.pose.detector import FallDetector, extract_features  # noqa: E402
from backend.pose.presence_run import _load_model, _pick_for_fall  # noqa: E402

model = _load_model(DEFAULT_PRESENCE)
pcfg = DEFAULT_PRESENCE

# 预先把每段视频的「逐帧特征」算一次并缓存，避免每个参数组合都重跑推理。
CACHE = os.path.join(ROOT, "tmp", "_feat_cache")
os.makedirs(CACHE, exist_ok=True)


def features_for(path: str):
    name = os.path.basename(path)
    cf = os.path.join(CACHE, name + ".npz")
    if os.path.exists(cf):
        z = np.load(cf)
        return z["kpts"], z["wh"], z["t"]

    cap = cv2.VideoCapture(path)
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    kpts_list, ts = [], []
    wh = None
    idx = 0
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        h0, w0 = frame.shape[:2]
        if w0 > pcfg.resize_width:
            s = pcfg.resize_width / w0
            frame = cv2.resize(frame, (int(w0 * s), int(h0 * s)))
        h, w = frame.shape[:2]
        wh = (w, h)
        res = model.predict(frame, imgsz=pcfg.imgsz, conf=pcfg.conf,
                            device=pcfg.device, verbose=False)[0]
        kp = _pick_for_fall(res, pcfg)
        if kp is None:
            kpts_list.append(np.zeros((17, 3), dtype=np.float32))
        else:
            kpts_list.append(np.asarray(kp, dtype=np.float32))
        ts.append(idx / fps)
        idx += 1
    cap.release()
    K = np.stack(kpts_list)
    T = np.asarray(ts, dtype=np.float32)
    np.savez_compressed(cf, kpts=K, wh=np.asarray(wh, dtype=np.int32), t=T)
    return K, np.asarray(wh, dtype=np.int32), T


def evaluate(angle_thr: float, confirm_frames: int, cache: dict):
    det_cfg = DetectionConfig()
    det_cfg.torso_angle_threshold = angle_thr
    det_cfg.lying_confirm_frames = confirm_frames

    scene_hit: dict[str, bool] = {}
    per_clip: dict[str, bool] = {}
    for name, (K, wh, T) in cache.items():
        det = FallDetector(det_cfg)
        w, h = int(wh[0]), int(wh[1])
        hit = False
        for i in range(len(K)):
            feat = extract_features(K[i], width=w, height=h, index=i,
                                    timestamp=float(T[i]), cfg=det_cfg)
            r = det.feed(feat)
            if r.triggered:
                hit = True
                break
        per_clip[name] = hit
        scene = name[:-4]
        if scene.endswith("-cam0") or scene.endswith("-cam1"):
            scene = scene[:-5]
        truth = name.startswith("fall-")
        if truth:
            scene_hit[scene] = scene_hit.get(scene, False) or hit
        else:
            scene_hit[scene] = scene_hit.get(scene, False) or hit

    stp = sfp = sfn = stn = 0
    for scene, hit in scene_hit.items():
        if scene.startswith("fall-"):
            stp += hit
            sfn += (not hit)
        else:
            sfp += hit
            stn += (not hit)
    rec = stp / (stp + sfn) if (stp + sfn) else 0.0
    prec = stp / (stp + sfp) if (stp + sfp) else 0.0
    f1 = 2 * prec * rec / (prec + rec) if (prec + rec) else 0.0
    acc = (stp + stn) / max(stp + sfp + sfn + stn, 1)
    return {"angle": angle_thr, "cf": confirm_frames,
            "TP": stp, "FP": sfp, "FN": sfn, "TN": stn,
            "recall": rec, "precision": prec, "f1": f1, "acc": acc}


def main():
    files = sorted(glob.glob(f"{ROOT}/tmp/urfd/*.mp4"))
    print(f"缓存 {len(files)} 段的逐帧特征（只算一次）…")
    cache = {}
    for i, p in enumerate(files, 1):
        if i % 20 == 0:
            print(f"  {i}/{len(files)}", flush=True)
        cache[os.path.basename(p)] = features_for(p)
    print(f"  完成\n")

    # 基线
    base = evaluate(62.0, 3, cache)
    print(f"基线  angle=62   cf=3   → 召回={base['recall']:.3f} "
          f"精确={base['precision']:.3f} F1={base['f1']:.3f}")
    print(f"\n{'angle':>6} {'cf':>4} {'TP':>4} {'FP':>4} {'FN':>4} {'TN':>4} "
          f"{'召回':>7} {'精确':>7} {'F1':>7}")
    print("-" * 62)
    results = []
    for angle in (62.0, 68.0, 72.0, 75.0, 78.0, 80.0, 82.0, 85.0):
        for cf in (3, 5, 8, 12):
            r = evaluate(angle, cf, cache)
            results.append(r)
            print(f"{angle:>6.0f} {cf:>4} {r['TP']:>4} {r['FP']:>4} {r['FN']:>4} "
                  f"{r['TN']:>4} {r['recall']:>7.3f} {r['precision']:>7.3f} "
                  f"{r['f1']:>7.3f}")

    best = max(results, key=lambda r: r["f1"])
    print(f"\n最佳 F1：angle={best['angle']:.0f} cf={best['cf']} "
          f"→ 召回={best['recall']:.3f} 精确={best['precision']:.3f} F1={best['f1']:.3f}")
    # 也报召回优先的选择
    hi_rec = [r for r in results if r["recall"] >= 0.70]
    if hi_rec:
        b2 = max(hi_rec, key=lambda r: r["precision"])
        print(f"召回>=0.70 下精确最高：angle={b2['angle']:.0f} cf={b2['cf']} "
              f"→ 召回={b2['recall']:.3f} 精确={b2['precision']:.3f} F1={b2['f1']:.3f}")


if __name__ == "__main__":
    main()
