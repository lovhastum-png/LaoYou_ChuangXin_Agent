"""跌倒检测状态机的自动化测试。

重点覆盖两条最容易出错的路径：

  1. 正常的「快速坠落 + 横卧确认」必须告警（回归基线行为）。
  2. 「朝镜头扑倒并沉出画面」在基线配置下**必然漏报**；
     开启「坠落并沉出画面」判据后必须告警，且关闭时绝不能告警。

第 2 条来自真实素材（`scripts/diagnostics/pose-fall-verify/`）：一段 1280×720
走廊平视视频里，下降速度连续 5 帧越过 0.8 阈值，但躯干角最大只有 21°，
随后人整体沉出画面下沿 —— 基线 0 告警。这里用等价的合成特征序列把它固化下来。

测试直接构造 FrameFeatures，不加载姿态模型、不读视频，因此可离线快速运行。

用法：
    python -m backend.pose.test_detector
"""

from __future__ import annotations

import numpy as np

from .config import DetectionConfig
from .detector import FallDetector, FrameFeatures, State


def _feat(i: int, t: float, *, nose_y: float, hip_y: float, angle: float) -> FrameFeatures:
    return FrameFeatures(
        index=i,
        timestamp=t,
        present=True,
        torso_angle=angle,
        vertical_ratio=0.25,
        nose_y=nose_y,
        hip_y=hip_y,
        lying=angle >= 62.0,
    )


def _absent(i: int, t: float) -> FrameFeatures:
    return FrameFeatures(index=i, timestamp=t, present=False)


def _run(det: FallDetector, feats: list[FrameFeatures]) -> list:
    return [det.feed(f) for f in feats]


# --------------------------------------------------------------------------- #
def test_normal_fall_still_alarms():
    """直立 → 快速下坠 → 横卧数帧：基线行为必须保持告警。"""
    det = FallDetector(DetectionConfig())
    fs = []
    # 前 10 帧直立走动
    for i in range(10):
        fs.append(_feat(i, i / 30.0, nose_y=0.25, hip_y=0.55, angle=3.0))
    # 快速下坠 4 帧（鼻部每帧下移 0.06 → 1.8 画面高/秒，远超 0.8 阈值）
    for k in range(4):
        i = 10 + k
        fs.append(_feat(i, i / 30.0, nose_y=0.25 + 0.06 * (k + 1),
                        hip_y=0.62, angle=30.0 + k * 10))
    # 横卧 8 帧
    for k in range(8):
        i = 14 + k
        fs.append(_feat(i, i / 30.0, nose_y=0.75, hip_y=0.80, angle=75.0))

    res = _run(det, fs)
    alarms = [r for r in res if r.triggered]
    assert alarms, "基线「坠落+横卧」路径不应退化"
    assert alarms[0].state is State.ALARMED


def test_slow_lying_down_does_not_alarm():
    """慢慢躺下（无坠落证据）不得告警 —— 这是精确度的主要来源。"""
    det = FallDetector(DetectionConfig())
    fs = []
    for i in range(30):
        t = i / 30.0
        # 30 帧内从直立到横卧，鼻部只下降 0.1 → 0.1 画面高/秒，远低于 0.8
        fs.append(_feat(i, t, nose_y=0.25 + 0.10 * i / 29.0,
                        hip_y=0.55, angle=3.0 + 72.0 * i / 29.0))
    res = _run(det, fs)
    assert not any(r.triggered for r in res), "慢速躺下不得告警"


def test_frame_exit_fall_off_does_not_alarm():
    """基线配置：快速坠落但躯干来不及横卧、随后沉出画面 → 必须漏报（已知边界）。"""
    det = FallDetector(DetectionConfig())  # detect_frame_exit_fall=False
    fs = []
    for i in range(60):
        fs.append(_feat(i, i / 30.0, nose_y=0.45, hip_y=0.90, angle=2.0))
    # 5 帧快速下坠：鼻部每帧 +0.04 → 1.2 画面高/秒；角度只升到 21°
    for k in range(5):
        i = 60 + k
        fs.append(_feat(i, i / 30.0, nose_y=0.45 + 0.04 * (k + 1),
                        hip_y=1.00, angle=10.0 + k * 2.75))
    # 人出画
    for k in range(10):
        i = 65 + k
        fs.append(_absent(i, i / 30.0))

    res = _run(det, fs)
    assert not any(r.triggered for r in res), "基线本就该漏报，若告警说明阈值被意外改动"


def test_frame_exit_fall_on_alarms():
    """开启判据：同样序列必须告警，且理由是「沉出画面」。"""
    cfg = DetectionConfig()
    cfg.detect_frame_exit_fall = True
    det = FallDetector(cfg)

    fs = []
    for i in range(60):
        fs.append(_feat(i, i / 30.0, nose_y=0.45, hip_y=0.90, angle=2.0))
    for k in range(5):
        i = 60 + k
        fs.append(_feat(i, i / 30.0, nose_y=0.45 + 0.04 * (k + 1),
                        hip_y=1.00, angle=10.0 + k * 2.75))
    for k in range(10):
        i = 65 + k
        fs.append(_absent(i, i / 30.0))

    res = _run(det, fs)
    alarms = [r for r in res if r.triggered]
    assert len(alarms) == 1, f"应恰好告警一次，实际 {len(alarms)} 次"
    assert "沉出画面" in alarms[0].reason
    assert alarms[0].detail.get("exit_frame_fall") is True
    # 告警发生在人出画之后的第一帧
    assert alarms[0].features.index == 65


def test_frame_exit_fall_requires_deep_hip():
    """人出画但髋部没抵近下沿（例如只是被门框挡住）不得告警。"""
    cfg = DetectionConfig()
    cfg.detect_frame_exit_fall = True
    det = FallDetector(cfg)

    fs = []
    for i in range(60):
        fs.append(_feat(i, i / 30.0, nose_y=0.45, hip_y=0.55, angle=2.0))
    for k in range(5):
        i = 60 + k
        fs.append(_feat(i, i / 30.0, nose_y=0.45 + 0.04 * (k + 1),
                        hip_y=0.60, angle=10.0 + k * 2.75))
    for k in range(10):
        i = 65 + k
        fs.append(_absent(i, i / 30.0))

    res = _run(det, fs)
    assert not any(r.triggered for r in res), "髋部未到下沿不应告警"


def test_frame_exit_fall_requires_recent_evidence():
    """髋部虽抵近下沿，但坠落证据已过期（慢慢走出画面）不得告警。"""
    cfg = DetectionConfig()
    cfg.detect_frame_exit_fall = True
    det = FallDetector(cfg)

    fs = []
    for i in range(60):
        fs.append(_feat(i, i / 30.0, nose_y=0.45, hip_y=0.90, angle=2.0))
    # 只有一次很轻的下降，不构成坠落证据
    for k in range(5):
        i = 60 + k
        fs.append(_feat(i, i / 30.0, nose_y=0.45 + 0.005 * (k + 1),
                        hip_y=1.00, angle=3.0))
    for k in range(10):
        i = 65 + k
        fs.append(_absent(i, i / 30.0))

    res = _run(det, fs)
    assert not any(r.triggered for r in res), "无坠落证据不应告警"


def test_frame_exit_fall_respects_cooldown():
    """告警后处于冷却期，重复出画不得连续告警。"""
    cfg = DetectionConfig()
    cfg.detect_frame_exit_fall = True
    det = FallDetector(cfg)

    def burst(start_idx: int):
        out = []
        for i in range(start_idx, start_idx + 60):
            out.append(_feat(i, i / 30.0, nose_y=0.45, hip_y=0.90, angle=2.0))
        for k in range(5):
            i = start_idx + 60 + k
            out.append(_feat(i, i / 30.0, nose_y=0.45 + 0.04 * (k + 1),
                             hip_y=1.00, angle=10.0 + k * 2.75))
        for k in range(10):
            i = start_idx + 65 + k
            out.append(_absent(i, i / 30.0))
        return out

    fs = burst(0) + burst(75)  # 两次出画间隔 (75-65)/30 ≈ 0.33s，远小于 30s 冷却
    res = _run(det, fs)
    assert len([r for r in res if r.triggered]) == 1, "冷却期内不得重复告警"


def test_extract_features_geometry():
    """几何量：直立约 0°，横卧接近 90°；有效关键点不足时视为无人。"""
    from .detector import extract_features

    cfg = DetectionConfig()

    def kpts(angle_deg: float) -> np.ndarray:
        """构造一个躯干与竖直轴成指定夹角的人形，全部关键点高置信。"""
        a = np.radians(angle_deg)
        hip = np.array([320.0, 400.0])
        torso = np.array([np.sin(a), -np.cos(a)]) * 120.0
        shoulder = hip + torso
        k = np.zeros((17, 3), dtype=float)
        k[:, 2] = 0.9
        k[0] = [shoulder[0], shoulder[1] - 40.0, 0.9]        # nose
        k[5] = [shoulder[0] - 30.0, shoulder[1], 0.9]        # L shoulder
        k[6] = [shoulder[0] + 30.0, shoulder[1], 0.9]        # R shoulder
        k[11] = [hip[0] - 20.0, hip[1], 0.9]                 # L hip
        k[12] = [hip[0] + 20.0, hip[1], 0.9]                 # R hip
        return k

    upright = extract_features(kpts(0.0), width=640, height=480,
                               index=0, timestamp=0.0, cfg=cfg)
    assert upright.present
    assert upright.torso_angle < 5.0
    assert not upright.lying

    lying = extract_features(kpts(85.0), width=640, height=480,
                             index=0, timestamp=0.0, cfg=cfg)
    assert lying.present
    assert lying.torso_angle > 80.0
    assert lying.lying

    # 关键点置信度全部不足 → 视为无人
    blank = np.zeros((17, 3), dtype=float)
    none_feat = extract_features(blank, width=640, height=480,
                                 index=0, timestamp=0.0, cfg=cfg)
    assert not none_feat.present


def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for fn in tests:
        try:
            fn()
            print(f"  PASS  {fn.__name__}")
        except AssertionError as exc:
            failed += 1
            print(f"  FAIL  {fn.__name__} :: {exc}")
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"  ERROR {fn.__name__} :: {exc.__class__.__name__}: {exc}")
    print(f"\n  {len(tests) - failed}/{len(tests)} 通过")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
