"""分析：vertical_ratio 能否作为与机位无关的「横卧」判据？

思路：B 素材里躺下时 vr 从 0.22 塌到 0.08（2.7×），且 vr 衡量的是
「躯干在图像里被压扁了多少」，不依赖人体躺向。如果 URFD 的日常动作
（弯腰、坐下、床上躺下）在同样角度区间里 vr 明显更大，就能用它把误报挡掉。

输出：角度落在 35~62°（灰区）时的 vr 分布，分「真跌倒躺下」与「日常动作」两组。
"""

from __future__ import annotations

import os
import pickle
import sys

import numpy as np

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

HERE = os.path.dirname(os.path.abspath(__file__))
URFD_CACHE = os.path.join(HERE, "urfd-features.pkl")
AB_CACHE = os.path.join(HERE, "ab-features.pkl")

LO, HI = 35.0, 62.0


def scene_key(name: str) -> str:
    return name.replace("-cam0.mp4", "").replace("-cam1.mp4", "")


def pct(a):
    if len(a) == 0:
        return "     —"
    return (f"p10={np.percentile(a,10):.3f} p50={np.percentile(a,50):.3f} "
            f"p90={np.percentile(a,90):.3f}")


def main() -> int:
    urfd = pickle.load(open(URFD_CACHE, "rb"))
    ab = pickle.load(open(AB_CACHE, "rb"))

    groups = {"URFD 真跌倒(fall-*)": [], "URFD 日常(adl-*)": []}
    for name, rows in urfd.items():
        k = scene_key(name)
        key = "URFD 真跌倒(fall-*)" if k.startswith("fall-") else "URFD 日常(adl-*)"
        for idx, ts, present, angle, vr, nose, hip in rows:
            if present and LO <= angle <= HI:
                groups[key].append(vr)
    # B 素材：只取躺下之后（>3.4s）的灰区帧
    b_lie = []
    for idx, ts, present, angle, vr, nose, hip in ab["B-房间吊装"]:
        if present and ts > 3.4 and LO <= angle <= HI:
            b_lie.append(vr)

    print(f"角度落在 {LO:.0f}~{HI:.0f}°（灰区）时的 vr 分布\n")
    print(f"{'分组':<24}{'帧数':>6}   vr 分布")
    print("-" * 74)
    for k, v in groups.items():
        arr = np.array(v)
        print(f"{k:<24}{len(arr):>6}   {pct(arr)}")
    arr = np.array(b_lie)
    print(f"{'B-房间吊装(躺下后)':<24}{len(arr):>6}   {pct(arr)}")

    # 如果阈值放在 vr<=X，各种 X 下各组还剩多少帧
    print("\n=== 若要求 vr <= X 才算横卧，灰区还剩多少帧 ===")
    print(f"{'X':>6}{'fall-*保留':>12}{'adl-*保留':>12}{'B 保留':>10}")
    for x in (0.30, 0.25, 0.20, 0.16, 0.14, 0.12, 0.10, 0.09, 0.08):
        f = int((np.array(groups["URFD 真跌倒(fall-*)"]) <= x).sum())
        a = int((np.array(groups["URFD 日常(adl-*)"]) <= x).sum())
        b = int((arr <= x).sum())
        print(f"{x:>6.2f}{f:>12}{a:>12}{b:>10}")

    print("\n判读：若某一档 X 下 adl 剩下极少而 B 保留很多，vr 判据就可用；"
          "\n      若两者同比缩放，则 vr 对该机位没有额外区分度。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
