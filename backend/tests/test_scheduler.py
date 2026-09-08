from __future__ import annotations

import os

import pytest


if not (os.getenv("DATABASE_URL") and os.getenv("LAOYOU_RUN_DB_TESTS")):
    pytest.skip("需要显式设置 DATABASE_URL 与 LAOYOU_RUN_DB_TESTS=1 才运行 PostgreSQL 集成测试", allow_module_level=True)

from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

from app.config import Settings
from app.db import SessionLocal
from app.models import Broadcast, DemoConfig, Elder, Observation, Reminder
from app.services import health_broadcast_text, schedule_due_broadcasts, discard_pending_reminder_broadcasts
import app.services as services


def test_medication_broadcast_is_idempotent_and_health_uses_observations(monkeypatch):
    local = datetime.now(timezone.utc).astimezone(ZoneInfo("Asia/Shanghai")).replace(second=0, microsecond=0)
    now = local.astimezone(timezone.utc)
    clock = local.strftime("%H:%M")
    db = SessionLocal()
    elder = Elder(
        name="scheduler-test",
        city="未知",
        family_id="scheduler-test-family",
        community_id="scheduler-test-community",
        routine={"wake_time": clock, "lunch_time": "03:17", "dinner_time": "03:18", "sleep_time": "03:19"},
        rules={"night_start": "22:00", "night_end": "06:00", "immobility_minutes": 60, "sleep_immobility_minutes": 180, "away_minutes": 120, "heart_rate_low": 50, "heart_rate_high": 120, "systolic_high": 160, "diastolic_high": 100},
    )
    try:
        db.add(elder)
        db.flush()
        db.add(Reminder(elder_id=elder.id, title="scheduler-test", medicine="测试药", dose="1片", time=clock, enabled=True))
        db.add(Observation(elder_id=elder.id, kind="heart_rate", value=130, occurred_at=now, source="simulated"))
        db.add(Observation(elder_id=elder.id, kind="blood_pressure", value={"systolic": 150, "diastolic": 90}, occurred_at=now, source="simulated"))
        db.add(Observation(elder_id=elder.id, kind="activity", value="散步10分钟", occurred_at=now, source="simulated"))
        db.flush()
        monkeypatch.setattr(services, "fetch_weather", lambda elder, app_settings: {"city": elder.city, "temperature": 20, "description": "晴", "advice": "测试", "observed_at": now, "source": "live"})
        assert schedule_due_broadcasts(db, at=now, app_settings=Settings(database_url="")) == 3
        assert schedule_due_broadcasts(db, at=now, app_settings=Settings(database_url="")) == 0
        items = db.query(Broadcast).filter(Broadcast.elder_id == elder.id).all()
        assert len([item for item in items if item.kind == "medication"]) == 1
        assert "心率 130" in health_broadcast_text(db, elder)
        assert "血压 150/90" in health_broadcast_text(db, elder)
        assert "活动记录 散步10分钟" in health_broadcast_text(db, elder)
        assert "超出演示阈值，请家属核实" in health_broadcast_text(db, elder)
    finally:
        # Everything in this test is flushed in one transaction. Rollback also
        # removes any same-minute broadcasts considered for existing demo elders.
        db.rollback()
        db.close()


def test_missed_minute_is_caught_up_once_and_marked_as_late():
    db = SessionLocal()
    scheduled = datetime(2026, 9, 9, 12, 0, tzinfo=timezone.utc)
    try:
        elder = Elder(name="catchup-test", city="未知", family_id="catchup-test", community_id="catchup-test")
        db.add(elder)
        db.flush()
        reminder = Reminder(elder_id=elder.id, title="晚间测试", medicine="演示", dose="演示", time="20:00", enabled=True, created_at=scheduled-timedelta(days=1))
        db.add(reminder)
        checkpoint = db.get(DemoConfig, "scheduler_checkpoint")
        value = {"at": (scheduled-timedelta(seconds=10)).isoformat()}
        if checkpoint: checkpoint.value = value
        else: db.add(DemoConfig(key="scheduler_checkpoint", value=value))
        db.flush()
        schedule_due_broadcasts(db, at=scheduled+timedelta(minutes=1), app_settings=Settings(database_url=""))
        db.expire_all()
        schedule_due_broadcasts(db, at=scheduled+timedelta(minutes=2), app_settings=Settings(database_url=""))
        rows = db.query(Broadcast).filter(Broadcast.reminder_id == reminder.id).all()
        assert len(rows) == 1
        assert rows[0].scheduled_at == scheduled
        assert "原定 20:00" in rows[0].text and "避免重复服用" in rows[0].text
    finally:
        db.rollback()
        db.close()


def test_cancelling_pending_reminder_preserves_already_played_history():
    db = SessionLocal()
    now = datetime.now(timezone.utc)
    try:
        elder = Elder(name="cancel-test", city="未知", family_id="cancel-test", community_id="cancel-test")
        db.add(elder); db.flush()
        reminder = Reminder(elder_id=elder.id, title="取消测试", medicine="演示", dose="演示", time="20:00", enabled=True)
        db.add(reminder); db.flush()
        pending = Broadcast(elder_id=elder.id, reminder_id=reminder.id, kind="medication", text="待播", scheduled_at=now, source="system", dedupe_key=f"pending:{reminder.id}")
        played = Broadcast(elder_id=elder.id, reminder_id=reminder.id, kind="medication", text="已播", scheduled_at=now, played_at=now, source="system", dedupe_key=f"played:{reminder.id}")
        db.add_all([pending, played]); db.flush()
        discard_pending_reminder_broadcasts(db, reminder.id)
        remaining = db.query(Broadcast).filter(Broadcast.reminder_id == reminder.id).all()
        assert [item.id for item in remaining] == [played.id]
    finally:
        db.rollback(); db.close()
