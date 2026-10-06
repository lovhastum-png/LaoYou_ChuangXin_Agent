"""生成跌倒验证的可视化交付物。

产物：
  1. detect-annotated.mp4 —— 逐帧骨架 + 状态叠加，告警瞬间红框高亮
  2. feature-curves.png  —— 下降速度 / 躯干角 随时间变化，标出两条阈值红线
                            与「人出画」时刻，说明基线为什么漏、新判据为什么能捞回
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
OUT_MP4 = os.path.join(HERE, "detect-annotated.mp4")
OUT_PNG = os.path.join(HERE, "feature-curves.png")

CN_FONT = "C:/Windows/Fonts/msyh.ttc"


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


def render_video(rows_by_idx, exit_fall: bool) -> None:
    from PIL import Image, ImageDraw, ImageFont
    from ultralytics import YOLO

    cfg = DetectionConfig()
    cfg.detect_frame_exit_fall = exit_fall
    model = YOLO(cfg.model_path)
    font = ImageFont.truetype(CN_FONT, 20)
    font_s = ImageFont.truetype(CN_FONT, 16)
    font_xl = ImageFont.truetype(CN_FONT, 30)

    cap = cv2.VideoCapture(VIDEO)
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    ok, frame0 = cap.read()
    cap.release()
    h0, w0 = frame0.shape[:2]
    scale = cfg.resize_width / w0
    W, H = int(w0 * scale), int(h0 * scale)
    vw = cv2.VideoWriter(OUT_MP4, cv2.VideoWriter_fourcc(*"mp4v"), fps, (W, H))

    cap = cv2.VideoCapture(VIDEO)
    idx = 0
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        frame = cv2.resize(frame, (W, H))
        res = model.predict(frame, imgsz=cfg.imgsz, conf=cfg.conf,
                            device=cfg.device, verbose=False)
        plotted = res[0].plot(labels=False, boxes=False, line_width=2)

        row = rows_by_idx.get(idx, {})
        alarmed = bool(row.get("alarm_now"))
        state = row.get("state", "-")
        angle_v = float(row.get("torso_angle", 0.0))
        v = float(row.get("descent", 0.0))

        img = Image.fromarray(cv2.cvtColor(plotted, cv2.COLOR_BGR2RGB))
        dr = ImageDraw.Draw(img, "RGBA")

        # 左上信息面板
        panel = (12, 12, 330, 148)
        dr.rectangle(panel, fill=(12, 18, 28, 200))
        dr.rectangle(panel, outline=(90, 110, 140, 255), width=1)
        dr.text((26, 20), f"时间 {idx/fps:5.2f}s   帧 {idx:03d}",
                font=font_s, fill=(180, 200, 220))
        dr.text((26, 46), f"状态  {state}", font=font, fill=(255, 255, 255))
        dr.text((26, 76),
                f"躯干角 {angle_v:5.1f}°  (阈值 62°)",
                font=font_s, fill=(200, 220, 255))
        col = (255, 120, 120) if v >= cfg.descent_speed_threshold else (170, 190, 210)
        dr.text((26, 100),
                f"下降速度 {v:5.2f} 画面高/秒 (阈值 0.8)",
                font=font_s, fill=col)
        # 坠落条
        filled = int(np.clip(v / 1.2, 0, 1) * 250)
        dr.rectangle((26, 124, 276, 136), fill=(40, 50, 62, 255))
        bar_col = (230, 70, 70, 255) if v >= cfg.descent_speed_threshold \
            else (90, 150, 220, 255)
        dr.rectangle((26, 124, 26 + filled, 136), fill=bar_col)

        # 告警横幅
        if alarmed:
            dr.rectangle((0, H // 2 - 44, W, H // 2 + 44), fill=(190, 30, 30, 215))
            txt = "检测到跌倒  —  已触发告警"
            tw = dr.textlength(txt, font=font_xl)
            dr.text(((W - tw) / 2, H // 2 - 20), txt, font=font_xl,
                    fill=(255, 245, 245))
        elif int(row.get("present", 0)) == 0 and idx > 90:
            dr.text((16, H - 40), "老人已不在画面内（约 3.0s 起沉出画面下沿）",
                    font=font_s, fill=(255, 200, 120))

        out = cv2.cvtColor(np.array(img), cv2.COLOR_RGB2BGR)
        vw.write(out)
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
    desc = [float(r["descent"]) for r in rows]
    present = [int(r["present"]) for r in rows]

    # 人出画的时刻 = 最后一个 present=1 的帧
    last_present_t = max(float(r["t"]) for r in rows if int(r["present"]) == 1)
    # 告警时刻（新判据）
    alarm_t = None
    for r in rows:
        if int(r["alarm"]) == 1:
            alarm_t = float(r["t"])
            break

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(11.5, 7.6), sharex=True,
                                   gridspec_kw={"height_ratios": [1, 1]})

    # 出画后的区间涂灰
    for ax in (ax1, ax2):
        ax.axvspan(last_present_t, max(t), color="#cccccc", alpha=0.45, lw=0)
        ax.grid(alpha=0.25, linestyle=":")

    # 上图：躯干角
    ax1.plot(t, angle, color="#1f4e79", lw=2.2, label="躯干角（度）")
    ax1.axhline(62, color="#c0392b", ls="--", lw=1.8,
                label="横卧判据阈值 62°")
    ax1.set_ylabel("躯干偏离竖直轴的夹角（度）", fontsize=11)
    ax1.set_ylim(-5, 95)
    peak_angle = max(angle)
    ax1.annotate(f"躯干角全程最大仅 {peak_angle:.0f}°\n"
                 f"（远达不到 62°，「横卧」判据永不成立）",
                 xy=(2.97, peak_angle), xytext=(1.05, 78),
                 fontsize=10.5, color="#c0392b",
                 arrowprops=dict(arrowstyle="->", color="#c0392b", lw=1.4))
    ax1.legend(loc="upper left", fontsize=10)
    ax1.set_title("跌倒识别验证：1280×720 走廊平视跟拍 · 站立→前扑→沉出画面下沿",
                  fontsize=13, pad=12)

    # 下图：下降速度
    ax2.plot(t, desc, color="#7d3c98", lw=2.2, label="鼻部下降速度（画面高/秒）")
    ax2.axhline(0.8, color="#c0392b", ls="--", lw=1.8,
                label="坠落判据阈值 0.8")
    peak_desc = max(desc)
    ax2.annotate(f"峰值 {peak_desc:.2f}\n连续 5 帧越过阈值 → 坠落信号成立",
                 xy=(2.97, peak_desc), xytext=(1.05, 0.78),
                 fontsize=10.5, color="#7d3c98",
                 arrowprops=dict(arrowstyle="->", color="#7d3c98", lw=1.4))
    ax2.set_ylabel("下降速度（画面高/秒）", fontsize=11)
    ax2.set_xlabel("时间（秒）", fontsize=11)
    ax2.set_ylim(-0.25, 1.25)
    ax2.legend(loc="upper left", fontsize=10)

    # 竖线：出画 / 告警
    ax2.axvline(last_present_t, color="#e67e22", lw=2)
    ax2.text(last_present_t + 0.06, -0.20, f"人出画 {last_present_t:.2f}s",
             color="#b9600f", fontsize=10)
    if alarm_t:
        ax2.axvline(alarm_t, color="#27ae60", lw=2, ls="-.")
        ax2.text(alarm_t + 0.06, 0.55, f"新判据告警 {alarm_t:.2f}s",
                 color="#1e8449", fontsize=10)

    ax2.text((last_present_t + max(t)) / 2, 1.05, "躯干不可见区间",
             color="#666666", fontsize=10, ha="center")

    fig.text(0.012, 0.015,
             "基线结论：坠落信号成立，但「横卧」判据因人体沉出画面而永不成立 → "
             "漏报。新增「坠落并沉出画面」判据后，t=3.00s 正确告警。",
             fontsize=10.5, color="#333333")
    fig.tight_layout(rect=(0, 0.045, 1, 1))
    fig.savefig(OUT_PNG, dpi=150)
    print(f"  已写出 {OUT_PNG}")


def main() -> int:
    rows = list(csv.DictReader(open(os.path.join(HERE, "trace-fixed.csv"),
                                    encoding="utf-8")))
    for r in rows:
        r["alarm_now"] = r["alarm"] == "1"
    by_idx = {int(r["idx"]): r for r in rows}

    # 视频：开启新判据（会正确告警）
    render_video(by_idx, exit_fall=True)
    render_chart(rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
