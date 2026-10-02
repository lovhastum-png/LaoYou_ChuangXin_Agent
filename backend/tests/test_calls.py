"""视频通话链路的回归用例：响铃超时清理、状态机与 WS 信令增强。

覆盖本轮新增行为：
  1. 超时 ringing 的周期/惰性清理（僵尸 ringing 会堵死该老人的新通话）；
  2. create_call 幂等去重与 call_action 状态机；
  3. WS 信令白名单扩展（media_state 转发）与 peer_left 掉线通知、
     重入房间后的 peer_ready 重发。

使用独立的 SQLite 引擎 + 依赖覆盖，不需要本地 PostgreSQL。注意不要用
`with TestClient(app)`：那会触发 lifespan → init_db，从而连到真实的
DATABASE_URL。WS 处理函数直接持有 app.main.SessionLocal，不走 get_db
依赖，需用 monkeypatch 换成测试会话工厂。
"""

from __future__ import annotations

import os
from datetime import timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app.main as main_module
from app.db import Base, get_db, seed_demo_data
from app.main import app
from app.models import Call
from app.security import utcnow
from app.services import expire_stale_ringing_calls

TMP_DIR = Path(__file__).resolve().parents[2] / "tmp"
TMP_DIR.mkdir(exist_ok=True)
DB_FILE = TMP_DIR / f"pytest-calls-{os.getpid()}.db"

DEMO_PASSWORD = "Laoyou123!"
RING_TIMEOUT = 90


@pytest.fixture(scope="module")
def session_factory():
    if DB_FILE.exists():
        DB_FILE.unlink()
    engine = create_engine(
        f"sqlite:///{DB_FILE.as_posix()}", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(engine)
    testing_session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    with testing_session.begin() as db:
        seed_demo_data(db)
    yield testing_session
    engine.dispose()


@pytest.fixture(scope="module")
def client(session_factory, monkeymodule):
    def override_get_db():
        db = session_factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    # calls_ws 直接使用 SessionLocal()，依赖覆盖对它无效。
    monkeymodule.setattr(main_module, "SessionLocal", session_factory)
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


@pytest.fixture(scope="module")
def monkeymodule():
    # pytest 自带的 monkeypatch 是 function 级，这里需要 module 级撤销。
    from _pytest.monkeypatch import MonkeyPatch

    patcher = MonkeyPatch()
    yield patcher
    patcher.undo()


def login(client: TestClient, username: str) -> dict[str, str]:
    response = client.post(
        "/api/auth/login", json={"username": username, "password": DEMO_PASSWORD}
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['token']}"}


def bearer_token(headers: dict[str, str]) -> str:
    return headers["Authorization"].split(" ", 1)[1]


def demo_elder_id(client: TestClient, headers: dict[str, str]) -> str:
    response = client.get("/api/elders", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()[0]["id"]


# ------------------------------------------------------------ 响铃超时清理

def test_expire_only_stale_ringing(session_factory):
    from app.models import Elder, User

    with session_factory() as db:
        elder = db.query(Elder).first()
        user = db.query(User).filter(User.username == "child").one()
        fresh = Call(elder_id=elder.id, created_by=user.id, status="ringing")
        stale = Call(
            elder_id=elder.id,
            created_by=user.id,
            status="ringing",
            created_at=utcnow() - timedelta(seconds=RING_TIMEOUT + 30),
        )
        answered = Call(
            elder_id=elder.id,
            created_by=user.id,
            status="active",
            created_at=utcnow() - timedelta(seconds=RING_TIMEOUT + 30),
        )
        db.add_all([fresh, stale, answered])
        db.flush()
        cleared = expire_stale_ringing_calls(db, RING_TIMEOUT)
        assert cleared == 1
        db.flush()
        assert stale.status == "ended" and stale.ended_at is not None
        assert fresh.status == "ringing"
        assert answered.status == "active"
        db.rollback()


def test_create_call_bypasses_zombie_ringing(client):
    headers = login(client, "child")
    elder_id = demo_elder_id(client, headers)

    first = client.post(f"/api/elders/{elder_id}/calls", headers=headers)
    assert first.status_code == 200, first.text
    zombie_id = first.json()["id"]

    # 直接改库把它变老：模拟调度周期之外残留的僵尸 ringing。
    with main_module.SessionLocal() as db:
        zombie = db.get(Call, zombie_id)
        zombie.created_at = utcnow() - timedelta(seconds=RING_TIMEOUT + 30)
        db.commit()

    second = client.post(f"/api/elders/{elder_id}/calls", headers=headers)
    assert second.status_code == 200, second.text
    assert second.json()["id"] != zombie_id, "超时 ringing 不应再被幂等复用"
    assert second.json()["status"] == "ringing"

    detail = client.get(f"/api/calls/{zombie_id}", headers=headers)
    assert detail.json()["status"] == "ended"
    assert detail.json()["ended_at"] is not None

    # 收尾，避免影响其他用例。
    client.post(
        f"/api/calls/{second.json()['id']}/actions",
        json={"action": "end"},
        headers=headers,
    )


def test_create_call_idempotent_for_live_ringing(client):
    headers = login(client, "child")
    elder_id = demo_elder_id(client, headers)
    first = client.post(f"/api/elders/{elder_id}/calls", headers=headers).json()
    second = client.post(f"/api/elders/{elder_id}/calls", headers=headers).json()
    assert second["id"] == first["id"]
    client.post(f"/api/calls/{first['id']}/actions", json={"action": "end"}, headers=headers)


# ------------------------------------------------------------ 状态机

def test_call_action_state_machine(client):
    child = login(client, "child")
    elder_id = demo_elder_id(client, child)
    call = client.post(f"/api/elders/{elder_id}/calls", headers=child).json()

    answer = client.post(f"/api/calls/{call['id']}/actions", json={"action": "answer"}, headers=child)
    assert answer.status_code == 200 and answer.json()["status"] == "active"
    # 重复 answer 幂等。
    again = client.post(f"/api/calls/{call['id']}/actions", json={"action": "answer"}, headers=child)
    assert again.status_code == 200 and again.json()["status"] == "active"
    # active 不能 decline。
    decline = client.post(f"/api/calls/{call['id']}/actions", json={"action": "decline"}, headers=child)
    assert decline.status_code == 409
    end = client.post(f"/api/calls/{call['id']}/actions", json={"action": "end"}, headers=child)
    assert end.status_code == 200 and end.json()["status"] == "ended"
    # ended 后再 answer 是状态冲突。
    late = client.post(f"/api/calls/{call['id']}/actions", json={"action": "answer"}, headers=child)
    assert late.status_code == 409


def test_community_cannot_operate_call(client):
    child = login(client, "child")
    community = login(client, "community")
    elder_id = demo_elder_id(client, child)
    call = client.post(f"/api/elders/{elder_id}/calls", headers=child).json()
    response = client.post(
        f"/api/calls/{call['id']}/actions", json={"action": "answer"}, headers=community
    )
    assert response.status_code == 403
    client.post(f"/api/calls/{call['id']}/actions", json={"action": "end"}, headers=child)


# ------------------------------------------------------------ WS 信令

def test_ws_media_state_peer_left_and_rejoin(client):
    child = login(client, "child")
    elder = login(client, "elder")
    elder_id = demo_elder_id(client, child)
    call = client.post(f"/api/elders/{elder_id}/calls", headers=child).json()
    call_id = call["id"]

    with client.websocket_connect(f"/ws/calls/{call_id}?token={bearer_token(elder)}") as ws_elder:
        with client.websocket_connect(f"/ws/calls/{call_id}?token={bearer_token(child)}") as ws_child:
            assert ws_child.receive_json()["type"] == "peer_ready"
            assert ws_elder.receive_json()["type"] == "peer_ready"

            # media_state 进入白名单并转发给对端。
            ws_child.send_json({"type": "media_state", "payload": {"audio": False, "video": True}})
            forwarded = ws_elder.receive_json()
            assert forwarded["type"] == "media_state"
            assert forwarded["payload"] == {"audio": False, "video": True}

            # 非法类型被拒绝且不回放。
            ws_child.send_json({"type": "token", "payload": {"secret": "x"}})
            rejected = ws_child.receive_json()
            assert rejected["type"] == "error"

        # child 端断开：elder 端收到 peer_left 即时提示。
        assert ws_elder.receive_json()["type"] == "peer_left"

        # 重入房间：满员后双方再次收到 peer_ready（重连再协商的锚点）。
        with client.websocket_connect(f"/ws/calls/{call_id}?token={bearer_token(child)}") as ws_child2:
            assert ws_child2.receive_json()["type"] == "peer_ready"
            assert ws_elder.receive_json()["type"] == "peer_ready"

    client.post(f"/api/calls/{call_id}/actions", json={"action": "end"}, headers=child)


def test_ws_rejects_ended_call(client):
    child = login(client, "child")
    elder = login(client, "elder")
    elder_id = demo_elder_id(client, child)
    call = client.post(f"/api/elders/{elder_id}/calls", headers=child).json()
    client.post(f"/api/calls/{call['id']}/actions", json={"action": "end"}, headers=child)
    from starlette.websockets import WebSocketDisconnect

    with pytest.raises(WebSocketDisconnect) as closed:
        with client.websocket_connect(f"/ws/calls/{call['id']}?token={bearer_token(elder)}"):
            pass
    assert closed.value.code == 4409
