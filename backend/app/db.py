from __future__ import annotations

import logging
from collections.abc import Generator

from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import load_settings

logger = logging.getLogger(__name__)
settings = load_settings()


class Base(DeclarativeBase):
    pass


engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,
    future=True,
    pool_size=5,
    max_overflow=5,
)
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


def init_db() -> None:
    # models must be imported before create_all so metadata contains every table.
    from . import models  # noqa: F401

    Base.metadata.create_all(engine)
    with SessionLocal.begin() as db:
        seed_demo_data(db)


def seed_demo_data(db: Session) -> None:
    """可重复执行的本地演示账号初始化。"""
    from .models import Elder, User
    from .security import hash_password

    users = {
        "elder": ("老人屏", "elder", "family-demo", "community-demo"),
        "child": ("子女", "child", "family-demo", "community-demo"),
        "community": ("社区", "community", None, "community-demo"),
        "admin": ("演示管理", "admin", None, None),
    }
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
                password_hash=hash_password("Laoyou123!"),
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
