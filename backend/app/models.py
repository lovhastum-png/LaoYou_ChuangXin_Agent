from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


def uuid_str() -> str:
    return str(uuid.uuid4())


def json_type() -> Any:
    # JSONB is the authoritative PostgreSQL storage type used by the app.
    return JSONB


def default_routine() -> dict[str, str]:
    return {
        "wake_time": "07:00",
        "lunch_time": "12:00",
        "dinner_time": "18:00",
        "sleep_time": "22:00",
    }


def default_rules() -> dict[str, Any]:
    return {
        "night_start": "22:00",
        "night_end": "06:00",
        "immobility_minutes": 60,
        "sleep_immobility_minutes": 180,
        "away_minutes": 120,
        "heart_rate_low": 50,
        "heart_rate_high": 120,
        "systolic_high": 160,
        "diastolic_high": 100,
    }


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    display_name: Mapped[str] = mapped_column(String(128), nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    family_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    community_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    elder_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class AuthSession(Base):
    __tablename__ = "auth_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Elder(Base):
    __tablename__ = "elders"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    city: Mapped[str] = mapped_column(String(128), nullable=False, default="成都")
    family_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    community_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    camera_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    voice_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    dialect: Mapped[str] = mapped_column(String(32), nullable=False, default="zh-CN")
    routine: Mapped[dict[str, Any]] = mapped_column(
        json_type(), nullable=False, default=default_routine
    )
    rules: Mapped[dict[str, Any]] = mapped_column(
        json_type(), nullable=False, default=default_rules
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class Reminder(Base):
    __tablename__ = "reminders"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    elder_id: Mapped[str] = mapped_column(ForeignKey("elders.id"), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(128), nullable=False)
    medicine: Mapped[str] = mapped_column(String(128), nullable=False)
    dose: Mapped[str] = mapped_column(String(128), nullable=False)
    time: Mapped[str] = mapped_column(String(5), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Broadcast(Base):
    __tablename__ = "broadcasts"
    __table_args__ = (UniqueConstraint("dedupe_key", name="uq_broadcast_dedupe"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    elder_id: Mapped[str] = mapped_column(ForeignKey("elders.id"), nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    scheduled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    played_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="system")
    reminder_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    dedupe_key: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Observation(Base):
    __tablename__ = "observations"
    __table_args__ = (
        UniqueConstraint("elder_id", "idempotency_key", name="uq_observation_idempotency"),
        Index("ix_observation_elder_occurred", "elder_id", "occurred_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    elder_id: Mapped[str] = mapped_column(ForeignKey("elders.id"), nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    value: Mapped[Any | None] = mapped_column(json_type(), nullable=True)
    duration_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sleeping: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    location: Mapped[dict[str, Any] | None] = mapped_column(json_type(), nullable=True)
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="simulated")
    idempotency_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Event(Base):
    __tablename__ = "events"
    __table_args__ = (
        UniqueConstraint("dedupe_key", name="uq_event_dedupe"),
        Index("ix_event_elder_status", "elder_id", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    elder_id: Mapped[str] = mapped_column(ForeignKey("elders.id"), nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    severity: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="alerted")
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    dedupe_key: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class Timeline(Base):
    __tablename__ = "timelines"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    event_id: Mapped[str] = mapped_column(ForeignKey("events.id"), nullable=False, index=True)
    node: Mapped[str] = mapped_column(String(64), nullable=False)
    at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    actor: Mapped[str] = mapped_column(String(128), nullable=False)
    detail: Mapped[str] = mapped_column(Text, nullable=False)


class Notification(Base):
    __tablename__ = "notifications"
    __table_args__ = (UniqueConstraint("event_id", "target", name="uq_notification_target"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    event_id: Mapped[str] = mapped_column(ForeignKey("events.id"), nullable=False, index=True)
    target: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    simulated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class Escort(Base):
    __tablename__ = "escorts"
    __table_args__ = (UniqueConstraint("event_id", name="uq_escort_event"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    event_id: Mapped[str] = mapped_column(ForeignKey("events.id"), nullable=False, index=True)
    elder_id: Mapped[str] = mapped_column(ForeignKey("elders.id"), nullable=False, index=True)
    platform: Mapped[str] = mapped_column(String(32), nullable=False, default="放心医")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="requested")
    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    simulated: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class AssistantToken(Base):
    __tablename__ = "assistant_tokens"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    elder_id: Mapped[str] = mapped_column(ForeignKey("elders.id"), nullable=False)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    proposal: Mapped[dict[str, Any]] = mapped_column(json_type(), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Call(Base):
    __tablename__ = "calls"
    __table_args__ = (Index("ix_call_elder_status", "elder_id", "status"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    elder_id: Mapped[str] = mapped_column(ForeignKey("elders.id"), nullable=False, index=True)
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="ringing")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    answered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Snapshot(Base):
    __tablename__ = "snapshots"

    elder_id: Mapped[str] = mapped_column(ForeignKey("elders.id"), primary_key=True)
    content: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    content_type: Mapped[str] = mapped_column(String(64), nullable=False, default="image/jpeg")


class DemoConfig(Base):
    __tablename__ = "demo_config"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[Any] = mapped_column(json_type(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
