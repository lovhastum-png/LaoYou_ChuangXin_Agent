"""平安看护的自动化测试。

覆盖四条判定路径 + 「不误报」这条最重要的边界：

  1. 画面内有人            -> 平安
  2. 人在画面内但不动      -> 长时间无动作
  3. 人离开画面            -> 离画
  4. 画面内摔倒            -> 摔倒（即使人还在画面里，也必须是「不平安」）
  5. 静态背景（无人）      -> 必须判为离画，不能被模型误检顶住

第 4 条是「平安 = 在画面内 且 没摔倒」这个定义的直接检验：
摔倒的优先级必须高于「人在画面内」，否则老人躺在地上反而显示平安。

第 5 条是这套东西最容易错的地方：yolov8n-pose 会把静态背景误检成人形，
实测误检的关键点平均置信度约 0.50，真人在 0.91 以上。
如果不设质量门槛，"老人离画"永远判不出来。

测试素材由 tmp/presence-test/ 下的脚本合成，真值来自构造方式而非人工标注。

用法：
    python -m backend.pose.test_presence
"""

from __future__ import annotations

import os

import cv2
import numpy as np

from .config import DetectionConfig, PresenceConfig
from .detector import FallDetector, extract_features
from .presence import (
    PresenceMonitor,
    PresenceState,
    feature_from_frame,
)
from .presence_run import _pick_for_fall, _pick_primary

GEOMETRY = DetectionConfig()


def _load_model():
    from ultralytics import YOLO

    cfg = PresenceConfig()
    return YOLO(cfg.model_path), cfg


def _read_frames(path: str) -> tuple[list, float]:
    cap = cv2.VideoCapture(path)
    if not cap.isOpened():
        raise FileNotFoundError(path)
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    frames = []
    while True:
        ok, f = cap.read()
        if not ok:
            break
        frames.append(f)
    cap.release()
    return frames, fps


def _run_frames(
    frames: list,
    fps: float,
    cfg: PresenceConfig,
    model,
    *,
    detect_fall: bool = False,
) -> tuple[PresenceMonitor, list]:
    """逐帧跑状态机，返回 (状态机, 每帧状态序列)。

    detect_fall=True 时同时跑跌倒检测，并把结论并入平安判定。
    取人逻辑分别对应正式实现：平安看护用严格门槛，摔倒检测用宽松取人。
    """
    monitor = PresenceMonitor(cfg)
    fall_det = FallDetector(DetectionConfig()) if detect_fall else None
    states = []
    for idx, frame in enumerate(frames):
        h, w = frame.shape[:2]
        if w > cfg.resize_width:
            scale = cfg.resize_width / w
            frame = cv2.resize(frame, (int(w * scale), int(h * scale)))
            h, w = frame.shape[:2]
        results = model.predict(frame, imgsz=cfg.imgsz, conf=cfg.conf, device=cfg.device, verbose=False)
        kpts, center, _ = _pick_primary(results[0], cfg)
        feat = extract_features(
            kpts, width=w, height=h, index=idx, timestamp=idx / fps, cfg=GEOMETRY
        )
        pfeat = feature_from_frame(feat, center)
        if fall_det is not None:
            fk = _pick_for_fall(results[0], cfg)
            ffeat = extract_features(
                fk, width=w, height=h, index=idx, timestamp=idx / fps, cfg=GEOMETRY
            )
            fr = fall_det.feed(ffeat)
            if fr.triggered:
                pfeat.fallen = True
                pfeat.fallen_reason = fr.reason
                pfeat.fall_detail = dict(fr.detail or {})
        res = monitor.feed(pfeat)
        states.append(res.state)
    return monitor, states


def _check(name: str, got, want, note: str = "") -> bool:
    ok = got == want
    mark = "通过" if ok else "失败"
    print(f"  [{mark}] {name}: 期望={want} 实际={got}" + (f"  {note}" if note else ""))
    return ok


def main() -> int:
    model, base = _load_model()
    results: list[bool] = []

    # ---- 用例 1：画面内有人 -> 平安 ----
    print("\n用例 1  画面内能看到老人且没摔倒，应判为平安")
    cfg = PresenceConfig(missing_grace_seconds=3, still_seconds=0, frame_stride=1)
    frames, fps = _read_frames("backend/pose/samples/adl-01-cam0.mp4")
    _, states = _run_frames(frames, fps, cfg, model, detect_fall=True)
    safe_ratio = sum(1 for s in states if s is PresenceState.IN_VIEW) / max(len(states), 1)
    results.append(_check(
        "全程判为平安的比例", round(safe_ratio, 3), 1.0,
        f"（{len(states)} 帧，含摔倒识别，日常动作不应被打断）",
    ))

    # ---- 用例 2：画面内摔倒 -> 必须是摔倒，不能被「在画面内」盖过 ----
    print("\n用例 2  老人在画面内摔倒，应判为摔倒（而不是平安）")
    print("        这条检验「平安 = 在画面内 且 没摔倒」：")
    print("        摔倒优先级必须高于「人在画面内」，否则老人躺着反而显示平安。")
    cfg2 = PresenceConfig(missing_grace_seconds=3, still_seconds=0, frame_stride=1)
    frames2, fps2 = _read_frames("backend/pose/samples/fall-01-cam0.mp4")
    _, states2 = _run_frames(frames2, fps2, cfg2, model, detect_fall=True)
    has_fallen = any(s is PresenceState.FALLEN for s in states2)
    results.append(_check("摔倒状态是否出现", has_fallen, True))
    # 摔倒之后不该再回到平安
    if has_fallen:
        first_fall = next(i for i, s in enumerate(states2) if s is PresenceState.FALLEN)
        after = states2[first_fall:]
        all_unsafe = all(s is not PresenceState.IN_VIEW for s in after)
        results.append(_check(
            "摔倒后不再翻回平安", all_unsafe, True,
            "（保持窗口内不得被 in_view 覆盖）",
        ))

    # ---- 用例 3：静态背景，必须判为离画（防误检） ----
    print("\n用例 3  画面里没有老人（静态背景），应判为离画")
    print("        这一条最容易错：模型会把家具误检成人形。")
    bg_path = "tmp/presence-test/bg-only.mp4"
    if not os.path.exists(bg_path):
        print(f"        跳过：缺少素材 {bg_path}")
    else:
        cfg3 = PresenceConfig(missing_grace_seconds=2, still_seconds=0, frame_stride=1)
        frames3, fps3 = _read_frames(bg_path)
        _, states3 = _run_frames(frames3, fps3, cfg3, model)
        tail = states3[int(len(states3) * 0.5):]
        out_ratio = sum(1 for s in tail if s is PresenceState.OUT_OF_VIEW) / max(len(tail), 1)
        results.append(_check("后半段判为离画的比例", round(out_ratio, 3), 1.0))

    # ---- 用例 4：画面内长时间不动 ----
    print("\n用例 4  老人在画面内但长时间没有动作，应判为长时间无动作")
    still_path = "tmp/presence-test/sit-still.mp4"
    if not os.path.exists(still_path):
        print(f"        跳过：缺少素材 {still_path}")
    else:
        cfg4 = PresenceConfig(missing_grace_seconds=5, still_seconds=5, frame_stride=1)
        frames4, fps4 = _read_frames(still_path)
        _, states4 = _run_frames(frames4, fps4, cfg4, model)
        tail4 = states4[int(len(states4) * 0.5):]
        still_ratio = sum(1 for s in tail4 if s is PresenceState.STILL) / max(len(tail4), 1)
        results.append(_check("后半段判为长时间无动作的比例", round(still_ratio, 3), 1.0))

    # ---- 用例 5：走出去再走回来，应产生离画与恢复 ----
    print("\n用例 5  老人走出画面再回来，应产生「离画」与「恢复平安」")
    walk_path = "tmp/presence-test/walk-away.mp4"
    if not os.path.exists(walk_path):
        print(f"        跳过：缺少素材 {walk_path}")
    else:
        cfg5 = PresenceConfig(missing_grace_seconds=2, still_seconds=0, frame_stride=1)
        frames5, fps5 = _read_frames(walk_path)
        _, states5 = _run_frames(frames5, fps5, cfg5, model)
        seen = []
        for s in states5:
            if not seen or seen[-1] != s:
                seen.append(s)
        results.append(_check(
            "状态变化序列",
            [s.value for s in seen],
            ["in_view", "out_of_view", "in_view"],
        ))

    passed = sum(1 for r in results if r)
    print(f"\n=== 结果：{passed}/{len(results)} 项通过 ===")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
