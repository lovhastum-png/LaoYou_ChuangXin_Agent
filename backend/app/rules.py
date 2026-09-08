from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time
from typing import Any
from zoneinfo import ZoneInfo


LOCAL_ZONE = ZoneInfo("Asia/Shanghai")


@dataclass(frozen=True)
class EventProposal:
    kind: str
    title: str
    severity: str
    description: str


def _minutes(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _clock(value: Any, default: str) -> time:
    try:
        hour, minute = str(value).split(":", 1)
        return time(int(hour), int(minute))
    except (TypeError, ValueError):
        hour, minute = default.split(":")
        return time(int(hour), int(minute))


def is_night(occurred_at: datetime, start: Any, end: Any) -> bool:
    """判断本地时间是否落在可跨午夜的夜间区间。"""
    local_time = occurred_at.astimezone(LOCAL_ZONE).time()
    start_time = _clock(start, "22:00")
    end_time = _clock(end, "06:00")
    if start_time <= end_time:
        return start_time <= local_time < end_time
    return local_time >= start_time or local_time < end_time


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def evaluate_observation(
    *,
    kind: str,
    value: Any,
    duration_minutes: int | None,
    sleeping: bool | None,
    occurred_at: datetime,
    rules: dict[str, Any],
) -> list[EventProposal]:
    """固定、可审计的演示规则；不代表医疗诊断。"""
    result: list[EventProposal] = []
    if kind == "fall":
        result.append(
            EventProposal("fall", "检测到跌倒", "critical", "收到跌倒观测，请尽快确认老人状态。")
        )
    elif kind == "wandering" and is_night(
        occurred_at, rules.get("night_start"), rules.get("night_end")
    ):
        result.append(
            EventProposal(
                "wandering",
                "夜间徘徊提醒",
                "warning",
                "在设定的夜间时段收到徘徊观测，请确认老人是否安全。",
            )
        )
    elif kind == "immobility":
        threshold = _minutes(
            rules.get("sleep_immobility_minutes") if sleeping else rules.get("immobility_minutes"),
            180 if sleeping else 60,
        )
        if duration_minutes is not None and duration_minutes >= threshold:
            label = "睡眠中长时间静止" if sleeping else "长时间静止"
            result.append(
                EventProposal(
                    "immobility",
                    label,
                    "critical" if not sleeping else "warning",
                    f"静止已达到 {duration_minutes} 分钟（演示阈值 {threshold} 分钟）。",
                )
            )
    elif kind == "away":
        threshold = _minutes(rules.get("away_minutes"), 120)
        if duration_minutes is not None and duration_minutes >= threshold:
            result.append(
                EventProposal(
                    "away",
                    "长时间未归",
                    "warning",
                    f"未归已达到 {duration_minutes} 分钟（演示阈值 {threshold} 分钟）。",
                )
            )
    elif kind == "heart_rate":
        number = _number(value)
        low = _number(rules.get("heart_rate_low"))
        high = _number(rules.get("heart_rate_high"))
        if number is not None and low is not None and high is not None and (number < low or number > high):
            result.append(
                EventProposal(
                    "heart_rate",
                    "心率异常提醒",
                    "critical",
                    f"观测心率 {number:g}，超出演示范围 {low:g}-{high:g}。",
                )
            )
    elif kind == "blood_pressure":
        if isinstance(value, dict):
            systolic = _number(value.get("systolic"))
            diastolic = _number(value.get("diastolic"))
            systolic_high = _number(rules.get("systolic_high"))
            diastolic_high = _number(rules.get("diastolic_high"))
            if (
                systolic is not None
                and diastolic is not None
                and systolic_high is not None
                and diastolic_high is not None
                and (systolic >= systolic_high or diastolic >= diastolic_high)
            ):
                result.append(
                    EventProposal(
                        "blood_pressure",
                        "血压异常提醒",
                        "critical",
                        f"观测血压 {systolic:g}/{diastolic:g}，达到演示提醒阈值。",
                    )
                )
    return result


ROUTINE_KIND_TO_KEY = {
    "wake": "wake_time",
    "lunch": "lunch_time",
    "dinner": "dinner_time",
    "sleep": "sleep_time",
}
