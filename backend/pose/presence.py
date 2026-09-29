"""平安看护：判断老人在摄像头画面内是否安好。

需求范围（刻意做小）：
    摄像头开着、能看见老人、老人没摔倒，就认为老人平安。
    看不见老人太久、老人在画面内长时间僵住不动、或者老人摔倒了，才提示家属。

判定只用现成的姿态模型（yolov8n-pose），不引入第二个模型，
不做房间识别，不做场景分类。

四个状态：
    IN_VIEW     画面内能看到老人且没摔倒  -> 平安（心跳，落库用）
    OUT_OF_VIEW 连续找不到人超过阈值      -> 不平安
    STILL       人在画面内但长时间无动作  -> 不平安
    FALLEN      人在画面内但判定为摔倒    -> 不平安（最高优先级）

判定优先级：FALLEN > OUT_OF_VIEW > STILL > IN_VIEW
    摔倒一旦确认，即使人还躺在画面里，也必须是「不平安」，
    不能被「人在画面内」这条更宽松的判据盖过去。
    实测踩过：如果先判在不在画面、再判摔没摔，摔倒会被 in_view 吞掉。

上报策略（关键，防止灌爆观测表）：
    只在「平安 -> 不平安」和「不平安 -> 平安」这两个时刻各上报一次，
    中间持续期间不重复上报。家属端看到的是状态变化点，不是每秒一条。

本模块只做推理与判定，不直接写数据库；上报由 client 层负责。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class PresenceState(str, Enum):
    IN_VIEW = "in_view"            # 画面内可见且未摔倒，平安
    OUT_OF_VIEW = "out_of_view"    # 持续看不到人
    STILL = "still"                # 画面内但长时间无动作
    FALLEN = "fallen"              # 画面内但判定为摔倒


# 状态 -> 是否平安。只有「看得见人且没摔倒」才算平安。
SAFE_STATES = {PresenceState.IN_VIEW}

# 不平安状态的紧迫程度：摔倒最急，其次失踪，再次不动。
# 上报与展示都按这个顺序取最高优先级。
PRIORITY = {
    PresenceState.FALLEN: 3,
    PresenceState.OUT_OF_VIEW: 2,
    PresenceState.STILL: 1,
    PresenceState.IN_VIEW: 0,
}


@dataclass
class PresenceFeature:
    """单帧输入。由调用方从 FrameFeatures 转换，保持本模块与几何实现解耦。"""

    index: int
    timestamp: float
    present: bool = False          # 本帧是否检测到有效人体
    motion: float = 0.0            # 归一化位移/秒
    box_center: tuple[float, float] | None = None  # 人体框中心（归一化 0-1）
    # 摔倒判定：由 FallDetector 给出，本模块不重复实现几何逻辑
    fallen: bool = False           # 本帧是否刚确认摔倒（单帧触发，需保持）
    fallen_reason: str = ""        # 摔倒判定理由，用于日志与展示
    fall_detail: dict[str, Any] = field(default_factory=dict)


@dataclass
class PresenceResult:
    """一次判定输出。"""

    state: PresenceState
    safe: bool
    reason: str
    # 需要上报时才非空：取值 "in_view" / "out_of_view" / "still"
    report: str | None = None
    detail: dict[str, Any] = field(default_factory=dict)


class PresenceMonitor:
    """平安看护状态机。

    参数含义见 PresenceConfig；核心是三个阈值：
        missing_grace_seconds  连续看不到人多久才算「离画」（容忍走动中被遮挡）
        still_seconds          画面内无动作多久才算「长时间不动」
        fall_hold_seconds      摔倒判定后保持多久（跌倒检测单帧触发，需转成持续状态）

    摔倒不在这里做几何判断 —— 由 FallDetector 负责，本类只接收结论并纳入状态。
    """

    def __init__(self, cfg):
        self.cfg = cfg
        self.state: PresenceState | None = None
        self._last_seen_at: float | None = None      # 最后一次看到人的时间
        self._first_seen_at: float | None = None     # 当前这段可见的起始时间
        self._still_since: float | None = None       # 画面内开始「没动」的时间
        self._last_motion_at: float | None = None    # 最后一次有动作的时间
        self._report_fired: dict[str, bool] = {}     # 当前风险是否已上报
        self._t0: float | None = None
        self._fall_at: float | None = None           # 最近一次确认摔倒的时间
        self._fall_reason: str = ""
        self._fall_detail: dict[str, Any] = {}

    # ------------------------------------------------------------------ #
    def _emit(self, state: PresenceState, reason: str, detail: dict) -> PresenceResult:
        """产出结果，并在状态发生有意义变化时给出待上报标记。"""
        safe = state in SAFE_STATES
        report: str | None = None

        if self.state is None:
            # 第一帧：建立初始状态，只在「开局就不平安」时上报一次
            self.state = state
            if not safe:
                report = state.value
                self._report_fired[state.value] = True
        elif state != self.state:
            prev = self.state
            self.state = state
            if safe:
                # 恢复平安：清空风险标记，并上报一次恢复
                self._report_fired.clear()
                report = "in_view"
            else:
                if not self._report_fired.get(state.value):
                    report = state.value
                    self._report_fired[state.value] = True
            detail = {**detail, "from": prev.value}

        return PresenceResult(state=state, safe=safe, reason=reason, report=report, detail=detail)

    # ------------------------------------------------------------------ #
    def feed(self, feat: PresenceFeature) -> PresenceResult:
        t = feat.timestamp
        if self._t0 is None:
            self._t0 = t

        # 记录摔倒判定。跌倒检测是单帧触发，这里转成「保持一段时间」的状态，
        # 否则摔倒后下一帧就会因为「人在画面内」而被判回平安。
        if feat.fallen:
            self._fall_at = t
            self._fall_reason = feat.fallen_reason
            self._fall_detail = dict(feat.fall_detail or {})

        if feat.present:
            self._last_seen_at = t
            if self._first_seen_at is None:
                self._first_seen_at = t
            # 有动作就刷新动作时间；每帧都刷，等价于「距上次有明显移动多久」
            if feat.motion >= self.cfg.still_motion_threshold:
                self._last_motion_at = t
                self._still_since = None
            elif self._still_since is None:
                self._still_since = t
        else:
            self._first_seen_at = None
            self._still_since = None

        # ---- 判定顺序：摔倒 > 在不在 > 动不动 ----
        # 摔倒必须排在最前。如果先判「人在不在画面里」，摔倒会被 in_view 吞掉，
        # 老人躺在地上反而显示平安（实测踩过这个顺序问题）。

        # 1) 摔倒判定（优先级最高）
        if self._fall_at is not None and t - self._fall_at <= self.cfg.fall_hold_seconds:
            held = t - self._fall_at
            return self._emit(
                PresenceState.FALLEN,
                self._fall_reason or "检测到跌倒",
                {
                    **self._fall_detail,
                    "fall_held_seconds": round(held, 1),
                    "fall_detected_at": round(self._fall_at, 2),
                },
            )

        # 2) 离画判定
        if self._last_seen_at is None:
            # 从头到尾没见过人：按已经观察了多久来算，避免开机瞬间就报
            elapsed = t - self._t0
            if elapsed >= self.cfg.missing_grace_seconds:
                return self._emit(
                    PresenceState.OUT_OF_VIEW,
                    f"开机后 {elapsed:.0f} 秒未在画面内看到老人",
                    {"missing_seconds": round(elapsed, 1)},
                )
            return self._emit(
                PresenceState.IN_VIEW,
                f"等待画面内出现老人（已 {elapsed:.0f} 秒）",
                {"warming_up": True},
            )

        missing = t - self._last_seen_at
        if missing >= self.cfg.missing_grace_seconds:
            return self._emit(
                PresenceState.OUT_OF_VIEW,
                f"已连续 {missing:.0f} 秒未在画面内看到老人",
                {"missing_seconds": round(missing, 1)},
            )

        # 3) 在画面内：检查是否长时间没动作
        if self.cfg.still_seconds > 0:
            anchor = self._last_motion_at if self._last_motion_at is not None else self._first_seen_at
            if anchor is not None:
                still_for = t - anchor
                if still_for >= self.cfg.still_seconds:
                    return self._emit(
                        PresenceState.STILL,
                        f"老人在画面内已 {_duration_text(still_for)}没有明显动作",
                        {"still_seconds": round(still_for, 1)},
                    )

        visible_for = t - self._first_seen_at if self._first_seen_at is not None else 0.0
        still_for = t - (self._last_motion_at or t)
        # 带上当前已「看不见人」的时长，即使还没到阈值。
        # 用处有两个：家属端能看到风险在累积；演示与排查时能看出宽限期进度。
        pending = max(t - self._last_seen_at, 0.0)
        return self._emit(
            PresenceState.IN_VIEW,
            "画面内可见老人",
            {
                "visible_seconds": round(visible_for, 1),
                "still_seconds": round(still_for, 1),
                "missing_seconds": round(pending, 1),
                "grace_seconds": self.cfg.missing_grace_seconds,
            },
        )


def _duration_text(seconds: float) -> str:
    """把秒数写成人话：不足 1 分钟说秒，超过 1 分钟说分。"""
    if seconds < 60:
        return f"{seconds:.0f} 秒"
    if seconds < 3600:
        return f"{seconds / 60:.0f} 分钟"
    return f"{seconds / 3600:.1f} 小时"


def feature_from_frame(feat, box_center: tuple[float, float] | None = None) -> PresenceFeature:
    """把 detector.FrameFeatures 转成 PresenceFeature。"""
    return PresenceFeature(
        index=feat.index,
        timestamp=feat.timestamp,
        present=bool(feat.present),
        motion=float(feat.motion),
        box_center=box_center,
    )


def safe_label(state: PresenceState) -> str:
    return "平安" if state in SAFE_STATES else "需留意"


def describe(state: PresenceState) -> str:
    return {
        PresenceState.IN_VIEW: "老人在画面内",
        PresenceState.OUT_OF_VIEW: "老人不在画面内",
        PresenceState.STILL: "老人长时间无动作",
        PresenceState.FALLEN: "检测到老人摔倒",
    }.get(state, state.value)


__all__ = [
    "PresenceState",
    "PresenceFeature",
    "PresenceResult",
    "PresenceMonitor",
    "feature_from_frame",
    "safe_label",
    "describe",
    "SAFE_STATES",
    "PRIORITY",
]
