from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


TIME_PATTERN = r"^(?:[01]\d|2[0-3]):[0-5]\d$"


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class UserOut(BaseModel):
    id: str
    username: str
    display_name: str
    role: Literal["elder", "child", "community", "admin"]


class LoginOut(BaseModel):
    token: str
    user: UserOut


class Routine(BaseModel):
    wake_time: str = Field(pattern=TIME_PATTERN)
    lunch_time: str = Field(pattern=TIME_PATTERN)
    dinner_time: str = Field(pattern=TIME_PATTERN)
    sleep_time: str = Field(pattern=TIME_PATTERN)


class Rules(BaseModel):
    night_start: str = Field(pattern=TIME_PATTERN)
    night_end: str = Field(pattern=TIME_PATTERN)
    immobility_minutes: int = Field(ge=1, le=1440)
    sleep_immobility_minutes: int = Field(ge=1, le=1440)
    away_minutes: int = Field(ge=1, le=10080)
    heart_rate_low: float = Field(ge=1, le=300)
    heart_rate_high: float = Field(ge=1, le=300)
    systolic_high: float = Field(ge=1, le=400)
    diastolic_high: float = Field(ge=1, le=300)

    @model_validator(mode="after")
    def validate_heart_range(self) -> "Rules":
        if self.heart_rate_low >= self.heart_rate_high:
            raise ValueError("heart_rate_low 必须小于 heart_rate_high")
        if self.night_start == self.night_end:
            # 两端相同时 is_night 恒为假，夜间徘徊规则会静默失效。
            raise ValueError("night_start 与 night_end 不能相同，否则夜间规则不会生效")
        return self


class ElderOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    city: str
    camera_enabled: bool
    voice_enabled: bool
    dialect: str
    routine: Routine
    rules: Rules


class ElderSettingsPatch(BaseModel):
    camera_enabled: bool | None = None
    voice_enabled: bool | None = None
    dialect: str | None = Field(default=None, max_length=32)
    city: str | None = Field(default=None, min_length=1, max_length=128)
    routine: Routine | None = None
    rules: Rules | None = None
    confirm_camera_off: bool = False


class ReminderCreate(BaseModel):
    title: str = Field(min_length=1, max_length=128)
    medicine: str = Field(min_length=1, max_length=128)
    dose: str = Field(min_length=1, max_length=128)
    time: str = Field(pattern=TIME_PATTERN)
    enabled: bool = True


class ReminderPatch(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=128)
    medicine: str | None = Field(default=None, min_length=1, max_length=128)
    dose: str | None = Field(default=None, min_length=1, max_length=128)
    time: str | None = Field(default=None, pattern=TIME_PATTERN)
    enabled: bool | None = None


class LabelRecognitionRequest(BaseModel):
    """药盒照片识别请求。image 接受 data URL 或裸 base64。

    上限 4,000,000 字符是配合全局 4 MB 请求体限制设的：base64 比原图大约 1/3，
    前端上传前会先把长边缩到 1280，正常只有几百 KB。
    """

    image: str = Field(min_length=32, max_length=4_000_000)


class ObservationCreate(BaseModel):
    kind: Literal[
        "fall",
        "wandering",
        "immobility",
        "away",
        "heart_rate",
        "blood_pressure",
        "activity",
        "wake",
        "lunch",
        "dinner",
        "sleep",
        "return_home",
    ]
    value: Any | None = None
    duration_minutes: int | None = Field(default=None, ge=0, le=10080)
    sleeping: bool | None = None
    occurred_at: datetime | None = None
    location: dict[str, Any] | None = None
    source: str = Field(default="simulated", min_length=1, max_length=32)
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=128)


class EventAction(BaseModel):
    action: Literal[
        "acknowledge",
        "start",
        "resolve",
        "correct",
        "community_unavailable",
    ]
    note: str | None = Field(default=None, max_length=2000)


class NotificationConfig(BaseModel):
    fail_targets: list[Literal["child", "community", "emergency"]] = Field(default_factory=list)


class NotificationAction(BaseModel):
    pass


class EscortAction(BaseModel):
    action: Literal["accept", "complete"]
    note: str | None = Field(default=None, max_length=2000)


class AssistantRequest(BaseModel):
    text: str = Field(min_length=1, max_length=500)
    dialect: str | None = Field(default=None, max_length=32)
    confirm_token: str | None = Field(default=None, max_length=256)


class SpeechSynthesisRequest(BaseModel):
    """播报请求。text 上限与 edge_tts 适配器的 MAX_TEXT_LENGTH 对齐。"""

    text: str = Field(min_length=1, max_length=300)
    dialect: str | None = Field(default=None, max_length=32)


class CallAction(BaseModel):
    action: Literal["answer", "end", "decline"]
