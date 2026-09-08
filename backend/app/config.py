from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    database_url: str
    app_host: str = "0.0.0.0"
    app_port: int = 8000
    session_days: int = 7
    weather_timeout_seconds: float = 3.0


def load_settings() -> Settings:
    """读取运行配置。

    DATABASE_URL 是唯一数据库配置入口。故意不提供隐式的 SQLite 或默认
    超级用户连接，避免开发环境和演示环境误连到错误数据库。
    """
    database_url = os.getenv("DATABASE_URL", "").strip()
    if not database_url:
        raise RuntimeError(
            "未配置 DATABASE_URL。请设置 PostgreSQL 连接串，例如 "
            "postgresql+psycopg://laoyou:<password>@127.0.0.1:55432/laoyou"
        )
    if database_url.startswith("postgresql://"):
        database_url = "postgresql+psycopg://" + database_url[len("postgresql://") :]
    return Settings(
        database_url=database_url,
        app_host=os.getenv("LAOYOU_HOST", "0.0.0.0"),
        app_port=int(os.getenv("LAOYOU_PORT", "8000")),
        session_days=max(1, int(os.getenv("LAOYOU_SESSION_DAYS", "7"))),
        weather_timeout_seconds=max(
            0.5, float(os.getenv("LAOYOU_WEATHER_TIMEOUT", "3"))
        ),
    )
