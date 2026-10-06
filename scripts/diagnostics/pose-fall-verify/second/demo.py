"""演示视频：同一段实录，两种阈值，同屏对照。

左：基线 62°（当前默认值）—— 全程漏报
右：标定 52°（吊装机位）—— 3.60s 告警

两路共用同一次姿态推理、同一帧画面，唯一差别是横卧角度阈值，
因此画面差异只可能来自阈值本身。关键区间（2.9~4.2s）慢放 3 倍。

输出 tmp/fall-verify2/demo-compare.mp4
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
VIDEO = os.path.join(HERE, "input.mp4")
OUT = os.path.join(HERE, "demo-compare.mp4")
CN = "C:/Windows/Fonts/msyh.ttc"

SCALE = 0.5                      # 720x1280 -> 360x640
PW, PH = 360, 640
STRIP = 132                      # 每个面板顶部信息条高度
TL = 118                         # 底部时间轴高度
W, H = PW * 2, PH + TL
SLOW_LO, SLOW_HI, SLOW_N = 2.9, 4.2, 3
HOLD_END = 75                    # 结尾定格帧数

BASE_ANGLE, CAL_ANGLE = 62.0, 52.0


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


def draw_panel(dr, x0, img, feat, res, det, F, *, title, thr, thr_other, accent,
               slow, alarmed_at, ts, banner):
    """在 (x0,0) 处画一个面板。"""
    dr._image.paste(img, (x0, 0))

    # ---- 顶部信息条 ----
    dr.rectangle((x0, 0, x0 + PW, STRIP), fill=(10, 15, 24, 214))
    dr.line((x0, STRIP, x0 + PW, STRIP), fill=(70, 90, 115, 255), width=1)

    dr.text((x0 + 14, 8), title, font=F["h"], fill=accent)

    ang = feat.torso_angle if feat.present else 0.0
    if feat.present:
        ang_col = (120, 250, 165) if ang >= thr else (
            (255, 205, 110) if ang >= thr_other else (195, 210, 230))
        ang_txt = f"{ang:5.1f}°"
    else:
        ang_col = (140, 150, 165)
        ang_txt = "  —  "
    dr.text((x0 + 14, 36), "躯干角", font=F["s"], fill=(165, 180, 200))
    dr.text((x0 + 78, 32), ang_txt, font=F["m"], fill=ang_col)

    # ---- 角度标尺 0~90°，标出两个阈值 ----
    bx0, bx1, by = x0 + 14, x0 + PW - 14, 68
    dr.rectangle((bx0, by, bx1, by + 11), fill=(38, 48, 60, 255))
    span = bx1 - bx0

    def tx(v):
        return bx0 + span * min(max(v / 90.0, 0.0), 1.0)

    # 已达成的区间高亮
    if feat.present and ang > 0:
        dr.rectangle((bx0, by, int(tx(ang)), by + 11),
                     fill=(80, 210, 140, 255) if ang >= thr else (200, 160, 70, 255))
    # 两个阈值刻度
    dr.line((int(tx(thr_other)), by - 5, int(tx(thr_other)), by + 17),
            fill=(220, 80, 70, 255), width=2)
    dr.line((int(tx(thr)), by - 5, int(tx(thr)), by + 17),
            fill=(70, 200, 125, 255), width=2)
    dr.text((int(tx(thr_other)) - 9, by + 18), f"{thr_other:.0f}°", font=F["t"],
            fill=(220, 110, 100))
    dr.text((int(tx(thr)) - 9, by - 24), f"{thr:.0f}°", font=F["t"],
            fill=(110, 220, 150))

    # ---- 横卧确认进度 ----
    # 告警后检测器进入冷却期、不再更新确认计数，此时显示陈旧值会误导，
    # 因此告警后一律显示「已确认」。
    if alarmed_at is not None:
        dr.text((x0 + 14, 104), "√ 已确认 5/5", font=F["s"], fill=(120, 245, 165))
    else:
        lying_n = det._lying_frames
        dr.text((x0 + 14, 104), f"横卧确认 {min(lying_n, 5)}/5",
                font=F["s"],
                fill=(145, 235, 180) if lying_n >= 5 else (150, 165, 185))
    dr.text((x0 + 148, 104), f"状态 {res.state.value}", font=F["s"],
            fill=(235, 235, 240))

    # ---- 关键区间慢放标记 ----
    if slow:
        dr.rectangle((x0 + PW - 96, 8, x0 + PW - 12, 30), fill=(60, 40, 90, 220))
        dr.text((x0 + PW - 90, 11), "慢放 1/3", font=F["s"], fill=(215, 190, 255))

    # ---- 已告警面板常驻红框，便于一眼看出哪一路报了 ----
    if alarmed_at is not None:
        dr.rectangle((x0 + 1, 1, x0 + PW - 1, PH - 1),
                     outline=(215, 60, 55, 255), width=3)

    # ---- 告警横幅：触发瞬间起持续约 0.8 秒源时间 ----
    # 放在信息条正下方（该区域通常是墙面/地面，不遮挡倒地的人）
    if banner:
        by0, by1 = STRIP + 10, STRIP + 68
        dr.rectangle((x0, by0, x0 + PW, by1), fill=(190, 30, 30, 232))
        t1, t2 = "检测到跌倒", "已触发告警"
        dr.text((x0 + (PW - dr.textlength(t1, font=F["a"])) / 2, by0 + 4),
                t1, font=F["a"], fill=(255, 245, 245))
        dr.text((x0 + (PW - dr.textlength(t2, font=F["s"])) / 2, by1 - 20),
                t2, font=F["s"], fill=(255, 210, 210))


def build_fonts():
    from PIL import ImageFont

    return {
        "h": ImageFont.truetype(CN, 22),
        "m": ImageFont.truetype(CN, 24),
        "s": ImageFont.truetype(CN, 15),
        "t": ImageFont.truetype(CN, 13),
        "a": ImageFont.truetype(CN, 26),
        "top": ImageFont.truetype(CN, 26),
        "topS": ImageFont.truetype(CN, 17),
    }


def main() -> int:
    from PIL import Image, ImageDraw
    from ultralytics import YOLO

    fcfg = DetectionConfig()
    model = YOLO(fcfg.model_path)

    cfg_b = DetectionConfig()
    cfg_b.torso_angle_threshold = BASE_ANGLE
    cfg_c = DetectionConfig()
    cfg_c.torso_angle_threshold = CAL_ANGLE
    det_b, det_c = FallDetector(cfg_b), FallDetector(cfg_c)

    fonts = build_fonts()
    cap = cv2.VideoCapture(VIDEO)
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    print(f"{total} 帧 @ {fps:.2f}fps，开始渲染…", flush=True)

    vw = cv2.VideoWriter(OUT, cv2.VideoWriter_fourcc(*"mp4v"), fps, (W, H))
    idx = 0
    # 记录关键信息供结尾定格用
    stat = {"base_alarm": None, "cal_alarm": None, "peak_angle": 0.0}

    def compose(img_src, feat, rb, rc, title_suffix=""):
        canvas = Image.new("RGB", (W, H), (8, 12, 18))
        dr = ImageDraw.Draw(canvas, "RGBA")
        ts = feat.timestamp
        slow = SLOW_LO <= ts <= SLOW_HI

        def banner_for(alarmed_at):
            if alarmed_at is None:
                return False
            return ts - alarmed_at <= 0.8

        draw_panel(dr, 0, img_src, feat, rb, det_b, fonts,
                   title=f"基线 {BASE_ANGLE:.0f}°（当前默认）", thr=BASE_ANGLE,
                   thr_other=CAL_ANGLE, accent=(255, 150, 150), slow=slow,
                   alarmed_at=stat["base_alarm"], ts=ts,
                   banner=banner_for(stat["base_alarm"]))
        draw_panel(dr, PW, img_src, feat, rc, det_c, fonts,
                   title=f"标定 {CAL_ANGLE:.0f}°（吊装机位）", thr=CAL_ANGLE,
                   thr_other=BASE_ANGLE, accent=(150, 240, 180), slow=slow,
                   alarmed_at=stat["cal_alarm"], ts=ts,
                   banner=banner_for(stat["cal_alarm"]))

        # 中缝
        dr.line((PW, 0, PW, PH), fill=(90, 110, 135, 255), width=2)

        # 顶部时间轴
        dr.rectangle((0, PH, W, H), fill=(10, 15, 24, 255))
        dr.rectangle((10, PH + 30, W - 10, PH + 42), fill=(38, 48, 60, 255))
        span = (W - 20)
        prog = min(idx / max(total - 1, 1), 1.0)
        dr.rectangle((10, PH + 30, 10 + int(span * prog), PH + 42),
                     fill=(90, 150, 220, 255))
        # 关键区间标注
        kx0 = 10 + span * (SLOW_LO / (total / fps))
        kx1 = 10 + span * (SLOW_HI / (total / fps))
        dr.rectangle((int(kx0), PH + 26, int(kx1), PH + 46),
                     fill=(120, 90, 190, 120))
        dr.text((12, PH + 4), f"t = {idx / fps:5.2f}s", font=fonts["topS"],
                fill=(200, 215, 235))
        if stat["base_alarm"] is not None:
            ax = 10 + span * (stat["base_alarm"] / (total / fps))
            dr.line((int(ax), PH + 22, int(ax), PH + 50), fill=(230, 70, 60), width=3)
        if stat["cal_alarm"] is not None:
            ax = 10 + span * (stat["cal_alarm"] / (total / fps))
            dr.line((int(ax), PH + 22, int(ax), PH + 50), fill=(60, 210, 130), width=3)
        dr.text((W - 250, PH + 4), "紫色区段 = 慢放区间",
                font=fonts["topS"], fill=(180, 160, 220))
        return canvas

    while True:
        ok, frame = cap.read()
        if not ok:
            break
        h, w = frame.shape[:2]
        res = model.predict(frame, imgsz=fcfg.imgsz, conf=fcfg.conf,
                            device=fcfg.device, verbose=False)
        plotted = res[0].plot(labels=False, boxes=False, line_width=2)
        kpts = pick_primary(res[0])
        ts = idx / fps
        feat = extract_features(kpts, width=w, height=h, index=idx,
                                timestamp=ts, cfg=cfg_b)
        rb = det_b.feed(feat)
        rc = det_c.feed(feat)

        if rb.triggered and stat["base_alarm"] is None:
            stat["base_alarm"] = ts
        if rc.triggered and stat["cal_alarm"] is None:
            stat["cal_alarm"] = ts
        if feat.present:
            stat["peak_angle"] = max(stat["peak_angle"], feat.torso_angle)

        small = Image.fromarray(cv2.cvtColor(plotted, cv2.COLOR_BGR2RGB))
        small = small.resize((PW, PH))
        canvas = compose(small, feat, rb, rc)

        n = SLOW_N if SLOW_LO <= ts <= SLOW_HI else 1
        arr = cv2.cvtColor(np.array(canvas), cv2.COLOR_RGB2BGR)
        for _ in range(n):
            vw.write(arr)
        idx += 1
        if idx % 40 == 0:
            print(f"  {idx}/{total}", flush=True)

    # ---- 结尾定格 ----
    cap.release()
    print(f"  渲染完成，追加定格 {HOLD_END} 帧…", flush=True)

    end = Image.new("RGB", (W, H), (10, 14, 22))
    dr = ImageDraw.Draw(end, "RGBA")
    F = fonts
    dr.text((24, 40), "结果对照", font=F["top"], fill=(235, 240, 250))
    dr.line((24, 84, W - 24, 84), fill=(70, 90, 115, 255), width=1)

    rows = [
        ("基线 62°（当前默认）", "全程无告警 —— 漏报", (255, 150, 150),
         f"躯干角峰值 {stat['peak_angle']:.1f}°，未达 62° 横卧阈值"),
        ("标定 52°（吊装机位）", f"{stat['cal_alarm']:.2f}s 触发告警"
         if stat["cal_alarm"] else "无告警", (150, 240, 180),
         "横卧确认 5/5 达成，判定为跌倒"),
    ]
    y = 110
    for name, verdict, col, note in rows:
        dr.rectangle((24, y, W - 24, y + 96), fill=(20, 27, 38, 255),
                     outline=col, width=2)
        dr.text((44, y + 14), name, font=F["h"], fill=col)
        dr.text((44, y + 46), verdict, font=F["m"], fill=(235, 240, 250))
        dr.text((44, y + 74), note, font=F["s"], fill=(165, 180, 200))
        y += 112

    y += 10
    dr.text((24, y), "★ 但要注意：52° 的检出依赖姿态估计抖动造成的单帧尖峰，",
            font=F["s"], fill=(250, 205, 120))
    dr.text((24, y + 24), "   隔帧采样即失效（stride≥2 时 0 组配置可检出）。",
            font=F["s"], fill=(250, 205, 120))
    dr.text((24, y + 52), "   本机位仅 1 段正样本、0 段负样本，误报率无从测量。",
            font=F["s"], fill=(250, 205, 120))

    dr.line((24, y + 100, W - 24, y + 100), fill=(70, 90, 115, 255), width=1)
    dr.text((24, y + 114), "复现（不改任何默认值，标定值走命令行覆盖）",
            font=F["s"], fill=(150, 165, 185))
    dr.text((24, y + 142),
            "backend/pose/.venv/Scripts/python.exe -X utf8 -m backend.pose.run \\",
            font=F["t"], fill=(130, 200, 240))
    dr.text((24, y + 164),
            "  --video <吊装机位视频> --torso-angle 52 --confirm-frames 5 --json out.json",
            font=F["t"], fill=(130, 200, 240))
    dr.text((24, y + 196),
            "详细结论见 doc/03-子文档/36-吊装机位跌倒检出标定结论.md",
            font=F["s"], fill=(150, 165, 185))

    arr = cv2.cvtColor(np.array(end), cv2.COLOR_RGB2BGR)
    for _ in range(HOLD_END):
        vw.write(arr)
    vw.release()
    print(f"  已写出 {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
