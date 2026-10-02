from __future__ import annotations

import os
from dataclasses import dataclass


DEMO_PASSWORD_DEFAULT = "Laoyou123!"


@dataclass(frozen=True)
class Settings:
    database_url: str
    app_host: str = "0.0.0.0"
    app_port: int = 8000
    session_days: int = 7
    weather_timeout_seconds: float = 3.0
    db_connect_timeout: int = 5
    max_request_body_bytes: int = 4 * 1024 * 1024
    call_ring_timeout_seconds: int = 90
    turn_ttl_seconds: int = 3600
    seed_demo: bool = True
    demo_password: str = DEMO_PASSWORD_DEFAULT
    cors_origins: tuple[str, ...] = ("*",)


def _parse_origins(raw: str) -> tuple[str, ...]:
    origins = tuple(item.strip() for item in raw.split(",") if item.strip())
    return origins or ("*",)


def load_settings() -> Settings:
    """读取运行配置。

    DATABASE_URL 是唯一数据库配置入口。PostgreSQL 是权威存储；本地快速预览
    允许显式使用 sqlite:// 连接串（见 doc/01-开发者文档.md「本地预览模式」），
    但不提供隐式回退，避免误连到错误数据库。

    演示账号相关开关：
      LAOYOU_SEED_DEMO=0        不创建演示账号（对外部署时必须设置）
      LAOYOU_DEMO_PASSWORD=...  覆盖演示账号口令
    """
    database_url = os.getenv("DATABASE_URL", "").strip()
    if not database_url:
        raise RuntimeError(
            "未配置 DATABASE_URL。请设置 PostgreSQL 连接串，例如 "
            "postgresql+psycopg://laoyou:<password>@127.0.0.1:55432/laoyou"
        )
    if database_url.startswith("postgresql://"):
        database_url = "postgresql+psycopg://" + database_url[len("postgresql://") :]
    demo_password = os.getenv("LAOYOU_DEMO_PASSWORD", "").strip() or DEMO_PASSWORD_DEFAULT
    return Settings(
        database_url=database_url,
        app_host=os.getenv("LAOYOU_HOST", "0.0.0.0"),
        app_port=int(os.getenv("LAOYOU_PORT", "8000")),
        session_days=max(1, int(os.getenv("LAOYOU_SESSION_DAYS", "7"))),
        weather_timeout_seconds=max(
            0.5, float(os.getenv("LAOYOU_WEATHER_TIMEOUT", "3"))
        ),
        db_connect_timeout=max(1, int(os.getenv("LAOYOU_DB_CONNECT_TIMEOUT", "5"))),
        max_request_body_bytes=max(
            64 * 1024, int(os.getenv("LAOYOU_MAX_BODY_BYTES", str(4 * 1024 * 1024)))
        ),
        call_ring_timeout_seconds=max(
            10, int(os.getenv("LAOYOU_CALL_RING_TIMEOUT", "90"))
        ),
        turn_ttl_seconds=max(300, int(os.getenv("LAOYOU_TURN_TTL", "3600"))),
        seed_demo=os.getenv("LAOYOU_SEED_DEMO", "1").strip().lower()
        not in {"0", "false", "no"},
        demo_password=demo_password,
        cors_origins=_parse_origins(os.getenv("LAOYOU_CORS_ORIGINS", "*")),
    )
