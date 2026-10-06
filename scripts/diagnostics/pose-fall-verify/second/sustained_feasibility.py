"""可行性测量：用「持续低位（vr 小）时长」代替「瞬时坠落尖峰」。

动机：素材 B 的坠落信号是姿态估计抖动造成的单帧伪影，对丢帧不免疫。
而人躺在地上会**持续**保持 vr 很小（B 素材躺了 4.5 秒）。
「持续时长」天然抗丢帧 —— 少采几帧不影响还算得出 2 秒。

这里只做统计测量（不改检测器），回答两个问题：
  1. B 素材躺下时的持续低位时长，与站立时有多大差距？
  2. URFD 的日常动作里，是否也存在同样长的持续低位？（若大量存在，该判据无效）
"""

from __future__ import annotations

import os
import pickle
import sys

import numpy as np

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

HERE = os.path.dirname(os.path.abspath(__file__))
URFD_CACHE = os.path.join(HERE, "urfd-features.pkl")
B_CACHE = os.path.join(HERE, "b-strides.pkl")
VR_THR = 0.12


def scene_key(name: str) -> str:
    return name.replace("-cam0.mp4", "").replace("-cam1.mp4", "")


def longest_run(rows, thr):
    """返回最长连续 (vr<=thr 且 present) 的时长（秒）。"""
    best = cur = 0.0
    prev_t = None
    for idx, ts, present, angle, vr, nose, hip in rows:
        if present and vr <= thr:
            if prev_t is not None:
                cur += ts - prev_t
            cur = max(cur, 1e-3)
            best = max(best, cur)
        else:
            cur = 0.0
        prev_t = ts
    return best


def main() -> int:
    urfd = pickle.load(open(URFD_CACHE, "rb"))
    bmap = pickle.load(open(B_CACHE, "rb"))

    # URFD：按唯一场景，取两机位中较长者
    scenes = {}
    for name, rows in urfd.items():
        k = scene_key(name)
        scenes.setdefault(k, {"truth": "fall" if k.startswith("fall-") else "adl",
                              "clips": []})["clips"].append(rows)

    fall_runs, adl_runs = [], []
    for k, s in scenes.items():
        r = max(longest_run(c, VR_THR) for c in s["clips"])
        (fall_runs if s["truth"] == "fall" else adl_runs).append(r)

    print(f"阈值 vr <= {VR_THR}，「最长持续低位时长」（秒）\n")
    for label, arr in (("URFD 真跌倒(fall-*)", fall_runs), ("URFD 日常(adl-*)", adl_runs)):
        a = np.array(arr)
        print(f"{label:<22} n={len(a):>3}  "
              f"p10={np.percentile(a,10):.2f} p50={np.percentile(a,50):.2f} "
              f"p90={np.percentile(a,90):.2f} max={a.max():.2f}")

    b1 = longest_run(bmap[1], VR_THR)
    b2 = longest_run(bmap[2], VR_THR)
    b3 = longest_run(bmap[3], VR_THR)
    print(f"{'B-房间吊装 (stride1)':<22} n=  1  最长持续 {b1:.2f}s")
    print(f"{'B-房间吊装 (stride2)':<22} n=  1  最长持续 {b2:.2f}s")
    print(f"{'B-房间吊装 (stride3)':<22} n=  1  最长持续 {b3:.2f}s")

    print("\n=== 若要求「持续低位 >= X 秒」，URFD 日常还剩多少场景会被判为低位 ===")
    print(f"{'X(秒)':>7}{'fall 命中':>11}{'adl 命中':>11}{'B(stride1/2/3)':>18}")
    a_arr, f_arr = np.array(adl_runs), np.array(fall_runs)
    for x in (0.5, 1.0, 1.5, 2.0, 2.5, 3.0):
        print(f"{x:>7.1f}{int((f_arr>=x).sum()):>11}{int((a_arr>=x).sum()):>11}"
              f"{f'{b1>=x}/{b2>=x}/{b3>=x}':>18}")

    print("\n判读：需要「adl 命中」接近 0 而 B 三档全为 True，该判据才有价值。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
