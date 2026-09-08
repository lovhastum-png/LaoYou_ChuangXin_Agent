from __future__ import annotations

import asyncio
import logging
import re
from contextlib import asynccontextmanager
from datetime import datetime, time, timedelta, timezone
from email.utils import format_datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import (
    Depends,
    FastAPI,
    Header,
    HTTPException,
    Query,
    Request,
    Response,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .db import SessionLocal, check_database, engine, get_db, init_db, settings
from .models import (
    AssistantToken,
    AuthSession,
    Broadcast,
    Call,
    Elder,
    Escort,
    Event,
    Notification,
    Observation,
    Reminder,
    Snapshot,
    Timeline,
    User,
)
from .schemas import (
    AssistantRequest,
    CallAction,
    ElderSettingsPatch,
    EscortAction,
    EventAction,
    LoginRequest,
    NotificationConfig,
    ObservationCreate,
    ReminderCreate,
    ReminderPatch,
)
from .security import (
    get_current_user,
    issue_session,
    token_digest,
    utcnow,
    verify_password,
)
from .services import (
    add_observation,
    add_timeline,
    call_dict,
    create_assistant_token,
    dispatch_notification,
    elder_dict,
    event_detail_dict,
    event_dict,
    escort_dict,
    fetch_weather,
    health_broadcast_text,
    weather_broadcast_text,
    medication_broadcast_text,
    discard_pending_reminder_broadcasts,
    notification_dict,
    observation_dict,
    reminder_dict,
    schedule_due_broadcasts,
    set_demo_fail_targets,
    broadcast_dict,
    parse_reminder,
    user_dict,
)

logger = logging.getLogger("laoyou")
logging.basicConfig(level=logging.INFO)
LOCAL_ZONE = ZoneInfo("Asia/Shanghai")
VALID_DIALECTS = {"zh-CN", "yue-HK", "sichuan", "northeast"}
VALID_EVENT_STATUS = {"alerted", "acknowledged", "handling", "resolved", "false_positive"}
_TOKEN_QUERY_RE = re.compile(r"([?&]token=)[^&\s\"]+", re.IGNORECASE)


class _AccessTokenRedactor(logging.Filter):
    """保留 Uvicorn 访问状态，同时避免 URL 查询令牌进入日志。"""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, tuple):
            record.args = tuple(
                _TOKEN_QUERY_RE.sub(r"\1<redacted>", value) if isinstance(value, str) else value
                for value in record.args
            )
        elif isinstance(record.args, dict):
            record.args = {
                key: _TOKEN_QUERY_RE.sub(r"\1<redacted>", value) if isinstance(value, str) else value
                for key, value in record.args.items()
            }
        if isinstance(record.msg, str):
            record.msg = _TOKEN_QUERY_RE.sub(r"\1<redacted>", record.msg)
        return True


logging.getLogger("uvicorn.access").addFilter(_AccessTokenRedactor())
logging.getLogger("uvicorn.error").addFilter(_AccessTokenRedactor())


def _is_accessible(user: User, elder: Elder) -> bool:
    if user.role == "admin":
        return True
    if user.role == "elder":
        return user.elder_id == elder.id
    if user.role == "child":
        return bool(user.family_id and user.family_id == elder.family_id)
    if user.role == "community":
        return bool(user.community_id and user.community_id == elder.community_id)
    return False


def load_elder(
    db: Session,
    user: User,
    elder_id: str,
    *,
    forbidden_status: int = 404,
    lock: bool = False,
) -> Elder:
    if lock:
        elder = db.query(Elder).filter(Elder.id == elder_id).with_for_update().one_or_none()
    else:
        elder = db.get(Elder, elder_id)
    if elder is None:
        raise HTTPException(status_code=404, detail="老人不存在")
    if not _is_accessible(user, elder):
        raise HTTPException(status_code=forbidden_status, detail="无权访问该家庭数据")
    return elder


def require_non_elder(user: User) -> None:
    if user.role == "elder":
        raise HTTPException(status_code=403, detail="老人端不能处置事件")


def can_mutate_elder(user: User) -> bool:
    return user.role in {"elder", "child", "admin"}


def _event_query_for_user(db: Session, user: User):
    query = db.query(Event, Elder.name).join(Elder, Elder.id == Event.elder_id)
    if user.role == "admin":
        return query
    if user.role == "elder":
        return query.filter(Event.elder_id == user.elder_id)
    if user.role == "child":
        return query.filter(Elder.family_id == user.family_id)
    if user.role == "community":
        return query.filter(Elder.community_id == user.community_id)
    return query.filter(False)


def _notification_query_for_user(db: Session, user: User):
    query = db.query(Notification, Event, Elder).join(Event, Event.id == Notification.event_id).join(
        Elder, Elder.id == Event.elder_id
    )
    if user.role == "admin":
        return query
    if user.role == "child":
        return query.filter(Notification.target == "child", Elder.family_id == user.family_id)
    if user.role == "community":
        return query.filter(Notification.target == "community", Elder.community_id == user.community_id)
    return query.filter(False)


def _commit(db: Session) -> None:
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="数据冲突，请重试") from exc


class CallHub:
    def __init__(self) -> None:
        self.rooms: dict[str, set[WebSocket]] = {}
        self.lock = asyncio.Lock()

    async def join(self, call_id: str, websocket: WebSocket) -> bool:
        async with self.lock:
            room = self.rooms.setdefault(call_id, set())
            if len(room) >= 2:
                return False
            room.add(websocket)
            return True

    async def leave(self, call_id: str, websocket: WebSocket) -> None:
        async with self.lock:
            room = self.rooms.get(call_id)
            if room is None:
                return
            room.discard(websocket)
            if not room:
                self.rooms.pop(call_id, None)

    async def broadcast(self, call_id: str, sender: WebSocket, message: dict[str, Any]) -> None:
        async with self.lock:
            targets = list(self.rooms.get(call_id, set()))
        for target in targets:
            if target is sender:
                continue
            try:
                await target.send_json(message)
            except Exception:
                await self.leave(call_id, target)


call_hub = CallHub()


def _scheduler_tick() -> None:
    try:
        with SessionLocal.begin() as db:
            schedule_due_broadcasts(db, app_settings=settings)
    except Exception as exc:
        # A scheduler failure must not terminate the API process. The next
        # tick retries, and the unique key keeps successful ticks idempotent.
        logger.warning("播报调度本轮失败，将在下一轮重试：%s", exc)


async def _scheduler(stop: asyncio.Event) -> None:
    while not stop.is_set():
        await asyncio.to_thread(_scheduler_tick)
        try:
            await asyncio.wait_for(stop.wait(), timeout=5)
        except asyncio.TimeoutError:
            continue


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.scheduler_stop = asyncio.Event()
    init_db()
    task = asyncio.create_task(_scheduler(app.state.scheduler_stop))
    app.state.scheduler_task = task
    try:
        yield
    finally:
        app.state.scheduler_stop.set()
        await task
        engine.dispose()


app = FastAPI(title="老友后端", version="1.0.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
def health() -> dict[str, str]:
    if not check_database():
        raise HTTPException(status_code=503, detail="数据库不可用")
    return {"status": "ok", "database": "ok"}


@app.post("/api/auth/login")
def login(payload: LoginRequest, db: Session = Depends(get_db)) -> dict[str, Any]:
    user = db.query(User).filter(User.username == payload.username).one_or_none()
    if user is None or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    token = issue_session(db, user)
    _commit(db)
    return {"token": token, "user": user_dict(user)}


@app.post("/api/auth/logout")
def logout(
    authorization: str | None = Header(default=None),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, bool]:
    del user  # dependency performs authentication; token is revoked below.
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="需要登录")
    session = (
        db.query(AuthSession)
        .filter(AuthSession.token_hash == token_digest(authorization[7:].strip()))
        .one_or_none()
    )
    if session:
        session.revoked_at = utcnow()
    _commit(db)
    return {"ok": True}


@app.get("/api/auth/me")
def me(user: User = Depends(get_current_user)) -> dict[str, Any]:
    return user_dict(user)


@app.get("/api/elders")
def list_elders(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    elders = db.query(Elder).order_by(Elder.name).all()
    return [elder_dict(item) for item in elders if _is_accessible(user, item)][:200]


@app.patch("/api/elders/{elder_id}/settings")
def update_elder_settings(
    elder_id: str,
    payload: ElderSettingsPatch,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    elder = load_elder(db, user, elder_id, forbidden_status=403)
    if not can_mutate_elder(user):
        raise HTTPException(status_code=403, detail="社区仅可查看设置")
    if payload.dialect is not None and payload.dialect not in VALID_DIALECTS:
        raise HTTPException(status_code=422, detail="不支持的方言标识")
    if payload.camera_enabled is False and elder.camera_enabled and not payload.confirm_camera_off:
        raise HTTPException(status_code=422, detail="关闭摄像头必须二次确认")
    if payload.camera_enabled is not None:
        elder.camera_enabled = payload.camera_enabled
        if not payload.camera_enabled:
            snapshot = db.get(Snapshot, elder.id)
            if snapshot is not None:
                db.delete(snapshot)
    if payload.voice_enabled is not None:
        elder.voice_enabled = payload.voice_enabled
    if payload.dialect is not None:
        elder.dialect = payload.dialect
    if payload.city is not None:
        elder.city = payload.city.strip()
    if payload.routine is not None:
        elder.routine = payload.routine.model_dump()
    if payload.rules is not None:
        elder.rules = payload.rules.model_dump()
    elder.updated_at = utcnow()
    _commit(db)
    return elder_dict(elder)


@app.get("/api/elders/{elder_id}/dashboard")
def dashboard(
    elder_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> dict[str, Any]:
    elder = load_elder(db, user, elder_id)
    schedule_due_broadcasts(db, app_settings=settings)
    db.commit()
    today_local = datetime.now(timezone.utc).astimezone(LOCAL_ZONE).date()
    day_start = datetime.combine(today_local, time.min, tzinfo=LOCAL_ZONE).astimezone(timezone.utc)
    day_end = day_start + timedelta(days=1)
    broadcasts = (
        db.query(Broadcast)
        .filter(Broadcast.elder_id == elder.id, Broadcast.scheduled_at >= day_start, Broadcast.scheduled_at < day_end)
        .order_by(Broadcast.played_at.is_(None).desc(), Broadcast.scheduled_at)
        .limit(200)
        .all()
    )
    latest = (
        db.query(Observation)
        .filter(Observation.elder_id == elder.id)
        .order_by(Observation.occurred_at.desc())
        .first()
    )
    active_count = (
        db.query(Event)
        .filter(Event.elder_id == elder.id, Event.status.in_(["alerted", "acknowledged", "handling"]))
        .count()
    )
    return {
        "elder": elder_dict(elder),
        "weather": fetch_weather(elder, settings),
        "reminders": [
            reminder_dict(item)
            for item in db.query(Reminder).filter(Reminder.elder_id == elder.id).order_by(Reminder.time).all()
        ],
        "broadcasts": [broadcast_dict(item) for item in broadcasts],
        "latest_observation": observation_dict(latest) if latest else None,
        "active_event_count": active_count,
    }


@app.get("/api/elders/{elder_id}/reminders")
def list_reminders(
    elder_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> list[dict[str, Any]]:
    elder = load_elder(db, user, elder_id)
    return [
        reminder_dict(item)
        for item in db.query(Reminder).filter(Reminder.elder_id == elder.id).order_by(Reminder.time, Reminder.created_at).limit(200).all()
    ]


@app.post("/api/elders/{elder_id}/reminders")
def create_reminder(
    elder_id: str,
    payload: ReminderCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    elder = load_elder(db, user, elder_id, forbidden_status=403)
    if not can_mutate_elder(user):
        raise HTTPException(status_code=403, detail="社区不能修改提醒")
    reminder = Reminder(elder_id=elder.id, **payload.model_dump())
    db.add(reminder)
    db.flush()
    _commit(db)
    return reminder_dict(reminder)


def _get_reminder(db: Session, user: User, reminder_id: str, *, mutate: bool = False) -> Reminder:
    reminder = db.get(Reminder, reminder_id)
    if reminder is None:
        raise HTTPException(status_code=404, detail="提醒不存在")
    load_elder(db, user, reminder.elder_id, forbidden_status=403 if mutate else 404)
    if mutate and not can_mutate_elder(user):
        raise HTTPException(status_code=403, detail="无权修改提醒")
    return reminder


@app.patch("/api/reminders/{reminder_id}")
def update_reminder(
    reminder_id: str,
    payload: ReminderPatch,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    reminder = _get_reminder(db, user, reminder_id, mutate=True)
    old_time = reminder.time
    for key, value in payload.model_dump(exclude_unset=True).items():
        if value is not None:
            setattr(reminder, key, value)
    if not reminder.enabled or reminder.time != old_time:
        discard_pending_reminder_broadcasts(db, reminder.id)
    else:
        pending = db.query(Broadcast).filter(Broadcast.reminder_id == reminder.id, Broadcast.played_at.is_(None)).all()
        for item in pending:
            item.text = medication_broadcast_text(reminder, item.scheduled_at, utcnow())
    _commit(db)
    return reminder_dict(reminder)


@app.delete("/api/reminders/{reminder_id}")
def delete_reminder(
    reminder_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> dict[str, bool]:
    reminder = _get_reminder(db, user, reminder_id, mutate=True)
    discard_pending_reminder_broadcasts(db, reminder.id)
    db.delete(reminder)
    _commit(db)
    return {"ok": True}


@app.get("/api/elders/{elder_id}/broadcasts")
def list_broadcasts(
    elder_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> list[dict[str, Any]]:
    elder = load_elder(db, user, elder_id)
    schedule_due_broadcasts(db, app_settings=settings)
    db.commit()
    local_date = datetime.now(timezone.utc).astimezone(LOCAL_ZONE).date()
    start = datetime.combine(local_date, time.min, tzinfo=LOCAL_ZONE).astimezone(timezone.utc)
    end = start + timedelta(days=1)
    items = (
        db.query(Broadcast)
        .filter(Broadcast.elder_id == elder.id, Broadcast.scheduled_at >= start, Broadcast.scheduled_at < end)
        .order_by(Broadcast.played_at.is_(None).desc(), Broadcast.scheduled_at, Broadcast.id)
        .limit(200)
        .all()
    )
    return [broadcast_dict(item) for item in items]


@app.post("/api/broadcasts/{broadcast_id}/played")
def mark_broadcast_played(
    broadcast_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> dict[str, Any]:
    item = db.get(Broadcast, broadcast_id)
    if item is None:
        raise HTTPException(status_code=404, detail="播报不存在")
    elder = load_elder(db, user, item.elder_id, forbidden_status=403)
    if user.role not in {"elder", "child", "admin"}:
        raise HTTPException(status_code=403, detail="无权确认播报")
    if item.played_at is None:
        item.played_at = utcnow()
        _commit(db)
    return broadcast_dict(item)


@app.get("/api/elders/{elder_id}/observations")
def list_observations(
    elder_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> list[dict[str, Any]]:
    elder = load_elder(db, user, elder_id)
    items = (
        db.query(Observation)
        .filter(Observation.elder_id == elder.id)
        .order_by(Observation.occurred_at.desc())
        .limit(100)
        .all()
    )
    return [observation_dict(item) for item in items]


def _validate_observation_input(elder: Elder, payload: ObservationCreate) -> None:
    source = payload.source.strip().lower()
    if source == "live" or source.startswith("live_"):
        raise HTTPException(status_code=422, detail="未接入真实设备，不能伪造 live 数据")
    if source.startswith("camera") and not elder.camera_enabled:
        raise HTTPException(status_code=409, detail="摄像头已关闭，不能接收监控推断")
    if payload.kind == "heart_rate":
        if not isinstance(payload.value, (int, float)) or isinstance(payload.value, bool) or not 0 < payload.value <= 300:
            raise HTTPException(status_code=422, detail="心率必须是 1-300 的数值")
    if payload.kind == "blood_pressure":
        value = payload.value
        if not isinstance(value, dict) or not isinstance(value.get("systolic"), (int, float)) or not isinstance(value.get("diastolic"), (int, float)):
            raise HTTPException(status_code=422, detail="血压必须包含 systolic 和 diastolic 数值")
        if not (0 < float(value["systolic"]) <= 400 and 0 < float(value["diastolic"]) <= 300):
            raise HTTPException(status_code=422, detail="血压数值超出范围")
    if payload.location is not None:
        latitude = payload.location.get("latitude")
        longitude = payload.location.get("longitude")
        if not isinstance(latitude, (int, float)) or not isinstance(longitude, (int, float)):
            raise HTTPException(status_code=422, detail="位置必须包含 latitude 和 longitude")
        if not (-90 <= float(latitude) <= 90 and -180 <= float(longitude) <= 180):
            raise HTTPException(status_code=422, detail="位置坐标超出范围")


@app.post("/api/elders/{elder_id}/observations")
def create_observation(
    elder_id: str,
    payload: ObservationCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    elder = load_elder(db, user, elder_id, forbidden_status=403)
    if user.role not in {"child", "admin"}:
        raise HTTPException(status_code=403, detail="只有子女或管理员可以注入演示观测")
    _validate_observation_input(elder, payload)
    observation_data = payload.model_dump()
    observation_data["source"] = observation_data["source"].strip()
    observation, events = add_observation(db, elder, **observation_data)
    _commit(db)
    return {
        "observation": observation_dict(observation),
        "events": [event_dict(db, item) for item in events],
    }


@app.get("/api/events")
def list_events(
    elder_id: str | None = Query(default=None),
    event_status: str | None = Query(default=None, alias="status"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    query = _event_query_for_user(db, user)
    if elder_id:
        query = query.filter(Event.elder_id == elder_id)
    if event_status:
        if event_status not in VALID_EVENT_STATUS:
            raise HTTPException(status_code=422, detail="事件状态无效")
        query = query.filter(Event.status == event_status)
    rows = query.order_by(Event.created_at.desc()).limit(200).all()
    return [event_dict(db, event, elder_name) for event, elder_name in rows]


def _get_visible_event(db: Session, user: User, event_id: str, *, lock: bool = False) -> Event:
    query = _event_query_for_user(db, user).filter(Event.id == event_id)
    if lock:
        query = query.with_for_update()
    row = query.one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="事件不存在或不可见")
    return row[0]


@app.get("/api/events/{event_id}")
def event_detail(
    event_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> dict[str, Any]:
    return event_detail_dict(db, _get_visible_event(db, user, event_id))


@app.post("/api/events/{event_id}/actions")
def event_action(
    event_id: str,
    payload: EventAction,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    event = _get_visible_event(db, user, event_id, lock=True)
    require_non_elder(user)
    action = payload.action
    note = (payload.note or "").strip()
    # Check action role before the idempotent fast path. A role without
    # permission must not receive a successful response for a prior action.
    if action == "correct" and user.role not in {"child", "admin"}:
        raise HTTPException(status_code=403, detail="只有子女或管理员可以修正误报")
    if action == "community_unavailable" and user.role not in {"community", "admin"}:
        raise HTTPException(status_code=403, detail="只有社区或管理员可以标记社区无法协助")
    timeline_items = db.query(Timeline).filter(Timeline.event_id == event.id).all()
    prior_nodes = {item.node for item in timeline_items}
    if (action == "correct" and "correct" in prior_nodes) or action in prior_nodes:
        return event_detail_dict(db, event)
    if action == "acknowledge":
        if event.status != "alerted":
            raise HTTPException(status_code=409, detail="只能确认 alerted 事件")
        event.status = "acknowledged"
        add_timeline(db, event.id, "acknowledge", user.role, "事件已确认。")
    elif action == "start":
        if event.status != "acknowledged":
            raise HTTPException(status_code=409, detail="只能从 acknowledged 开始处理")
        event.status = "handling"
        add_timeline(db, event.id, "start", user.role, "开始处理事件。")
    elif action == "resolve":
        if event.status != "handling":
            raise HTTPException(status_code=409, detail="只能从 handling 结束事件")
        if not note:
            raise HTTPException(status_code=422, detail="resolve 必须填写处理说明")
        event.status = "resolved"
        add_timeline(db, event.id, "resolve", user.role, note)
    elif action == "correct":
        if not note:
            raise HTTPException(status_code=422, detail="修正误报必须填写原因")
        if event.status == "false_positive":
            return event_detail_dict(db, event)
        event.status = "false_positive"
        add_timeline(db, event.id, "correct", user.role, f"误报修正原因：{note}")
    elif action == "community_unavailable":
        if event.kind not in {"heart_rate", "blood_pressure"}:
            raise HTTPException(status_code=409, detail="只有医疗异常事件可以创建陪诊")
        if event.status in {"resolved", "false_positive"}:
            raise HTTPException(status_code=409, detail="事件已结束")
        if not note:
            raise HTTPException(status_code=422, detail="必须填写社区无法协助说明")
        escort = db.query(Escort).filter(Escort.event_id == event.id).one_or_none()
        if escort is None:
            escort = Escort(event_id=event.id, elder_id=event.elder_id, platform="放心医", simulated=True)
            db.add(escort)
            db.flush()
        add_timeline(db, event.id, "community_unavailable", user.role, note)
        add_timeline(db, event.id, "escort_requested", "system", "已创建模拟放心医陪诊工单。")
    event.updated_at = utcnow()
    _commit(db)
    return event_detail_dict(db, event)


@app.get("/api/notifications")
def list_notifications(
    user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> list[dict[str, Any]]:
    rows = _notification_query_for_user(db, user).order_by(Notification.created_at.desc()).limit(200).all()
    return [notification_dict(item) for item, _, _ in rows]


def _notification_for_action(db: Session, user: User, notification_id: str) -> Notification:
    item = (
        db.query(Notification)
        .filter(Notification.id == notification_id)
        .with_for_update()
        .one_or_none()
    )
    if item is None:
        raise HTTPException(status_code=404, detail="通知不存在")
    event = db.get(Event, item.event_id)
    elder = db.get(Elder, event.elder_id) if event else None
    if event is None or elder is None or not _is_accessible(user, elder):
        raise HTTPException(status_code=404, detail="通知不存在或不可见")
    if user.role != "admin" and item.target != user.role:
        raise HTTPException(status_code=403, detail="不能确认此类通知")
    return item


@app.post("/api/notifications/{notification_id}/ack")
def ack_notification(
    notification_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> dict[str, Any]:
    item = _notification_for_action(db, user, notification_id)
    if item.status == "acknowledged":
        return notification_dict(item)
    if item.status != "sent":
        raise HTTPException(status_code=409, detail="只有已送达通知可以确认")
    item.status = "acknowledged"
    item.acknowledged_at = utcnow()
    event = db.get(Event, item.event_id)
    if event is not None:
        event.updated_at = utcnow()
    add_timeline(
        db,
        item.event_id,
        "notification_ack",
        user.role,
        f"通知目标 {item.target} 已确认。",
    )
    _commit(db)
    return notification_dict(item)


@app.post("/api/notifications/{notification_id}/retry")
def retry_notification(
    notification_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> dict[str, Any]:
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="只有管理员可以重试通知")
    item = db.query(Notification).filter(Notification.id == notification_id).with_for_update().one_or_none()
    if item is None:
        raise HTTPException(status_code=404, detail="通知不存在")
    if item.status != "failed":
        raise HTTPException(status_code=409, detail="只有失败通知可以重试")
    dispatch_notification(db, item)
    _commit(db)
    return notification_dict(item)


@app.patch("/api/demo/notifications")
def update_demo_notifications(
    payload: NotificationConfig,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, list[str]]:
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="只有管理员可以配置模拟通知")
    targets = set_demo_fail_targets(db, payload.fail_targets)
    _commit(db)
    return {"fail_targets": targets}


@app.get("/api/escorts")
def list_escorts(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    query = db.query(Escort, Elder).join(Elder, Elder.id == Escort.elder_id)
    if user.role == "admin":
        pass
    elif user.role == "community":
        query = query.filter(Elder.community_id == user.community_id)
    elif user.role == "child":
        query = query.filter(Elder.family_id == user.family_id)
    elif user.role == "elder":
        query = query.filter(Elder.id == user.elder_id)
    else:
        query = query.filter(False)
    return [escort_dict(escort) for escort, _ in query.order_by(Escort.requested_at.desc()).limit(200).all()]


@app.post("/api/escorts/{escort_id}/actions")
def escort_action(
    escort_id: str,
    payload: EscortAction,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    if user.role not in {"community", "admin"}:
        raise HTTPException(status_code=403, detail="只有社区或管理员可以处置陪诊工单")
    escort = (
        db.query(Escort)
        .filter(Escort.id == escort_id)
        .with_for_update()
        .one_or_none()
    )
    if escort is None:
        raise HTTPException(status_code=404, detail="陪诊工单不存在")
    elder = load_elder(db, user, escort.elder_id, forbidden_status=404)
    note = (payload.note or "").strip()
    if payload.action == "accept":
        if escort.status == "accepted":
            return escort_dict(escort)  # type: ignore[return-value]
        if escort.status != "requested":
            raise HTTPException(status_code=409, detail="只能接受 requested 工单")
        escort.status = "accepted"
        escort.accepted_at = utcnow()
        if note:
            escort.note = note
        event = db.get(Event, escort.event_id)
        if event:
            add_timeline(db, event.id, "escort_accepted", user.role, note or "社区已接受陪诊工单。")
    else:
        if escort.status == "completed":
            return escort_dict(escort)  # type: ignore[return-value]
        if escort.status != "accepted":
            raise HTTPException(status_code=409, detail="只能完成 accepted 工单")
        if not note:
            raise HTTPException(status_code=422, detail="完成陪诊必须填写说明")
        escort.status = "completed"
        escort.completed_at = utcnow()
        escort.note = note
        event = db.get(Event, escort.event_id)
        if event:
            add_timeline(db, event.id, "escort_completed", user.role, note)
    _commit(db)
    return escort_dict(escort)  # type: ignore[return-value]


@app.post("/api/elders/{elder_id}/assistant")
def assistant(
    elder_id: str,
    payload: AssistantRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    elder = load_elder(db, user, elder_id, forbidden_status=403)
    if not can_mutate_elder(user):
        raise HTTPException(status_code=403, detail="当前身份不能使用老人指令助手")
    text = re.sub(r"\s+", "", payload.text.strip())
    mode = "local_rules"
    if payload.confirm_token:
        if text not in {"确认", "确认。", "确认！"}:
            raise HTTPException(status_code=409, detail="请明确说确认后再执行")
        token = (
            db.query(AssistantToken)
            .filter(
                AssistantToken.token_hash == token_digest(payload.confirm_token),
                AssistantToken.user_id == user.id,
                AssistantToken.elder_id == elder.id,
                AssistantToken.used_at.is_(None),
            )
            .with_for_update()
            .one_or_none()
        )
        if token is None or token.expires_at <= utcnow():
            raise HTTPException(status_code=409, detail="确认令牌无效或已过期")
        token.used_at = utcnow()
        proposal = dict(token.proposal)
        if token.action == "reminder_create":
            reminder = Reminder(elder_id=elder.id, **proposal)
            db.add(reminder)
            db.flush()
            _commit(db)
            return {
                "reply": f"好的，已创建每天 {reminder.time} 的 {reminder.medicine} 提醒。",
                "mode": mode,
                "action": "reminder_created",
                "proposal": reminder_dict(reminder),
                "confirm_token": None,
            }
        if token.action == "camera_off":
            elder.camera_enabled = False
            elder.updated_at = utcnow()
            snapshot = db.get(Snapshot, elder.id)
            if snapshot:
                db.delete(snapshot)
            _commit(db)
            return {
                "reply": "好的，摄像头已关闭。",
                "mode": mode,
                "action": "camera_updated",
                "proposal": {"camera_enabled": False},
                "confirm_token": None,
            }
        raise HTTPException(status_code=409, detail="确认操作类型无效")

    if any(word in text for word in ("关闭摄像头", "关掉摄像头", "停止摄像头")):
        proposal = {"camera_enabled": False}
        token = create_assistant_token(db, user, elder, "camera_off", proposal)
        _commit(db)
        return {
            "reply": "关闭摄像头会停止监控采集并清除最近画面。请说‘确认’继续。",
            "mode": mode,
            "action": "camera_confirm",
            "proposal": proposal,
            "confirm_token": token,
        }
    reminder_proposal = parse_reminder(text)
    if reminder_proposal:
        token = create_assistant_token(db, user, elder, "reminder_create", reminder_proposal)
        _commit(db)
        return {
            "reply": f"我准备每天 {reminder_proposal['time']} 提醒你服用 {reminder_proposal['medicine']}。剂量尚未填写，请说‘确认’先创建提醒，之后可在提醒设置中补充。",
            "mode": mode,
            "action": "reminder_proposal",
            "proposal": reminder_proposal,
            "confirm_token": token,
        }
    if "视频" in text and ("通话" in text or "打电话" in text):
        if user.role not in {"elder", "child", "admin"}:
            raise HTTPException(status_code=403, detail="当前身份不能发起视频通话")
        # Serialize the active-call check so a double click cannot create two
        # ringing calls for the same elder.
        elder = load_elder(db, user, elder_id, forbidden_status=403, lock=True)
        active = db.query(Call).filter(Call.elder_id == elder.id, Call.status.in_(["ringing", "active"])).first()
        if active is None:
            active = Call(elder_id=elder.id, created_by=user.id, status="ringing")
            db.add(active)
            db.flush()
            _commit(db)
        return {
            "reply": "视频通话已发起，请等待另一端接听。",
            "mode": mode,
            "action": "call",
            "proposal": {"call_id": active.id},
            "confirm_token": None,
        }
    if "天气" in text:
        weather = fetch_weather(elder, settings)
        return {
            "reply": weather_broadcast_text(weather),
            "mode": mode,
            "action": "weather",
            "proposal": weather,
            "confirm_token": None,
        }
    if "健康" in text or "身体" in text:
        latest = db.query(Observation).filter(Observation.elder_id == elder.id).order_by(Observation.occurred_at.desc()).first()
        summary = health_broadcast_text(db, elder)
        return {
            "reply": summary,
            "mode": mode,
            "action": "health",
            "proposal": {
                "summary": summary,
                "latest_observation": observation_dict(latest) if latest else None,
            },
            "confirm_token": None,
        }
    if "提醒" in text and ("查询" in text or "看看" in text or "有哪些" in text):
        reminders = db.query(Reminder).filter(Reminder.elder_id == elder.id).order_by(Reminder.time).all()
        return {
            "reply": f"目前有 {len(reminders)} 条提醒。",
            "mode": mode,
            "action": "none",
            "proposal": {"reminders": [reminder_dict(item) for item in reminders]},
            "confirm_token": None,
        }
    if any(word in text for word in ("你好通通", "你好", "通通")):
        reply = "你好，我是通通。我可以帮你查天气、看健康提示、设置提醒或发起视频通话。"
    else:
        reply = "我目前支持查天气、健康提示、查询或设置提醒、视频通话和关闭摄像头。"
    return {"reply": reply, "mode": mode, "action": "none", "proposal": None, "confirm_token": None}


@app.post("/api/elders/{elder_id}/speech")
async def transcribe_speech(
    elder_id: str,
    request: Request,
    dialect: str | None = Query(default=None),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """讯飞语音边界：只接收 16k/16bit/单声道原始 PCM，结果仍需由本地助手解释。"""
    elder = load_elder(db, user, elder_id, forbidden_status=403)
    if user.role not in {"elder", "child", "admin"}:
        raise HTTPException(status_code=403, detail="当前身份不能使用语音入口")
    content_type = request.headers.get("content-type", "").lower()
    if not content_type.startswith("audio/l16"):
        raise HTTPException(status_code=422, detail="语音必须是 audio/L16 原始 PCM")
    audio = await request.body()
    # 16,000 samples/s × 2 bytes/sample × 30s; one channel.
    if len(audio) < 3_200 or len(audio) > 960_000 or len(audio) % 2:
        raise HTTPException(status_code=422, detail="语音长度必须为 0.1-30 秒（3200-960000 字节）且为偶数")
    requested_dialect = dialect or elder.dialect or "zh-CN"
    if requested_dialect not in VALID_DIALECTS:
        raise HTTPException(status_code=422, detail="不支持的方言标识")
    try:
        from .integrations.xfyun import SpeechServiceError, transcribe_pcm
    except ImportError as exc:
        raise HTTPException(status_code=503, detail="讯飞语音适配器未安装或未配置") from exc
    try:
        text = await transcribe_pcm(audio=audio, dialect=requested_dialect)
    except SpeechServiceError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    except Exception as exc:
        logger.warning("讯飞语音识别失败：%s", exc)
        raise HTTPException(status_code=502, detail="讯飞语音服务暂不可用") from exc
    return {"text": text, "dialect": requested_dialect, "provider": "xfyun"}


@app.get("/api/capabilities")
def capabilities() -> dict[str, Any]:
    try:
        from .integrations.xfyun import is_configured

        xfyun_configured = is_configured()
    except ImportError:
        xfyun_configured = False
    return {
        "assistant_mode": "local_rules",
        "speech": {
            "provider": "xfyun" if xfyun_configured else "browser",
            "configured": xfyun_configured,
            "dialects": [
                {"id": "zh-CN", "label": "普通话", "available": True, "note": "浏览器系统语音/文字入口。"},
                {"id": "yue-HK", "label": "粤语", "available": xfyun_configured, "note": "需配置讯飞 ASR。" if not xfyun_configured else "已配置讯飞 ASR。"},
                {"id": "sichuan", "label": "四川话", "available": xfyun_configured, "note": "未配置讯飞方言识别服务，请使用文字入口。" if not xfyun_configured else "已配置讯飞方言域；账号需另行开通授权，实际可用性以服务返回为准。"},
                {"id": "northeast", "label": "东北话", "available": xfyun_configured, "note": "未配置讯飞方言识别服务，请使用文字入口。" if not xfyun_configured else "已配置讯飞方言域；账号需另行开通授权，实际可用性以服务返回为准。"},
            ],
        },
        "video": {"mode": "webrtc_signaling"},
        "integrations": {
            "camera": "browser_snapshot",
            "wearable": "simulated",
            "emergency": "simulated",
            "escort": "simulated",
        },
    }


def _get_call_for_user(db: Session, user: User, call_id: str, *, hide: bool = True) -> Call:
    call = db.get(Call, call_id)
    if call is None:
        raise HTTPException(status_code=404, detail="通话不存在")
    elder = db.get(Elder, call.elder_id)
    if elder is None or not _is_accessible(user, elder) or user.role not in {"elder", "child", "admin"}:
        raise HTTPException(status_code=404 if hide else 403, detail="通话不存在或不可见")
    return call


@app.post("/api/elders/{elder_id}/calls")
def create_call(
    elder_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> dict[str, Any]:
    elder = load_elder(db, user, elder_id, forbidden_status=403, lock=True)
    if user.role not in {"elder", "child", "admin"}:
        raise HTTPException(status_code=403, detail="只有老人或子女可以发起通话")
    active = db.query(Call).filter(Call.elder_id == elder.id, Call.status.in_(["ringing", "active"])).first()
    if active:
        return call_dict(active)
    item = Call(elder_id=elder.id, created_by=user.id, status="ringing")
    db.add(item)
    db.flush()
    _commit(db)
    return call_dict(item)


@app.get("/api/elders/{elder_id}/calls")
def list_calls(
    elder_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> list[dict[str, Any]]:
    elder = load_elder(db, user, elder_id)
    return [
        call_dict(item)
        for item in db.query(Call).filter(Call.elder_id == elder.id).order_by(Call.created_at.desc()).limit(200).all()
    ]


@app.post("/api/calls/{call_id}/actions")
def call_action(
    call_id: str,
    payload: CallAction,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    item = _get_call_for_user(db, user, call_id, hide=False)
    now = utcnow()
    if payload.action == "answer":
        if item.status == "active":
            return call_dict(item)
        if item.status != "ringing":
            raise HTTPException(status_code=409, detail="只能接听 ringing 通话")
        item.status = "active"
        item.answered_at = now
    elif payload.action == "end":
        if item.status == "ended":
            return call_dict(item)
        if item.status not in {"ringing", "active"}:
            raise HTTPException(status_code=409, detail="通话已结束或拒绝")
        item.status = "ended"
        item.ended_at = now
    else:
        if item.status == "declined":
            return call_dict(item)
        if item.status != "ringing":
            raise HTTPException(status_code=409, detail="只能拒绝 ringing 通话")
        item.status = "declined"
        item.ended_at = now
    _commit(db)
    return call_dict(item)


@app.get("/api/calls/{call_id}")
def get_call(
    call_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> dict[str, Any]:
    return call_dict(_get_call_for_user(db, user, call_id, hide=True))


async def calls_ws(websocket: WebSocket, call_id: str, token: str | None) -> None:
    if not token:
        await websocket.close(code=4401)
        return
    db = SessionLocal()
    try:
        session = db.query(AuthSession).filter(AuthSession.token_hash == token_digest(token), AuthSession.revoked_at.is_(None)).one_or_none()
        if session is None or session.expires_at <= utcnow():
            await websocket.close(code=4401)
            return
        user = db.get(User, session.user_id)
        if user is None:
            await websocket.close(code=4401)
            return
        call = _get_call_for_user(db, user, call_id)
        if call.status in {"ended", "declined"}:
            await websocket.close(code=4409)
            return
        await websocket.accept()
        if not await call_hub.join(call_id, websocket):
            await websocket.close(code=4409, reason="通话已连接两端")
            return
        # 双方都收到 rendezvous 信号；offer 端收到后再开始发送 offer。
        async with call_hub.lock:
            peer_count = len(call_hub.rooms.get(call_id, set()))
        if peer_count == 2:
            peer_ready = {"type": "peer_ready", "payload": {}}
            await websocket.send_json(peer_ready)
            await call_hub.broadcast(call_id, websocket, peer_ready)
        while True:
            message = await websocket.receive_json()
            if not isinstance(message, dict) or message.get("type") not in {"offer", "answer", "candidate"}:
                await websocket.send_json({"type": "error", "detail": "只支持 offer、answer、candidate 信令"})
                continue
            payload = message.get("payload")
            if not isinstance(payload, dict):
                await websocket.send_json({"type": "error", "detail": "信令 payload 必须是对象"})
                continue
            await call_hub.broadcast(call_id, websocket, {"type": message["type"], "payload": payload})
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        logger.info("通话信令连接结束：%s", exc)
        try:
            await websocket.close(code=1011)
        except Exception:
            pass
    finally:
        await call_hub.leave(call_id, websocket)
        db.close()


@app.websocket("/ws/calls/{call_id}")
async def calls_ws_root(websocket: WebSocket, call_id: str, token: str | None = Query(default=None)) -> None:
    await calls_ws(websocket, call_id, token)


@app.websocket("/api/ws/calls/{call_id}")
async def calls_ws_api(websocket: WebSocket, call_id: str, token: str | None = Query(default=None)) -> None:
    await calls_ws(websocket, call_id, token)


@app.post("/api/elders/{elder_id}/snapshot")
async def upload_snapshot(
    elder_id: str,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    elder = load_elder(db, user, elder_id, forbidden_status=403)
    if user.role not in {"elder", "admin"}:
        raise HTTPException(status_code=403, detail="只有老人端或管理员可以上传快照")
    if not elder.camera_enabled:
        raise HTTPException(status_code=409, detail="摄像头已关闭")
    content_type = request.headers.get("content-type", "")
    if not content_type.lower().startswith("image/jpeg"):
        raise HTTPException(status_code=422, detail="快照必须是 image/jpeg")
    body = await request.body()
    if len(body) == 0 or len(body) > 512 * 1024:
        raise HTTPException(status_code=422, detail="快照大小必须在 1-512KB")
    snapshot = db.get(Snapshot, elder.id)
    if snapshot is None:
        snapshot = Snapshot(elder_id=elder.id, content=body, captured_at=utcnow(), content_type="image/jpeg")
        db.add(snapshot)
    else:
        snapshot.content = body
        snapshot.captured_at = utcnow()
        snapshot.content_type = "image/jpeg"
    _commit(db)
    return {"ok": True, "captured_at": snapshot.captured_at, "size": len(body)}


@app.get("/api/elders/{elder_id}/snapshot")
def get_snapshot(
    elder_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)
) -> Response:
    elder = load_elder(db, user, elder_id)
    if not elder.camera_enabled:
        raise HTTPException(status_code=409, detail="摄像头已关闭")
    snapshot = db.get(Snapshot, elder.id)
    if snapshot is None:
        raise HTTPException(status_code=404, detail="暂无快照")
    captured = snapshot.captured_at
    return Response(
        content=snapshot.content,
        media_type=snapshot.content_type,
        headers={
            "X-Captured-At": captured.isoformat(),
            "Last-Modified": format_datetime(captured.astimezone(timezone.utc), usegmt=True),
            "Cache-Control": "no-store",
        },
    )


web_dist = Path(__file__).resolve().parents[2] / "web" / "dist"
if web_dist.is_dir():
    def spa_entry() -> FileResponse:
        return FileResponse(str(web_dist / "index.html"), media_type="text/html")

    for page_path in ("/reminders", "/health", "/safety", "/family", "/calls"):
        app.add_api_route(
            page_path,
            spa_entry,
            methods=["GET"],
            include_in_schema=False,
            name=f"spa_{page_path.strip('/')}",
        )

    @app.get("/call/{call_id}", include_in_schema=False)
    def call_entry(call_id: str) -> FileResponse:
        # The page consumes the session token from the URL and removes it
        # client-side; this route never records or echoes the query string.
        del call_id
        return spa_entry()

    # Mounted last so every /api and /ws route above keeps precedence.
    app.mount("/", StaticFiles(directory=str(web_dist), html=True), name="web")
