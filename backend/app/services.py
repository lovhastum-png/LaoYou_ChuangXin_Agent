from __future__ import annotations

import json
import logging
import re
import secrets
import urllib.parse
import urllib.request
from datetime import date, datetime, time, timedelta, timezone
from statistics import median
from typing import Any, Iterable
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session
from sqlalchemy import text as sql_text

from .config import Settings, load_settings
from .models import (
    AssistantToken,
    Broadcast,
    Call,
    DemoConfig,
    Elder,
    Escort,
    Event,
    Notification,
    Observation,
    Reminder,
    Timeline,
    User,
)
from .rules import EventProposal, ROUTINE_KIND_TO_KEY, evaluate_observation
from .security import token_digest, utcnow

logger = logging.getLogger(__name__)
LOCAL_ZONE = ZoneInfo("Asia/Shanghai")


def to_utc(value: datetime | None) -> datetime:
    if value is None:
        return utcnow()
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def response_utc(value: datetime | None) -> datetime | None:
    """将数据库返回的任意带时区值统一编码成 API 约定的 UTC。"""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def user_dict(user: User) -> dict[str, Any]:
    return {
        "id": user.id,
        "username": user.username,
        "display_name": user.display_name,
        "role": user.role,
    }


def elder_dict(elder: Elder) -> dict[str, Any]:
    return {
        "id": elder.id,
        "name": elder.name,
        "city": elder.city,
        "camera_enabled": elder.camera_enabled,
        "voice_enabled": elder.voice_enabled,
        "dialect": elder.dialect,
        "routine": dict(elder.routine or {}),
        "rules": dict(elder.rules or {}),
    }


def reminder_dict(reminder: Reminder) -> dict[str, Any]:
    return {
        "id": reminder.id,
        "elder_id": reminder.elder_id,
        "title": reminder.title,
        "medicine": reminder.medicine,
        "dose": reminder.dose,
        "time": reminder.time,
        "enabled": reminder.enabled,
        "created_at": response_utc(reminder.created_at),
    }


def broadcast_dict(broadcast: Broadcast) -> dict[str, Any]:
    return {
        "id": broadcast.id,
        "elder_id": broadcast.elder_id,
        "kind": broadcast.kind,
        "reminder_id": broadcast.reminder_id,
        "text": broadcast.text,
        "scheduled_at": response_utc(broadcast.scheduled_at),
        "played_at": response_utc(broadcast.played_at),
        "source": broadcast.source,
    }


def observation_dict(observation: Observation) -> dict[str, Any]:
    return {
        "id": observation.id,
        "elder_id": observation.elder_id,
        "kind": observation.kind,
        "value": observation.value,
        "duration_minutes": observation.duration_minutes,
        "sleeping": observation.sleeping,
        "occurred_at": response_utc(observation.occurred_at),
        "location": observation.location,
        "source": observation.source,
    }


def timeline_dict(item: Timeline) -> dict[str, Any]:
    return {
        "id": item.id,
        "node": item.node,
        "at": response_utc(item.at),
        "actor": item.actor,
        "detail": item.detail,
    }


def notification_dict(item: Notification) -> dict[str, Any]:
    return {
        "id": item.id,
        "event_id": item.event_id,
        "target": item.target,
        "status": item.status,
        "created_at": response_utc(item.created_at),
        "sent_at": response_utc(item.sent_at),
        "acknowledged_at": response_utc(item.acknowledged_at),
        "attempts": item.attempts,
        "last_error": item.last_error,
        "simulated": item.simulated,
    }


def escort_dict(item: Escort | None) -> dict[str, Any] | None:
    if item is None:
        return None
    return {
        "id": item.id,
        "event_id": item.event_id,
        "elder_id": item.elder_id,
        "platform": item.platform,
        "status": item.status,
        "requested_at": response_utc(item.requested_at),
        "accepted_at": response_utc(item.accepted_at),
        "completed_at": response_utc(item.completed_at),
        "note": item.note,
        "simulated": item.simulated,
    }


def event_dict(db: Session, event: Event, elder_name: str | None = None) -> dict[str, Any]:
    if elder_name is None:
        elder = db.get(Elder, event.elder_id)
        elder_name = elder.name if elder else ""
    return {
        "id": event.id,
        "elder_id": event.elder_id,
        "elder_name": elder_name,
        "kind": event.kind,
        "title": event.title,
        "severity": event.severity,
        "status": event.status,
        "source": event.source,
        "description": event.description,
        "created_at": response_utc(event.created_at),
        "updated_at": response_utc(event.updated_at),
    }


def event_detail_dict(db: Session, event: Event) -> dict[str, Any]:
    payload = event_dict(db, event)
    payload["timeline"] = [
        timeline_dict(item)
        for item in db.query(Timeline)
        .filter(Timeline.event_id == event.id)
        .order_by(Timeline.at, Timeline.id)
        .all()
    ]
    payload["notifications"] = [
        notification_dict(item)
        for item in db.query(Notification)
        .filter(Notification.event_id == event.id)
        .order_by(Notification.created_at, Notification.id)
        .all()
    ]
    payload["escort"] = escort_dict(
        db.query(Escort).filter(Escort.event_id == event.id).one_or_none()
    )
    return payload


def call_dict(item: Call) -> dict[str, Any]:
    return {
        "id": item.id,
        "elder_id": item.elder_id,
        "created_by": item.created_by,
        "status": item.status,
        "created_at": response_utc(item.created_at),
        "answered_at": response_utc(item.answered_at),
        "ended_at": response_utc(item.ended_at),
    }


def expire_stale_ringing_calls(db: Session, timeout_seconds: int, *, now: datetime | None = None) -> int:
    """把超时未接听的 ringing 通话置为 ended，返回清理条数。

    ringing 没有自然终结者：无人接听且两端都不显式挂断时会永久残留，
    而 create_call 的幂等去重会一直命中这条僵尸记录，导致该老人无法再
    发起新通话。调度周期清理之外，创建/列表路径也惰性调用本函数兜底。
    语义复用 ended（不新增状态），由 ended_at 与缺失的 answered_at 表达"未接通"。
    """
    moment = now or utcnow()
    cutoff = moment - timedelta(seconds=timeout_seconds)
    stale = db.query(Call).filter(Call.status == "ringing", Call.created_at < cutoff).all()
    for item in stale:
        item.status = "ended"
        item.ended_at = moment
    return len(stale)


def get_demo_fail_targets(db: Session) -> set[str]:
    config = db.get(DemoConfig, "notification_fail_targets")
    if not config or not isinstance(config.value, list):
        return set()
    return {str(item) for item in config.value}


def set_demo_fail_targets(db: Session, targets: Iterable[str]) -> list[str]:
    normalized = sorted(set(targets))
    config = db.get(DemoConfig, "notification_fail_targets")
    if config is None:
        db.add(DemoConfig(key="notification_fail_targets", value=normalized))
    else:
        config.value = normalized
    return normalized


def dispatch_notification(db: Session, item: Notification) -> Notification:
    """执行明确标注为模拟的适配器发送，并记录每次尝试。"""
    if item.status == "acknowledged":
        return item
    item.attempts += 1
    item.simulated = True
    if item.target in get_demo_fail_targets(db):
        item.status = "failed"
        item.last_error = f"模拟适配器故障：{item.target} 暂不可达"
        item.sent_at = None
        result = "failed"
    else:
        item.status = "sent"
        item.sent_at = utcnow()
        item.last_error = None
        result = "sent"
    add_timeline(
        db,
        item.event_id,
        f"notification_{result}",
        "system",
        f"通知目标 {item.target} 第 {item.attempts} 次尝试：{result}（模拟适配器）。",
    )
    return item


def notification_targets(kind: str) -> list[str]:
    targets = ["child"]
    if kind in {"fall", "immobility", "away"}:
        targets.append("community")
    elif kind in {"heart_rate", "blood_pressure"}:
        targets.extend(["community", "emergency"])
    return targets


def add_timeline(
    db: Session, event_id: str, node: str, actor: str, detail: str, at: datetime | None = None
) -> Timeline:
    item = Timeline(
        event_id=event_id,
        node=node,
        actor=actor,
        detail=detail,
        at=at or utcnow(),
    )
    db.add(item)
    db.flush()
    return item


def create_event_for_proposal(
    db: Session, elder: Elder, observation: Observation, proposal: EventProposal
) -> Event | None:
    dedupe_key = f"observation:{observation.id}:{proposal.kind}"
    existing = db.query(Event).filter(Event.dedupe_key == dedupe_key).one_or_none()
    if existing is not None:
        return existing
    event = Event(
        elder_id=elder.id,
        kind=proposal.kind,
        title=proposal.title,
        severity=proposal.severity,
        status="alerted",
        source=observation.source,
        description=proposal.description,
        dedupe_key=dedupe_key,
        created_at=utcnow(),
        updated_at=utcnow(),
    )
    db.add(event)
    db.flush()
    add_timeline(db, event.id, "alerted", "system", "规则命中，事件已创建。")
    for target in notification_targets(proposal.kind):
        notification = Notification(event_id=event.id, target=target, status="pending")
        db.add(notification)
        db.flush()
        dispatch_notification(db, notification)
    return event


def learn_routine(db: Session, elder: Elder, observation: Observation) -> None:
    key = ROUTINE_KIND_TO_KEY.get(observation.kind)
    if key is None:
        return
    recent = (
        db.query(Observation)
        .filter(Observation.elder_id == elder.id, Observation.kind == observation.kind)
        .order_by(Observation.occurred_at.desc())
        .limit(7)
        .all()
    )
    local_minutes = [
        item.occurred_at.astimezone(LOCAL_ZONE).hour * 60
        + item.occurred_at.astimezone(LOCAL_ZONE).minute
        for item in recent
    ]
    if not local_minutes:
        return
    midpoint = int(round(median(local_minutes))) % (24 * 60)
    learned = f"{midpoint // 60:02d}:{midpoint % 60:02d}"
    elder.routine = {**(elder.routine or {}), key: learned}


def add_observation(
    db: Session,
    elder: Elder,
    *,
    kind: str,
    value: Any,
    duration_minutes: int | None,
    sleeping: bool | None,
    occurred_at: datetime | None,
    location: dict[str, Any] | None,
    source: str,
    idempotency_key: str | None,
) -> tuple[Observation, list[Event]]:
    if idempotency_key:
        existing = (
            db.query(Observation)
            .filter(
                Observation.elder_id == elder.id,
                Observation.idempotency_key == idempotency_key,
            )
            .one_or_none()
        )
        if existing is not None:
            events = (
                db.query(Event)
                .filter(Event.dedupe_key.like(f"observation:{existing.id}:%"))
                .order_by(Event.created_at)
                .all()
            )
            return existing, events
    observation = Observation(
        elder_id=elder.id,
        kind=kind,
        value=value,
        duration_minutes=duration_minutes,
        sleeping=sleeping,
        occurred_at=to_utc(occurred_at),
        location=location,
        source=source,
        idempotency_key=idempotency_key,
        created_at=utcnow(),
    )
    db.add(observation)
    db.flush()
    proposals = evaluate_observation(
        kind=kind,
        value=value,
        duration_minutes=duration_minutes,
        sleeping=sleeping,
        occurred_at=observation.occurred_at,
        rules=elder.rules or {},
    )
    events = [
        event
        for proposal in proposals
        if (event := create_event_for_proposal(db, elder, observation, proposal)) is not None
    ]
    learn_routine(db, elder, observation)
    return observation, events


def _clock_minutes(text: str, default: str) -> int:
    try:
        hour, minute = text.split(":", 1)
        return int(hour) * 60 + int(minute)
    except (AttributeError, ValueError):
        hour, minute = default.split(":")
        return int(hour) * 60 + int(minute)


def local_schedule(day: date, hhmm: str) -> datetime:
    minutes = _clock_minutes(hhmm, "07:00")
    return datetime.combine(
        day, time(minutes // 60, minutes % 60), tzinfo=LOCAL_ZONE
    ).astimezone(timezone.utc)


def _add_broadcast(
    db: Session,
    *,
    elder: Elder,
    kind: str,
    text: str,
    scheduled_at: datetime,
    source: str,
    dedupe_key: str,
    reminder_id: str | None = None,
) -> Broadcast | None:
    if db.query(Broadcast).filter(Broadcast.dedupe_key == dedupe_key).one_or_none():
        return None
    broadcast = Broadcast(
        elder_id=elder.id,
        kind=kind,
        text=text,
        scheduled_at=scheduled_at,
        source=source,
        reminder_id=reminder_id,
        dedupe_key=dedupe_key,
        created_at=utcnow(),
    )
    db.add(broadcast)
    db.flush()
    return broadcast


def health_broadcast_text(db: Session, elder: Elder) -> str:
    """用最近观测生成日常提示；只陈述数据，不做医疗诊断或改药建议。"""
    latest_by_kind: dict[str, Observation] = {}
    for kind in ("heart_rate", "blood_pressure", "activity"):
        latest = (
            db.query(Observation)
            .filter(Observation.elder_id == elder.id, Observation.kind == kind)
            .order_by(Observation.occurred_at.desc())
            .first()
        )
        if latest is not None:
            latest_by_kind[kind] = latest
    if not latest_by_kind:
        return "健康提示：目前还没有心率、血压或活动观测，请按需要补充记录；这不是医疗诊断。"
    parts: list[str] = []
    for kind in ("heart_rate", "blood_pressure", "activity"):
        observation = latest_by_kind.get(kind)
        if observation is None:
            continue
        triggered = evaluate_observation(
            kind=observation.kind,
            value=observation.value,
            duration_minutes=observation.duration_minutes,
            sleeping=observation.sleeping,
            occurred_at=observation.occurred_at,
            rules=elder.rules or {},
        )
        if observation.kind == "heart_rate" and observation.value is not None:
            suffix = "，超出演示阈值，请家属核实" if triggered else "，在演示范围内"
            parts.append(f"最近心率 {observation.value} 次/分{suffix}")
        elif observation.kind == "blood_pressure" and isinstance(observation.value, dict):
            suffix = "，超出演示阈值，请家属核实" if triggered else "，请继续按需记录"
            parts.append(
                f"最近血压 {observation.value.get('systolic', '?')}/{observation.value.get('diastolic', '?')}{suffix}"
            )
        elif observation.kind == "activity" and observation.value is not None:
            parts.append(f"最近活动记录 {observation.value}")
    summary = "；".join(parts) if parts else "已有观测记录，但内容不足以生成摘要"
    return f"健康提示：{summary}。请结合自身感受安排活动，异常情况咨询专业人员；这不是医疗诊断。"


def weather_broadcast_text(weather: dict[str, Any]) -> str:
    if weather["source"] == "live":
        return f"{weather['city']}今日天气：{weather['temperature']}℃，{weather['description']}。{weather['advice']}"
    return f"{weather['city']}今日天气获取失败，实时天气服务暂不可用。{weather['advice']}"


def medication_broadcast_text(reminder: Reminder, planned: datetime, now: datetime) -> str:
    if planned < now.replace(second=0, microsecond=0):
        return f"原定 {reminder.time} 的{reminder.title}尚未播报，请核对用药安排，避免重复服用。"
    if reminder.dose:
        return f"{reminder.title}：请按您记录的剂量 {reminder.dose} 服用 {reminder.medicine}。"
    return f"{reminder.title}：到了您设置的用药提醒时间，请核对已确认的用药安排。"


def discard_pending_reminder_broadcasts(db: Session, reminder_id: str) -> None:
    db.query(Broadcast).filter(Broadcast.reminder_id == reminder_id, Broadcast.played_at.is_(None)).delete(synchronize_session=False)


def schedule_due_broadcasts(
    db: Session, at: datetime | None = None, app_settings: Settings | None = None
) -> int:
    """以数据库唯一键保证重启/重复轮询不会重复生成播报。"""
    now = to_utc(at)
    if app_settings is None:
        try:
            app_settings = load_settings()
        except RuntimeError:
            # Unit callers can still schedule deterministic non-weather items;
            # the running app always has DATABASE_URL and uses configured timeout.
            app_settings = Settings(database_url="", weather_timeout_seconds=3.0)
    local_now = now.astimezone(LOCAL_ZONE)
    today = local_now.date()
    minute_mark = local_now.replace(second=0, microsecond=0)
    # Dashboard polling and the background scheduler share this transaction lock.
    # A persisted checkpoint covers a missed minute without replaying old schedules
    # when a reminder is newly created or moved to an earlier time today.
    # SQLite（本地预览）无此函数，依赖唯一键保证幂等。
    if db.get_bind().dialect.name == "postgresql":
        db.execute(sql_text("SELECT pg_advisory_xact_lock(1279348569)"))
    checkpoint = db.get(DemoConfig, "scheduler_checkpoint")
    previous = to_utc(datetime.fromisoformat(checkpoint.value["at"])) if checkpoint else minute_mark.astimezone(timezone.utc)
    window_start = min(previous, minute_mark.astimezone(timezone.utc))

    def due(planned: datetime) -> bool:
        return window_start <= planned <= now

    created = 0
    elders = db.query(Elder).all()
    for elder in elders:
        routine = elder.routine or {}
        wake_time = str(routine.get("wake_time", "07:00"))
        wake_at = local_schedule(today, wake_time)
        weather_key = f"weather:{elder.id}:{today.isoformat()}"
        if due(wake_at) and db.query(Broadcast).filter(Broadcast.dedupe_key == weather_key).first() is None:
            weather = fetch_weather(elder, app_settings)
            if _add_broadcast(
                db,
                elder=elder,
                kind="weather",
                text=weather_broadcast_text(weather),
                scheduled_at=wake_at,
                source=weather["source"],
                dedupe_key=weather_key,
            ):
                created += 1
        health_times = (
            ("wake_time", "早间健康提示"),
            ("lunch_time", "午间健康提示"),
            ("dinner_time", "晚间健康提示"),
        )
        for routine_key, label in health_times:
            planned = local_schedule(today, str(routine.get(routine_key, "07:00")))
            if due(planned):
                if _add_broadcast(
                    db,
                    elder=elder,
                    kind="health",
                    text=f"{label}：{health_broadcast_text(db, elder)}",
                    scheduled_at=planned,
                    source="system",
                    dedupe_key=f"health:{elder.id}:{routine_key}:{today.isoformat()}",
                ):
                    created += 1
        reminders = (
            db.query(Reminder)
            .filter(Reminder.elder_id == elder.id, Reminder.enabled.is_(True))
            .all()
        )
        for reminder in reminders:
            planned = local_schedule(today, reminder.time)
            if due(planned) and planned >= reminder.created_at.replace(second=0, microsecond=0):
                if _add_broadcast(
                    db,
                    elder=elder,
                    kind="medication",
                    text=medication_broadcast_text(reminder, planned, now),
                    scheduled_at=planned,
                    source="system",
                    dedupe_key=f"medication:{elder.id}:{reminder.id}:{today.isoformat()}:{reminder.time}",
                    reminder_id=reminder.id,
                ):
                    created += 1
    if checkpoint is None:
        db.add(DemoConfig(key="scheduler_checkpoint", value={"at": now.isoformat()}))
    else:
        checkpoint.value = {"at": now.isoformat()}
    db.flush()
    return created


CITY_COORDS: dict[str, tuple[float, float]] = {
    "成都": (30.5728, 104.0668),
    "北京": (39.9042, 116.4074),
    "上海": (31.2304, 121.4737),
    "广州": (23.1291, 113.2644),
    "深圳": (22.5431, 114.0579),
    "重庆": (29.5630, 106.5516),
    "杭州": (30.2741, 120.1551),
}

WEATHER_DESCRIPTIONS = {
    0: "晴",
    1: "大部晴朗",
    2: "局部多云",
    3: "多云",
    45: "雾",
    48: "雾凇",
    51: "小毛毛雨",
    53: "毛毛雨",
    55: "较强毛毛雨",
    61: "小雨",
    63: "中雨",
    65: "大雨",
    71: "小雪",
    73: "中雪",
    75: "大雪",
    80: "阵雨",
    81: "中阵雨",
    82: "强阵雨",
    95: "雷雨",
}


def _simulated_weather(elder: Elder) -> dict[str, Any]:
    return {
        "city": elder.city,
        "temperature": None,
        "description": "天气服务暂不可用（演示降级）",
        "advice": "请以现场天气为准，出门前查看屏幕提示并适当添衣。",
        "observed_at": utcnow(),
        "source": "simulated",
    }


def fetch_weather(elder: Elder, app_settings: Settings) -> dict[str, Any]:
    """调用 Open-Meteo；任何超时、网络或解析失败都清楚降级为 simulated。"""
    coords = CITY_COORDS.get(elder.city)
    if coords is None:
        return _simulated_weather(elder)
    query = urllib.parse.urlencode(
        {
            "latitude": coords[0],
            "longitude": coords[1],
            "current": "temperature_2m,weather_code",
            "timezone": "Asia/Shanghai",
        }
    )
    url = f"https://api.open-meteo.com/v1/forecast?{query}"
    try:
        with urllib.request.urlopen(url, timeout=app_settings.weather_timeout_seconds) as response:
            raw = json.loads(response.read().decode("utf-8"))
        current = raw.get("current") or {}
        temperature = current.get("temperature_2m")
        code = int(current.get("weather_code"))
        description = WEATHER_DESCRIPTIONS.get(code, "天气变化")
        advice = "天气适宜，出门记得带好随身物品。"
        if temperature is not None and float(temperature) < 10:
            advice = "气温偏低，出门请添衣并注意保暖。"
        elif temperature is not None and float(temperature) > 30:
            advice = "气温较高，外出请避开高温并及时补水。"
        return {
            "city": elder.city,
            "temperature": temperature,
            "description": description,
            "advice": advice,
            "observed_at": utcnow(),
            "source": "live",
        }
    except Exception as exc:  # network is an optional integration boundary
        logger.info("天气实时服务降级为模拟：%s", exc)
        return _simulated_weather(elder)


def create_assistant_token(
    db: Session, user: User, elder: Elder, action: str, proposal: dict[str, Any]
) -> str:
    raw = secrets.token_urlsafe(24)
    db.add(
        AssistantToken(
            token_hash=token_digest(raw),
            user_id=user.id,
            elder_id=elder.id,
            action=action,
            proposal=proposal,
            expires_at=utcnow() + timedelta(minutes=10),
        )
    )
    return raw


_CHINESE_DIGITS = {
    "零": 0,
    "〇": 0,
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
    "十": 10,
}


def parse_chinese_number(text: str) -> int | None:
    if not text:
        return None
    if text.isdigit():
        return int(text)
    if text == "十":
        return 10
    if "十" in text:
        high, _, low = text.partition("十")
        high_value = _CHINESE_DIGITS.get(high, 1) if high else 1
        low_value = _CHINESE_DIGITS.get(low, 0) if low else 0
        return high_value * 10 + low_value
    if len(text) == 2 and all(char in _CHINESE_DIGITS for char in text):
        # Common clock phrasing such as “八点零五分”.
        return _CHINESE_DIGITS[text[0]] * 10 + _CHINESE_DIGITS[text[1]]
    if len(text) == 1:
        return _CHINESE_DIGITS.get(text)
    return None


def parse_hour(text: str) -> int | None:
    match = re.search(r"(\d{1,2})\s*点", text)
    if match:
        return int(match.group(1))
    match = re.search(r"([零〇一二两三四五六七八九十]{1,3})\s*点", text)
    if not match:
        return None
    return parse_chinese_number(match.group(1))


def _has_reminder_intent(normalized: str) -> bool:
    """判断这句话是不是在要求"定时做某事"。

    口语里老人不会说"提醒"两个字，会说"叫我吃药""几点吃药"。
    这里放宽为三类信号，任一成立即进入时间/药物解析：
      1. 显式提醒词：提醒、闹钟、定时、叫我、喊我
      2. 用药动词：吃、服、用药、服药
      3. 服药名词：药、片、胶囊、冲剂、口服液、汤药
    放宽只影响"是否尝试解析"，时间点仍由 parse_hour 严格校验，
    所以"每天提醒我吃降压药"这种缺时间的句子依旧返回 None。
    """
    if any(word in normalized for word in ("提醒", "闹钟", "定时", "叫我", "喊我")):
        return True
    if any(word in normalized for word in ("吃", "服", "用药", "服药")):
        return True
    return any(word in normalized for word in ("药", "片", "胶囊", "冲剂", "口服液", "汤药"))


def parse_reminder(text: str) -> dict[str, Any] | None:
    normalized = re.sub(r"\s+", "", text)
    if not _has_reminder_intent(normalized):
        return None
    hour = parse_hour(normalized)
    if hour is None:
        match = re.search(r"(?:每天)?\s*(\d{1,2}):(\d{2})", normalized)
        if match:
            hour = int(match.group(1))
            minute = int(match.group(2))
        else:
            return None
    else:
        minute = 0
        minute_match = re.search(r"点(\d{1,2})分", normalized)
        if minute_match:
            minute = int(minute_match.group(1))
        elif "点半" in normalized or "点一半" in normalized:
            minute = 30
        elif "点一刻" in normalized:
            minute = 15
        elif "点三刻" in normalized:
            minute = 45
        else:
            chinese_minute = re.search(r"点([零〇一二两三四五六七八九十]{1,3})(?:分)?", normalized)
            if chinese_minute:
                parsed = parse_chinese_number(chinese_minute.group(1))
                if parsed is None:
                    return None
                minute = parsed
    if "晚上" in normalized or "晚间" in normalized:
        if hour < 12:
            hour += 12
    elif "下午" in normalized and hour < 12:
        hour += 12
    if hour > 23 or minute > 59:
        return None
    # 药物名必须紧跟在用药动词之后，且只取一个词。
    # 原实现用 [^，。,\s。]+ 会一路吃到句尾，在"叫我吃药吃降压片"里
    # 会抓出"药吃降压片"。这里按动词切分后取最后一个候选——
    # "吃药"只是泛称，"吃降压片"才是药名。泛称（药/药物/片）要过滤掉，
    # 否则会把"吃药"当成药名。
    medicine = "药物"
    for match in re.finditer(r"(?:服用?|吃|喝)(?P<name>[^，。,.!？?\s。]+)", normalized):
        for piece in re.split(r"(?:服用?|吃|喝|提醒|叫我|记得|别忘)", match.group("name")):
            candidate = re.sub(r"^(?:个|点)+", "", piece).strip()
            if candidate and not re.fullmatch(r"药|药物|片|颗|粒|胶囊|冲剂|口服液|汤药", candidate):
                medicine = candidate
    return {
        "title": f"{medicine}提醒",
        "medicine": medicine,
        "dose": "",
        "time": f"{hour:02d}:{minute:02d}",
        "enabled": True,
    }
