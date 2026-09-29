"""通义千问语音适配器的边界行为测试。

这些测试不访问网络：只校验配置判定、参数校验、提示词组装和返回解析。
真实识别效果须用音频实测，不能由单元测试推断。
"""
import asyncio
import struct

import pytest

from app.integrations.qwen_asr import (
    SpeechServiceError,
    build_prompt,
    is_configured,
    transcribe_audio,
    wrap_pcm_as_wav,
)


def test_missing_configuration_never_returns_fake_transcription(monkeypatch):
    monkeypatch.delenv("QWEN_API_KEY", raising=False)
    assert not is_configured()
    with pytest.raises(SpeechServiceError) as error:
        asyncio.run(transcribe_audio(bytes(32), "sichuan"))
    assert error.value.status_code == 503


def test_rejects_unknown_dialect(monkeypatch):
    monkeypatch.setenv("QWEN_API_KEY", "test-key")
    with pytest.raises(SpeechServiceError) as error:
        asyncio.run(transcribe_audio(bytes(32), "klingon"))
    assert error.value.status_code == 422


def test_rejects_empty_and_oversized_audio(monkeypatch):
    monkeypatch.setenv("QWEN_API_KEY", "test-key")
    for payload in (b"", b"\x00" * (10 * 1024 * 1024 + 2)):
        with pytest.raises(SpeechServiceError) as error:
            asyncio.run(transcribe_audio(payload, "sichuan"))
        assert error.value.status_code == 422


def test_dialect_prompt_asks_for_normalized_mandarin():
    prompt = build_prompt("sichuan")
    assert "四川方言" in prompt
    assert "规范" in prompt
    # 普通话不做归一，避免把正常表达"改写"掉
    assert build_prompt("zh-CN") == build_prompt("zh-CN", normalize=False)


def test_unknown_dialect_label_falls_back_to_plain_prompt():
    assert build_prompt("yue-HK") != build_prompt("zh-CN")
    assert "粤语" in build_prompt("yue-HK")


def test_wrap_pcm_as_wav_produces_a_readable_header():
    pcm = b"\x01\x02" * 800  # 1600 字节，16kHz 单声道正好 0.05 秒
    wav = wrap_pcm_as_wav(pcm)
    assert wav[:4] == b"RIFF"
    assert wav[8:12] == b"WAVE"
    assert wav[36:40] == b"data"
    assert len(wav) == 44 + len(pcm)
    # 采样率写在 offset 24，位深在 offset 34
    assert struct.unpack("<I", wav[24:28])[0] == 16000
    assert struct.unpack("<H", wav[34:36])[0] == 16
