from __future__ import annotations

import logging
from collections.abc import Generator
from datetime import datetime, timezone

from sqlalchemy import DateTime, create_engine, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.types import TypeDecorator

from .config import DEMO_PASSWORD_DEFAULT, load_settings

logger = logging.getLogger(__name__)
settings = load_settings()


class UTCDateTime(TypeDecorator):
    """时区安全时间列：PostgreSQL 保持 timestamptz 原行为，SQLite 适配 UTC。

    本地 SQLite 快速预览临时引入；PostgreSQL 行为不变。
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        value = value.astimezone(timezone.utc)
        if dialect.name == "sqlite":
            return value.replace(tzinfo=None)
        return value

    def process_result_value(self, value: datetime | None, dialect):
        if value is not None and value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value



class Base(DeclarativeBase):
    pass


def _engine_options(database_url: str) -> dict[str, object]:
    """按方言组装引擎参数。

    PostgreSQL 必须显式给出 connect_timeout：实测在 psycopg 下，目标库不可达时
    连接等待会以原生层崩溃收场（进程直接死亡、抓不到任何 Python 异常），
    足以把整个 uvicorn worker 打挂，而 /api/health 也就无法返回 503。
    显式超时后连接失败会正常抛出 OperationalError。
    """
    if database_url.startswith("postgresql"):
        return {
            "future": True,
            "pool_pre_ping": True,
            "pool_size": 5,
            "max_overflow": 5,
            "connect_args": {"connect_timeout": settings.db_connect_timeout},
        }
    # SQLite（本地预览）没有连接超时语义，保持默认池即可。
    return {"future": True}


engine = create_engine(settings.database_url, **_engine_options(settings.database_url))
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def check_database() -> bool:
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        return True
    except SQLAlchemyError:
        logger.exception("数据库健康检查失败")
        return False
    except Exception:  # noqa: BLE001 - 健康检查不得因任何异常把请求打成 500
        logger.exception("数据库健康检查出现预期外异常")
        return False


def init_db() -> None:
    # models must be imported before create_all so metadata contains every table.
    from . import models  # noqa: F401

    Base.metadata.create_all(engine)
    if not settings.seed_demo:
        logger.info("已按 LAOYOU_SEED_DEMO=0 跳过演示账号播种")
        return
    if settings.demo_password == DEMO_PASSWORD_DEFAULT:
        logger.warning(
            "演示账号正在使用内置默认口令。请设置 LAOYOU_DEMO_PASSWORD 覆盖，"
            "或在对外部署时设置 LAOYOU_SEED_DEMO=0 不创建演示账号。"
        )
    with SessionLocal.begin() as db:
        seed_demo_data(db)


def seed_demo_data(db: Session) -> None:
    """可重复执行的本地演示账号初始化。

    口令来自 LAOYOU_DEMO_PASSWORD，缺省为内置演示口令；对外部署应改用
    LAOYOU_SEED_DEMO=0 关闭播种，或自行覆盖口令。
    """
    from .models import Elder, User
    from .security import hash_password

    users = {
        "elder": ("老人屏", "elder", "family-demo", "community-demo"),
        "child": ("子女", "child", "family-demo", "community-demo"),
        "community": ("社区", "community", None, "community-demo"),
        "admin": ("演示管理", "admin", None, None),
    }
    password_hash = hash_password(settings.demo_password)
    by_username: dict[str, User] = {}
    for username, (display_name, role, family_id, community_id) in users.items():
        user = db.query(User).filter(User.username == username).one_or_none()
        if user is None:
            user = User(
                username=username,
                display_name=display_name,
                role=role,
                family_id=family_id,
                community_id=community_id,
                password_hash=password_hash,
            )
            db.add(user)
            db.flush()
        by_username[username] = user

    elder = (
        db.query(Elder)
        .filter(Elder.family_id == "family-demo")
        .one_or_none()
    )
    if elder is None:
        elder = Elder(
            name="李奶奶",
            city="成都",
            family_id="family-demo",
            community_id="community-demo",
            camera_enabled=True,
            voice_enabled=True,
            dialect="zh-CN",
        )
        db.add(elder)
        db.flush()
    by_username["elder"].elder_id = elder.id
    by_username["child"].elder_id = elder.id
