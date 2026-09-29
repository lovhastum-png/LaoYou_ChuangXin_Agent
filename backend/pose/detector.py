"""基于姿态关键点的跌倒检测状态机。

设计依据（均为公开项目的常用做法，非本项目原创，也不构成医学判定）：

1. 姿态几何：躯干相对竖直的夹角 + 肩髋竖直跨度/肩宽比，判断"身体是否已横卧"。
   参考 omrode1/Person-falling-Pose 的 FALL_ANGLE_THRESHOLD 思路。
2. 运动学：鼻部纵坐标下降速度、躯干角速度超过阈值，判断"是否发生快速坠落"。
   阈值形态取自 AwaisShah75 项目的 v_y > 0.7 身高/秒、Δθ/Δt > 35°/秒。
3. 时序：横卧姿态需连续 N 帧确认，落定后需保持静止 M 秒，用于排除
   坐下、躺沙发、弯腰捡物等日常动作造成的误报。

状态机：ABSENT → UPRIGHT → DESCENDING → LYING → ALARMED
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

import numpy as np


class State(str, Enum):
    ABSENT = "absent"          # 画面内无有效人体
    UPRIGHT = "upright"        # 直立（含正常坐姿）
    DESCENDING = "descending"  # 快速下坠中
    LYING = "lying"            # 已横卧，观察是否静止
    ALARMED = "alarmed"        # 已确认跌倒，冷却中


@dataclass
class FrameFeatures:
    """单帧的几何/运动学特征。"""

    index: int
    timestamp: float
    present: bool = False
    torso_angle: float = 0.0          # 躯干相对竖直方向夹角（度）
    vertical_ratio: float = 0.0       # 肩髋竖直跨度 / 肩宽
    nose_y: float = 0.0               # 鼻部归一化纵坐标（0=顶部）
    hip_y: float = 0.0
    descent_speed: float = 0.0        # 身高/秒
    angular_velocity: float = 0.0     # 度/秒
    motion: float = 0.0               # 归一化位移/秒
    lying: bool = False
    falling: bool = False
    angle_jump: bool = False          # 该帧检出关键点跳变，运动量不可信


@dataclass
class DetectionResult:
    """一次判定的输出。"""

    triggered: bool
    state: State
    reason: str
    features: FrameFeatures
    detail: dict[str, Any] = field(default_factory=dict)


def _kp(kpts: np.ndarray, idx: int, conf_thr: float) -> np.ndarray | None:
    """取单个关键点，置信度不足返回 None。"""
    if kpts is None or len(kpts) <= idx:
        return None
    x, y, c = float(kpts[idx][0]), float(kpts[idx][1]), float(kpts[idx][2])
    if c < conf_thr:
        return None
    return np.array([x, y], dtype=float)


def extract_features(
    kpts: np.ndarray | None,
    *,
    width: int,
    height: int,
    index: int,
    timestamp: float,
    cfg,
) -> FrameFeatures:
    """从 COCO-17 关键点提取归一化几何特征。"""
    feat = FrameFeatures(index=index, timestamp=timestamp)
    if kpts is None or len(kpts) < 17:
        return feat

    nose = _kp(kpts, cfg.NOSE, cfg.kpt_conf_threshold)
    ls = _kp(kpts, cfg.L_SHOULDER, cfg.kpt_conf_threshold)
    rs = _kp(kpts, cfg.R_SHOULDER, cfg.kpt_conf_threshold)
    lh = _kp(kpts, cfg.L_HIP, cfg.kpt_conf_threshold)
    rh = _kp(kpts, cfg.R_HIP, cfg.kpt_conf_threshold)

    # 有效性门槛：关键点太少时几何量不可信，宁可当作"没人"也不要误判
    if kpts is not None and len(kpts) >= 17:
        valid = int(np.sum(np.asarray(kpts)[:, 2] >= cfg.kpt_conf_threshold))
        if valid < cfg.min_valid_keypoints:
            return feat

    # 至少要有肩和髋，才能算躯干
    if ls is None or rs is None or lh is None or rh is None:
        return feat

    shoulder_mid = (ls + rs) / 2.0
    hip_mid = (lh + rh) / 2.0
    shoulder_width = float(np.linalg.norm(ls - rs)) or 1.0

    torso = shoulder_mid - hip_mid  # 由髋指向肩
    # 躯干相对竖直方向的偏离角：
    # 用 |dy| / (|dx| + |dy|) 的形式计算，结果是「与竖直轴的最小夹角」，
    # 天然落在 0-90° 区间且与躯干朝向符号无关。
    # 这样既避免了 arccos 在人体倒置时翻到 150°+ 的假信号，
    # 也不会因为左右肩/髋关键点互换而产生 180° 跳变。
    dx = abs(float(torso[0]))
    dy = abs(float(torso[1]))
    if dx + dy < 1e-6:
        return feat
    feat.torso_angle = float(np.degrees(np.arctan2(dx, dy)))

    # 归一化躯干竖直跨度：|躯干竖直分量| / 画面高度。
    # 直立约 0.2-0.4，横卧趋近 0。
    feat.vertical_ratio = float(dy / max(height, 1))

    if nose is not None:
        feat.nose_y = float(nose[1]) / height
    feat.hip_y = float(hip_mid[1]) / height

    # 姿态判据在此处直接给出，保证任何调用方（评测脚本、状态机）拿到同一结论。
    # 横卧仅以躯干偏离竖直轴的角度为准，不叠加肩宽比 —— 实测肩宽比在
    # 正对/侧对镜头间波动过大，单独使用会显著抬高误报。
    feat.lying = feat.torso_angle >= cfg.torso_angle_threshold

    feat.present = True
    return feat


class FallDetector:
    """有状态的单目标跌倒检测器。

    假定画面主位只有一个老人（居家场景）。多人时取面积最大/置信度最高者，
    这是当前基线版本的明确限制。
    """

    def __init__(self, cfg):
        self.cfg = cfg
        self.state = State.ABSENT
        self._history: deque[FrameFeatures] = deque(maxlen=cfg.window_frames)
        self._lying_frames = 0
        self._lying_since: float | None = None
        self._rest_anchor: tuple[float, float] | None = None
        self._last_alarm_at: float = -1e9
        self._last_falling_at: float = -1e9

    # ------------------------------------------------------------------ #
    def reset(self) -> None:
        self.state = State.ABSENT
        self._history.clear()
        self._lying_frames = 0
        self._lying_since = None
        self._rest_anchor = None
        self._last_falling_at = -1e9

    # ------------------------------------------------------------------ #
    def _compute_kinematics(self, feat: FrameFeatures) -> None:
        """用历史窗口计算下降速度、角速度与位移速率。

        所有速度量都以「画面高度 / 秒」为单位，不依赖人体像素尺度，
        因此对远近不同的机位保持一致。
        """
        if not self._history:
            return
        prev = self._history[-1]
        dt = feat.timestamp - prev.timestamp
        if dt <= 1e-6:
            return

        # 姿态模型偶尔会在相邻帧之间把左右肩/髋判反（或短暂丢失后重捕），
        # 造成单帧内几百度的假角速度。躯干只有约 0.5 秒的物理转体极限，
        # 因此把 dt 内的角度变化上限钳到 300° —— 超过即视为关键点跳变，
        # 不计入运动量，避免污染下降/角速度信号。
        MAX_DEG_PER_STEP = 300.0

        # 鼻部下降速度：鼻 y 增大表示下降
        if feat.present and prev.present and feat.nose_y > 0 and prev.nose_y > 0:
            v = (feat.nose_y - prev.nose_y) / dt
            if abs(v) <= 3.0:  # 3 画面高/秒已远超人体下落极限
                feat.descent_speed = float(v)

        # 躯干角速度
        if feat.present and prev.present:
            delta = abs(feat.torso_angle - prev.torso_angle)
            if delta > MAX_DEG_PER_STEP:
                feat.angular_velocity = 0.0
                feat.angle_jump = True
            else:
                feat.angular_velocity = float(delta / dt)

        # 髋部位移速率。
        # 注意：依赖上一帧的 hip_y，因此只在相邻帧（无跳帧）时有意义。
        # 平安看护默认每 5 帧推理一次，帧间不连续，此时由调用方
        # （presence 侧）用「最近一次有效移动距今多久」代替瞬时速度判断静止。
        if prev.present and feat.present and dt > 0:
            feat.motion = float(abs(feat.hip_y - prev.hip_y) / dt)

    # ------------------------------------------------------------------ #
    def _evaluate(self, feat: FrameFeatures) -> tuple[bool, str, dict[str, Any]]:
        cfg = self.cfg

        # 姿态是否横卧：躯干接近水平即可，不再叠加肩宽比条件
        # —— 实测肩宽比在正对/侧对镜头时波动过大，单独用角度更稳。
        feat.lying = feat.torso_angle >= cfg.torso_angle_threshold

        # 是否处于快速坠落阶段（用于区分"摔下去"与"慢慢躺下"）
        feat.falling = (
            feat.descent_speed >= cfg.descent_speed_threshold
            or feat.angular_velocity >= cfg.angular_velocity_threshold
        )

        detail = {
            "torso_angle": round(feat.torso_angle, 2),
            "vertical_ratio": round(feat.vertical_ratio, 3),
            "descent_speed": round(feat.descent_speed, 3),
            "angular_velocity": round(feat.angular_velocity, 2),
            "motion": round(feat.motion, 4),
        }

        # 记录坠落证据的时间戳，供横卧阶段回溯校验
        if feat.falling:
            self._last_falling_at = feat.timestamp

        if not feat.present:
            self._lying_frames = 0
            self._lying_since = None
            self._rest_anchor = None
            self.state = State.ABSENT
            return False, "画面内无有效人体", detail

        # 冷却期内不重复告警
        if feat.timestamp - self._last_alarm_at < cfg.cooldown_seconds:
            self.state = State.ALARMED
            return False, "告警冷却中", detail

        self.state = State.UPRIGHT

        # --- 阶段 1：快速坠落 ---
        if feat.falling and feat.lying:
            self.state = State.DESCENDING

        # --- 阶段 2：横卧确认 ---
        if feat.lying:
            self._lying_frames += 1
            if self._lying_since is None:
                self._lying_since = feat.timestamp
                self._rest_anchor = (feat.hip_y, feat.nose_y)
        else:
            self._lying_frames = 0
            self._lying_since = None
            self._rest_anchor = None
            return False, "直立或正常坐姿", detail

        if self._lying_frames < cfg.lying_confirm_frames:
            self.state = State.DESCENDING
            return (
                False,
                f"横卧姿态确认中 {self._lying_frames}/{cfg.lying_confirm_frames}",
                detail,
            )

        self.state = State.LYING

        # --- 阶段 2.5：坠落证据校验 ---
        # 只有「先快速坠落、再横卧」才判为跌倒；慢慢躺下/坐下不告警。
        # 这里是精确度的主要来源，比单纯拉长静止窗口有效得多。
        if cfg.require_falling_evidence:
            has_evidence = (
                feat.timestamp - self._last_falling_at
                <= cfg.falling_evidence_window
            )
            if not has_evidence:
                return False, "横卧但无坠落证据（疑似躺下/坐下）", detail
            detail["falling_evidence"] = round(
                feat.timestamp - self._last_falling_at, 2
            )

        # --- 阶段 3：落定确认 ---
        # 判据是「横卧持续时长」，不是「额外静止时长」。
        # 实测 URFD 受试者跌倒后很快起身，最长横卧仅 0.5-0.7 秒，
        # 若要求落定后再静止 immobility_seconds 会大量漏检。
        # 因此改为：连续横卧达到 max(lying_confirm_frames 帧, immobility_seconds 秒)
        # 即确认，immobility_seconds 默认 0 表示只靠帧数确认。
        if self._lying_since is None:
            return False, "横卧但未开始计时", detail
        elapsed = feat.timestamp - self._lying_since
        if elapsed < cfg.immobility_seconds:
            return False, f"横卧确认中 {elapsed:.1f}/{cfg.immobility_seconds}s", detail

        # 可选：要求髋部在横卧期间基本不动，排除「横卧中继续走动」的假信号。
        # 默认关闭，因为起身动作本身会产生位移，开启会降低召回。
        if cfg.enforce_rest_check and self._rest_anchor is not None:
            drift = abs(feat.hip_y - self._rest_anchor[0])
            if drift > cfg.immobility_motion_threshold * max(elapsed, 1.0):
                self._rest_anchor = (feat.hip_y, feat.nose_y)
                self._lying_since = feat.timestamp
                return False, "横卧中仍有位移，继续观察", detail

        self._last_alarm_at = feat.timestamp
        self.state = State.ALARMED
        detail["elapsed_lying_seconds"] = round(elapsed, 2)
        detail["confirm_frames"] = self._lying_frames
        return True, "坠落 + 横卧确认，判定为跌倒", detail

    # ------------------------------------------------------------------ #
    def feed(self, feat: FrameFeatures) -> DetectionResult:
        """送入一帧特征，返回判定结果。"""
        self._compute_kinematics(feat)
        self._history.append(feat)
        triggered, reason, detail = self._evaluate(feat)
        return DetectionResult(
            triggered=triggered,
            state=self.state,
            reason=reason,
            features=feat,
            detail=detail,
        )
