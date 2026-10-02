"""方言语音链路的接口级测试。

分两层，避免把"链路连通"和"识别质量"混为一谈：

  1. 选路与校验（默认运行，不联网）：用打桩的识别后端验证 /speech 的
     provider 选择、参数校验、错误码映射。
  2. 真实归一化（需凭据，LAOYOU_LIVE_SPEECH=1）：把真实音频交给模型，
     再喂给 /assistant，验证方言能变成提醒提案。识别准确率以实测为准，
     单元测试不宣称质量。

路由层测试不联网；真实测试只用 MP3 走适配器，不要求本地有音频解码器。
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
from app.services import parse_reminder

TMP_DIR = Path(__file__).resolve().parents[2] / "tmp"
FIXTURES = TMP_DIR / "dialect-probe"
LIVE = os.environ.get("LAOYOU_LIVE_SPEECH") == "1" and bool(os.environ.get("QWEN_API_KEY"))
DB_FILE = TMP_DIR / f"pytest-speech-{os.getpid()}.db"


@pytest.fixture(scope="module")
def client():
    if DB_FILE.exists():
        DB_FILE.unlink()
    engine = create_engine(f"sqlite:///{DB_FILE.as_posix()}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    session_local = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    with session_local.begin() as db:
        seed_demo_data(db)

    def override_get_db():
        db = session_local()
        try:
            yield db
        finally:
            db.close()

    # 不要用 `with TestClient(app)`：那会触发 lifespan → init_db，
    # 连到真实的 DATABASE_URL（本模块按约定不需要本地 PostgreSQL）。
    app.dependency_overrides[get_db] = override_get_db
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()
        engine.dispose()


def _login(client: TestClient, username: str) -> tuple[dict, str]:
    response = client.post("/api/auth/login", json={"username": username, "password": "Laoyou123!"})
    assert response.status_code == 200, response.text
    headers = {"Authorization": f"Bearer {response.json()['token']}"}
    elders = client.get("/api/elders", headers=headers)
    assert elders.status_code == 200, elders.text
    return headers, elders.json()[0]["id"]


# ---------------------------------------------------------------- 选路与校验

def test_forced_qwen_without_credentials_reports_503(client, monkeypatch):
    monkeypatch.delenv("QWEN_API_KEY", raising=False)
    headers, elder_id = _login(client, "elder")
    response = client.post(
        f"/api/elders/{elder_id}/speech?dialect=sichuan&provider=qwen",
        content=b"\x00" * 4000,
        headers={**headers, "Content-Type": "audio/L16"},
    )
    assert response.status_code == 503


def test_dialect_without_any_provider_reports_503(client, monkeypatch):
    monkeypatch.delenv("QWEN_API_KEY", raising=False)
    for key in ("XFYUN_APP_ID", "XFYUN_API_KEY", "XFYUN_API_SECRET"):
        monkeypatch.delenv(key, raising=False)
    headers, elder_id = _login(client, "elder")
    response = client.post(
        f"/api/elders/{elder_id}/speech?dialect=northeast",
        content=b"\x00" * 4000,
        headers={**headers, "Content-Type": "audio/L16"},
    )
    assert response.status_code == 503


def test_bad_audio_length_is_rejected_before_provider_call(client, monkeypatch):
    # 不配置任何凭据：若长度校验生效，应在选路前就以 422 结束，而不是 503。
    monkeypatch.delenv("QWEN_API_KEY", raising=False)
    headers, elder_id = _login(client, "elder")
    for size in (100, 3_200_001):
        response = client.post(
            f"/api/elders/{elder_id}/speech?dialect=sichuan",
            content=b"\x00" * size,
            headers={**headers, "Content-Type": "audio/L16"},
        )
        assert response.status_code == 422, f"size={size}"


def test_wrong_content_type_is_rejected(client):
    headers, elder_id = _login(client, "elder")
    response = client.post(
        f"/api/elders/{elder_id}/speech?dialect=sichuan",
        content=b"\x00" * 4000,
        headers={**headers, "Content-Type": "audio/mpeg"},
    )
    assert response.status_code == 422


def test_community_role_cannot_use_speech(client):
    headers, elder_id = _login(client, "elder")
    community_headers, _ = _login(client, "community")
    response = client.post(
        f"/api/elders/{elder_id}/speech?dialect=sichuan",
        content=b"\x00" * 4000,
        headers={**community_headers, "Content-Type": "audio/L16"},
    )
    assert response.status_code == 403


# ------------------------------------------------------------ 真实方言归一化

@pytest.mark.skipif(not LIVE, reason="需要 LAOYOU_LIVE_SPEECH=1 与 QWEN_API_KEY")
def test_live_dialect_normalization_then_reminder_proposal():
    """东北话 -> 归一化普通话 -> 规则助手提醒提案。

    音频用系统语音合成（tmp/gen_pcm_fixtures.mjs），验证的是链路，
    不是真实老人方言口音的识别率；后者须另做人工验收。
    """
    import asyncio

    from app.integrations.qwen_asr import transcribe_audio

    mp3 = FIXTURES / "ne-medicine.mp3"
    if not mp3.exists():
        pytest.skip(
            f"缺少夹具 {mp3}。用 msedge-tts 合成后再跑："
            "文本“你帮我整个闹钟，明儿个早上七点叫我吃药，吃降压片。”，"
            "音色 zh-CN-liaoning-XiaobeiNeural。"
        )

    text = asyncio.run(transcribe_audio(mp3.read_bytes(), "northeast", extension="mp3"))
    assert "咋" not in text, f"归一化后仍残留方言词：{text}"

    proposal = parse_reminder(text)
    assert proposal is not None, f"归一化结果未能解析成提醒：{text}"
    assert proposal["time"] == "07:00"
    assert proposal["medicine"] == "降压片"


@pytest.mark.skipif(not LIVE, reason="需要 LAOYOU_LIVE_SPEECH=1 与 QWEN_API_KEY")
def test_live_capabilities_advertises_qwen(client):
    body = client.get("/api/capabilities").json()
    assert body["speech"]["configured"] is True
    assert body["speech"]["provider"] == "qwen"
    assert body["speech"]["normalizes_dialect"] is True
