"""总览图：两段真机位素材的失效模式对照。

同一套判据，两种机位以相反的方式失效：
  · 素材 A（走廊平视跟拍）：横卧角度根本没来得及出现（人已出画）
  · 素材 B（房间吊装高机位）：横卧角度出现了，但被机位仰角压到 40-60°

输出 tmp/fall-verify2/overview.png
"""

from __future__ import annotations

import csv
import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib import font_manager  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
CN_FONT = "C:/Windows/Fonts/msyh.ttc"
OUT = os.path.join(HERE, "overview.png")

font_manager.fontManager.addfont(CN_FONT)
plt.rcParams["font.family"] = "Microsoft YaHei"
plt.rcParams["axes.unicode_minus"] = False


def load(path):
    rows = list(csv.DictReader(open(path, encoding="utf-8")))
    return {
        "t": [float(r["t"]) for r in rows],
        "angle": [float(r["torso_angle"]) for r in rows],
        "present": [int(r["present"]) for r in rows],
    }


A = load(os.path.join(ROOT, "tmp", "fall-verify", "trace.csv"))
B = load(os.path.join(ROOT, "tmp", "fall-verify2", "trace.csv"))

fig, axes = plt.subplots(1, 2, figsize=(13.6, 5.2), sharey=True)

for ax, d, title, note in (
    (axes[0], A, "素材 A · 走廊平视跟拍（1280×720）",
     "人朝镜头扑倒，2.97s 沉出画面下沿\n躯干角峰值仅 21.1° —— 横卧判据从未成立\n→ 需要「坠落并沉出画面」判据"),
    (axes[1], B, "素材 B · 房间吊装高机位（720×1280）",
     "人躺在地上但机位仰角把躯干压平\n躯干角只到 63.1°，且仅 1 帧越过 62°\n→ 需要把横卧阈值降到 50° 左右"),
):
    ax.plot(d["t"], d["angle"], color="#1f4e79", lw=2.0, label="躯干角（度）")
    ax.axhline(62, color="#c0392b", ls="--", lw=1.8, label="横卧阈值 62°（基线）")
    ax.grid(alpha=0.25, linestyle=":")

    # 人不在画面内的区间涂灰
    inside = []
    start = None
    for i, p in enumerate(d["present"]):
        if p == 0 and start is None:
            start = d["t"][i]
        elif p == 1 and start is not None:
            inside.append((start, d["t"][i]))
            start = None
    if start is not None:
        inside.append((start, d["t"][-1]))
    for s, e in inside:
        if e - s > 0.3:
            ax.axvspan(s, e, color="#bbbbbb", alpha=0.4, lw=0)

    ax.set_title(title, fontsize=12.5, pad=10)
    ax.set_xlabel("时间（秒）", fontsize=11)
    ax.set_ylim(-5, 100)
    ax.legend(loc="upper left", fontsize=9.5)
    ax.text(0.98, 0.05, note, transform=ax.transAxes, ha="right", va="bottom",
            fontsize=9.5, color="#8a2f2f",
            bbox=dict(boxstyle="round,pad=0.45", fc="#fdf3f3", ec="#e0b4b4"))

axes[0].set_ylabel("躯干偏离竖直轴的夹角（度）", fontsize=11)

fig.suptitle("同一套判据，两种机位、两种相反的失效模式", fontsize=14.5, y=0.99)
fig.text(0.5, 0.012,
         "灰色区间 = 画面内检不到人体的时段。素材 A 的关键姿态发生在出画之后；"
         "素材 B 的人始终在画面内，但躺下时的躯干投射角够不到 62°。",
         ha="center", fontsize=10.2, color="#333333")
fig.tight_layout(rect=(0, 0.05, 1, 0.955))
fig.savefig(OUT, dpi=150)
print(f"已写出 {OUT}")
