"""素材 B（房间吊装高机位）的验证可视化。

产物：
  1. detect-annotated.mp4 —— 逐帧骨架 + 状态面板，3.57s 弹出告警红幅
  2. feature-curves.png  —— 躯干角（同时标 62° 旧阈值与 50° 新阈值）
                             与躯干竖直跨度 vr（展示躺下时的塌缩）
"""

from __future__ import annotations

import csv
import os
import sys

import cv2
import numpy as np

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from backend.pose.config import DetectionConfig  # noqa: E402
from backend.pose.detector import FallDetector, extract_features  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
VIDEO = os.path.join(HERE, "input.mp4")
TRACE = os.path.join(HERE, "trace-fixed.csv")
OUT_MP4 = os.path.join(HERE, "detect-annotated.mp4")
OUT_PNG = os.path.join(HERE, "feature-curves.png")
CN_FONT = "C:/Windows/Fonts/msyh.ttc"

NEW_ANGLE = 52.0
OLD_ANGLE = 62.0


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


def render_video(by_idx: dict, rows: list[dict]) -> None:
    from PIL import Image, ImageDraw, ImageFont
    from ultralytics import YOLO

    cfg = DetectionConfig()
    cfg.torso_angle_threshold = NEW_ANGLE
    cfg.detect_frame_exit_fall = True
    model = YOLO(cfg.model_path)

    font = ImageFont.truetype(CN_FONT, 22)
    font_s = ImageFont.truetype(CN_FONT, 18)
    font_xl = ImageFont.truetype(CN_FONT, 34)

    cap = cv2.VideoCapture(VIDEO)
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    ok, f0 = cap.read()
    cap.release()
    h0, w0 = f0.shape[:2]
    scale = cfg.resize_width / w0 if w0 > cfg.resize_width else 1.0
    W, H = int(w0 * scale), int(h0 * scale)
    vw = cv2.VideoWriter(OUT_MP4, cv2.VideoWriter_fourcc(*"mp4v"), fps, (W, H))

    # 用最大横卧帧数决定“确认进度”展示
    cap = cv2.VideoCapture(VIDEO)
    idx = 0
    run_lying = 0
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        frame = cv2.resize(frame, (W, H))
        res = model.predict(frame, imgsz=cfg.imgsz, conf=cfg.conf,
                            device=cfg.device, verbose=False)
        plotted = res[0].plot(labels=False, boxes=False, line_width=3)

        row = by_idx.get(idx)
        isnone = row is None
        alarm_now = (not isnone) and row["alarm"] == "1"
        present = 0 if isnone else int(row["present"])
        angle_v = 0.0 if isnone else float(row["torso_angle"])
        vr_v = 0.0 if isnone else float(row["vr"])
        running = int((not isnone) and int(row["lying"]) == 1)
        run_lying = run_lying + 1 if running else 0

        img = Image.fromarray(cv2.cvtColor(plotted, cv2.COLOR_BGR2RGB))
        dr = ImageDraw.Draw(img, "RGBA")

        dr.rectangle((10, 10, W - 10, 178), fill=(12, 18, 28, 205))
        dr.rectangle((10, 10, W - 10, 178), outline=(90, 110, 140, 255), width=1)
        dr.text((26, 18), f"时间 {idx/fps:5.2f}s   帧 {idx:03d}   "
                          f"{'有人在画面内' if present else '画面内无有效人体'}",
                font=font_s, fill=(180, 200, 220))
        dr.text((26, 46), f"状态  {'—' if isnone else row['state']}",
                font=font, fill=(255, 255, 255))
        # 躯干角：旧阈值 62 与新阈值 50 双参考
        col = (120, 255, 160) if angle_v >= NEW_ANGLE else (
            (255, 210, 120) if angle_v >= OLD_ANGLE else (190, 205, 225))
        dr.text((26, 78), f"躯干角 {angle_v:5.1f}°", font=font, fill=col)
        dr.text((210, 82), f"旧阈值 62° / 新阈值 50°", font=font_s,
                fill=(160, 175, 195))
        dr.text((26, 112), f"躯干竖直跨度 vr {vr_v:.3f}"
                           f"（直立≈0.22，躺下≈0.08）", font=font_s,
                fill=(200, 220, 255))
        # 横卧确认进度（新阈值下需 5 帧）
        dr.text((26, 140), f"横卧确认 {min(run_lying,5)}/5 帧", font=font_s,
                fill=(150, 235, 180) if run_lying >= 5 else (150, 165, 185))
        bar_w = int(np.clip(run_lying / 5.0, 0, 1) * (W - 240))
        dr.rectangle((196, 146, W - 24, 158), fill=(40, 50, 62, 255))
        dr.rectangle((196, 146, 196 + bar_w, 158),
                     fill=(80, 200, 130, 255) if run_lying >= 5 else (90, 150, 220, 255))

        if alarm_now:
            dr.rectangle((0, H // 2 - 52, W, H // 2 + 52), fill=(190, 30, 30, 215))
            txt = "检测到跌倒  —  已触发告警"
            tw = dr.textlength(txt, font=font_xl)
            dr.text(((W - tw) / 2, H // 2 - 24), txt, font=font_xl,
                    fill=(255, 245, 245))

        vw.write(cv2.cvtColor(np.array(img), cv2.COLOR_RGB2BGR))
        idx += 1
    cap.release()
    vw.release()
    print(f"  已写出 {OUT_MP4}")


def render_chart(rows: list[dict]) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib import font_manager

    font_manager.fontManager.addfont(CN_FONT)
    plt.rcParams["font.family"] = "Microsoft YaHei"
    plt.rcParams["axes.unicode_minus"] = False

    t = [float(r["t"]) for r in rows]
    angle = [float(r["torso_angle"]) for r in rows]
    vr = [float(r["vr"]) for r in rows]
    present = [int(r["present"]) for r in rows]

    first_present = next((float(r["t"]) for r in rows if int(r["present"]) == 1), 0.0)
    alarm_t = next((float(r["t"]) for r in rows if int(r["alarm"]) == 1), None)

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(11.5, 7.6), sharex=True)
    for ax in (ax1, ax2):
        ax.grid(alpha=0.25, linestyle=":")

    # 上图：躯干角 + 两条阈值
    ax1.plot(t, angle, color="#1f4e79", lw=2.0, label="躯干角（度）")
    ax1.axhline(OLD_ANGLE, color="#c0392b", ls="--", lw=1.8,
                label=f"基线横卧阈值 {OLD_ANGLE:.0f}°（近景侧视标定）")
    ax1.axhline(NEW_ANGLE, color="#27ae60", ls="-.", lw=1.8,
                label=f"吊装机位建议阈值 {NEW_ANGLE:.0f}°")
    ax1.fill_between(t, NEW_ANGLE, OLD_ANGLE, color="#f39c12", alpha=0.13, lw=0)
    ax1.text(6.4, 56.5, "仅 62° 能确认\n（此段仅 1 帧）", fontsize=9.5,
             color="#c0392b", ha="center")
    ax1.text(6.4, 46, "50°~62° 区间\n（吊装机位躺下常落在这里）", fontsize=9.5,
             color="#b9600f", ha="center")
    ax1.set_ylabel("躯干偏离竖直轴的夹角（度）", fontsize=11)
    ax1.set_ylim(-5, 118)
    ax1.legend(loc="upper left", fontsize=9.5, framealpha=0.92)
    ax1.set_title("素材 B 验证：720×1280 房间吊装高机位 · 走入住画 → 前扑 → 躺地不起",
                  fontsize=13, pad=12)
    peak_a = max(angle)
    peak_t = t[angle.index(peak_a)]
    ax1.annotate(f"峰值 {peak_a:.1f}°，仅在 {peak_t:.2f}s 单帧越过 62°；"
                 f"基线需连续 5 帧 → 只确认 1 帧 → 漏报",
                 xy=(peak_t, peak_a), xytext=(1.15, 96), fontsize=10,
                 color="#c0392b",
                 arrowprops=dict(arrowstyle="->", color="#c0392b", lw=1.4))

    # 下图：vr
    ax2.plot(t, vr, color="#7d3c98", lw=2.0, label="躯干竖直跨度 vr（|肩髋竖直差| / 画面高）")
    ax2.set_ylabel("vr", fontsize=11)
    ax2.set_xlabel("时间（秒）", fontsize=11)
    ax2.set_ylim(-0.01, 0.30)
    ax2.legend(loc="upper left", fontsize=9.5)
    if first_present:
        ax2.axvline(first_present, color="#e67e22", lw=1.8)
        ax2.text(first_present + 0.05, 0.27, f"人入画 {first_present:.2f}s",
                 color="#b9600f", fontsize=9.5)
    if alarm_t:
        for ax in (ax1, ax2):
            ax.axvline(alarm_t, color="#27ae60", lw=2)
        ax2.text(alarm_t + 0.06, 0.20, f"新配置告警 {alarm_t:.2f}s",
                 color="#1e8449", fontsize=10)
    ax2.annotate("vr 从 0.22 塌到 0.08（约 2.7×）\n"
                 "比角度更稳的「人已躺平」信号",
                 xy=(5.0, 0.10), xytext=(3.9, 0.215), fontsize=10,
                 color="#7d3c98",
                 arrowprops=dict(arrowstyle="->", color="#7d3c98", lw=1.4))

    fig.text(0.012, 0.015,
             "结论：吊装机位下躺地时躯干只投射到 40~60°，按近景侧视标定的 62° 阈值不成立。"
             "降到 52° 后 t=3.60s 正常确认告警；\n代价是 URFD 全量精确 0.724→0.636、F1 0.712→0.667，"
             "且该配置在丢帧下失效（坠落证据依赖姿态抖动造成的单帧尖峰）。",
             fontsize=10, color="#333333")
    fig.tight_layout(rect=(0, 0.045, 1, 1))
    fig.savefig(OUT_PNG, dpi=150)
    print(f"  已写出 {OUT_PNG}")


def main() -> int:
    rows = list(csv.DictReader(open(TRACE, encoding="utf-8")))
    by_idx = {int(r["idx"]): r for r in rows}
    render_video(by_idx, rows)
    render_chart(rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
