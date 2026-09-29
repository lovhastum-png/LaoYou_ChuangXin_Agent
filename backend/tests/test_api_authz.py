"""接口层鉴权、越权与资源上限的回归用例。

这些路径此前完全没有测试覆盖（见审计报告）：鉴权判定、跨家庭越权、角色边界、
请求体上限、助手确认路径的字段校验，此前都只能靠人工验证。

本模块使用独立的 SQLite 引擎 + 依赖覆盖，因此不需要本地 PostgreSQL 或
LAOYOU_RUN_DB_TESTS 开关。注意不要用 `with TestClient(app)`：那会触发
lifespan → init_db，从而连到真实的 DATABASE_URL。
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base, get_db, seed_demo_data
from app.main import app
from app.models import Elder

TMP_DIR = Path(__file__).resolve().parents[2] / "tmp"
TMP_DIR.mkdir(exist_ok=True)
# 每次进程用独立文件名：Windows 下复用同名文件会与上一次的句柄竞争。
DB_FILE = TMP_DIR / f"pytest-api-authz-{os.getpid()}.db"

DEMO_PASSWORD = "Laoyou123!"
OTHER_ELDER_ID = "elder-other-family"


@pytest.fixture(scope="module")
def client():
    if DB_FILE.exists():
        DB_FILE.unlink()
    engine = create_engine(
        f"sqlite:///{DB_FILE.as_posix()}", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(engine)
    testing_session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    with testing_session.begin() as db:
        seed_demo_data(db)
        # 另一个家庭的老人：用于验证跨家庭越权被挡住。
        db.add(
            Elder(
                id=OTHER_ELDER_ID,
                name="隔壁王大爷",
                city="成都",
                family_id="family-other",
                community_id="community-other",
            )
        )

    def override_get_db():
        db = testing_session()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()
        engine.dispose()


def login(client: TestClient, username: str) -> dict[str, str]:
    response = client.post(
        "/api/auth/login", json={"username": username, "password": DEMO_PASSWORD}
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['token']}"}


def demo_elder_id(client: TestClient, headers: dict[str, str]) -> str:
    response = client.get("/api/elders", headers=headers)
    assert response.status_code == 200, response.text
    elders = [item for item in response.json() if item["id"] != OTHER_ELDER_ID]
    assert elders, "演示老人缺失"
    return elders[0]["id"]


@pytest.mark.parametrize(
    "path",
    [
        "/api/elders",
        "/api/events",
        "/api/notifications",
        "/api/escorts",
        "/api/auth/me",
    ],
)
def test_protected_routes_reject_anonymous(client: TestClient, path: str):
    assert client.get(path).status_code == 401


def test_child_cannot_read_other_family_elder(client: TestClient):
    headers = login(client, "child")
    visible = {item["id"] for item in client.get("/api/elders", headers=headers).json()}
    assert OTHER_ELDER_ID not in visible, "子女列表里出现了别的家庭的老人"

    for path in (
        f"/api/elders/{OTHER_ELDER_ID}/dashboard",
        f"/api/elders/{OTHER_ELDER_ID}/reminders",
        f"/api/elders/{OTHER_ELDER_ID}/broadcasts",
        f"/api/elders/{OTHER_ELDER_ID}/snapshot",
    ):
        assert client.get(path, headers=headers).status_code == 404, path


def test_child_cannot_write_other_family_elder(client: TestClient):
    headers = login(client, "child")
    response = client.post(
        f"/api/elders/{OTHER_ELDER_ID}/reminders",
        headers=headers,
        json={
            "title": "越权写入",
            "medicine": "测试",
            "dose": "1 片",
            "time": "08:00",
        },
    )
    # 写路径用 forbidden_status=403 明确拒绝，而不是把不归自己家庭的数据藏成 404。
    assert response.status_code == 403, response.text


def test_community_is_read_only_for_elder_writes(client: TestClient):
    headers = login(client, "community")
    elder_id = client.get("/api/elders", headers=headers).json()[0]["id"]
    response = client.post(
        f"/api/elders/{elder_id}/reminders",
        headers=headers,
        json={"title": "社区写入", "medicine": "测试", "dose": "1 片", "time": "08:00"},
    )
    assert response.status_code == 403, response.text


def test_elder_cannot_take_event_actions(client: TestClient):
    admin = login(client, "admin")
    elder_id = demo_elder_id(client, admin)
    created = client.post(
        f"/api/elders/{elder_id}/observations",
        headers=admin,
        json={"kind": "fall", "source": "simulated"},
    )
    assert created.status_code in {200, 201}, created.text
    events = created.json().get("events") or []
    if not events:
        pytest.skip("本次观测未产生事件，跳过角色用例")
    event_id = events[0]["id"]

    elder = login(client, "elder")
    for action in ("acknowledge", "start", "resolve"):
        response = client.post(
            f"/api/events/{event_id}/actions", headers=elder, json={"action": action}
        )
        assert response.status_code == 403, (action, response.text)


def test_child_cannot_confirm_broadcast_played(client: TestClient):
    headers = login(client, "child")
    response = client.post("/api/broadcasts/any-id/played", headers=headers)
    assert response.status_code == 403, response.text


def test_observation_rejects_forged_live_source(client: TestClient):
    admin = login(client, "admin")
    elder_id = demo_elder_id(client, admin)
    response = client.post(
        f"/api/elders/{elder_id}/observations",
        headers=admin,
        json={"kind": "heart_rate", "value": 80, "source": "live_device"},
    )
    assert response.status_code == 422, response.text


def test_rules_reject_equal_night_window(client: TestClient):
    headers = login(client, "child")
    elder_id = demo_elder_id(client, headers)
    response = client.patch(
        f"/api/elders/{elder_id}/settings",
        headers=headers,
        json={
            "rules": {
                "night_start": "22:00",
                "night_end": "22:00",
                "immobility_minutes": 90,
                "sleep_immobility_minutes": 180,
                "away_minutes": 720,
                "heart_rate_low": 50,
                "heart_rate_high": 120,
                "systolic_high": 160,
                "diastolic_high": 95,
            }
        },
    )
    assert response.status_code == 422, response.text


def test_assistant_confirm_rejects_overlong_medicine(client: TestClient):
    """助手确认路径必须与 REST 路径一样受字段长度约束（曾因绕过校验返回 500）。"""
    headers = login(client, "elder")
    elder_id = demo_elder_id(client, headers)
    proposal = client.post(
        f"/api/elders/{elder_id}/assistant",
        headers=headers,
        json={"text": "每天八点提醒我吃" + "药" * 200},
    )
    assert proposal.status_code == 200, proposal.text
    token = proposal.json().get("confirm_token")
    if not token:
        pytest.skip("未生成确认令牌，跳过确认路径用例")

    confirmed = client.post(
        f"/api/elders/{elder_id}/assistant",
        headers=headers,
        json={"text": "确认", "confirm_token": token},
    )
    assert confirmed.status_code == 422, confirmed.text


def test_oversized_request_body_is_rejected_before_parsing(client: TestClient):
    """超限正文应在读取阶段就被拒绝，而不是先缓冲进内存再判长度。"""
    payload = b"x" * (5 * 1024 * 1024)
    response = client.post(
        "/api/auth/login",
        content=payload,
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 413, response.status_code
    assert "过大" in response.json()["detail"]


def test_health_reports_database_failure_as_503(client: TestClient, monkeypatch):
    """数据库不可用时 /api/health 必须返回 503，而不是让请求挂掉。"""
    import app.main as main_module

    monkeypatch.setattr(main_module, "check_database", lambda: False)
    assert client.get("/api/health").status_code == 503
