"""方言语音合成适配器的边界与真实合成测试。

前一半不联网，校验音色选择与错误映射；后一半在 LAOYOU_LIVE_TTS=1 时
真实合成，验证"列表里的音色不等于可用"这一事实。
"""
from __future__ import annotations

import asyncio
import os

import pytest

from app.integrations.edge_tts import (
    DIALECT_VOICES,
    SpeechServiceError,
    has_dialect_voice,
    is_configured,
    resolve_voice,
    synthesize,
)


def test_rejects_empty_text(monkeypatch):
    monkeypatch.delenv("LAOYOU_DISABLE_TTS", raising=False)
    with pytest.raises(SpeechServiceError) as error:
        asyncio.run(synthesize("   ", "northeast"))
    assert error.value.status_code == 422


def test_rejects_overlong_text(monkeypatch):
    monkeypatch.delenv("LAOYOU_DISABLE_TTS", raising=False)
    with pytest.raises(SpeechServiceError) as error:
        asyncio.run(synthesize("好" * 301, "northeast"))
    assert error.value.status_code == 422


def test_disabled_by_env_reports_503(monkeypatch):
    monkeypatch.setenv("LAOYOU_DISABLE_TTS", "1")
    assert not is_configured()
    with pytest.raises(SpeechServiceError) as error:
        asyncio.run(synthesize("早上七点吃药", "northeast"))
    assert error.value.status_code == 503


def test_dialect_voice_mapping():
    assert has_dialect_voice("northeast")
    assert has_dialect_voice("yue-HK")
    # 四川话没有专属音色，回落普通话——不假装有川味
    assert not has_dialect_voice("sichuan")
    assert resolve_voice("sichuan") == DIALECT_VOICES["zh-CN"]
    assert resolve_voice("northeast") == DIALECT_VOICES["northeast"]


def test_every_advertised_voice_is_not_hkong_gai():
    """回归：HiuGaai 在 edge-tts 7.x 上收不到音频，不能再被选为粤语音色。"""
    assert DIALECT_VOICES["yue-HK"] != "zh-HK-HiuGaaiNeural"


LIVE = os.environ.get("LAOYOU_LIVE_TTS") == "1"


@pytest.mark.skipif(not LIVE, reason="需要 LAOYOU_LIVE_TTS=1")
@pytest.mark.parametrize("dialect", ["zh-CN", "northeast", "yue-HK", "sichuan"])
def test_live_synthesis_returns_mp3(dialect):
    audio, voice = asyncio.run(synthesize("早上七点，记得吃药啊。", dialect))
    assert len(audio) > 1000, f"{dialect} 合成音频过短"
    # MP3 帧同步字或 ID3 头
    assert audio[:3] == b"ID3" or audio[:2] in (b"\xff\xfb", b"\xff\xf3", b"\xff\xf2")
    assert voice in DIALECT_VOICES.values()
