"""方言语音合成（TTS）。

用微软 Edge 神经网络语音的公开接口，无需 API Key。之所以自己做一层而不是
让浏览器说：浏览器 ``speechSynthesis`` 只按 BCP-47 选系统音色，四川话/东北话
在多数设备上根本没有对应音色，只能拿普通话念方言文本。

音频不落盘，直接以字节流返回给调用方。
"""
from __future__ import annotations

import asyncio
import hashlib
import os

# 按方言选择音色。键与 VALID_DIALECTS 保持一致。
#
# 注意：音色列表里有、不代表能合成。实测 zh-HK-HiuGaaiNeural 在
# edge-tts 7.x 上返回 NoAudioReceived，而 HiuMaan / WanLung 正常。
# 新增音色必须实测过再写进来。
DIALECT_VOICES: dict[str, str] = {
    "zh-CN": "zh-CN-XiaoxiaoNeural",
    "northeast": "zh-CN-liaoning-XiaobeiNeural",
    "yue-HK": "zh-HK-HiuMaanNeural",
}

# 四川话目前没有可用的免费音色：识别侧会归一化为普通话，
# 播报侧只能用普通话音色读出规范化后的文本。这里不假装有川味。
DIALECT_FALLBACK: dict[str, str] = {
    "sichuan": "zh-CN",
}

DEFAULT_RATE = "-4%"
DEFAULT_PITCH = "+0Hz"
MAX_TEXT_LENGTH = 300
SYNTH_TIMEOUT_SECONDS = 15.0
CACHE_MAX_ENTRIES = 64

_cache: dict[str, bytes] = {}


class SpeechServiceError(Exception):
    def __init__(self, message: str, status_code: int = 502):
        self.status_code = status_code
        super().__init__(message)


def is_configured() -> bool:
    """edge-tts 走公开接口，不需要凭据；只检查依赖是否装好。

    设 LAOYOU_DISABLE_TTS=1 可显式关闭，便于离线部署时快速降级。
    """
    if os.environ.get("LAOYOU_DISABLE_TTS", "").strip() in {"1", "true", "yes"}:
        return False
    try:
        import edge_tts  # noqa: F401
    except ImportError:
        return False
    return True


def resolve_voice(dialect: str) -> str | None:
    """方言 -> 音色。没有专属音色时回落到普通话音色，而不是报错。"""
    if dialect in DIALECT_VOICES:
        return DIALECT_VOICES[dialect]
    fallback = DIALECT_FALLBACK.get(dialect)
    if fallback:
        return DIALECT_VOICES.get(fallback)
    return None


def has_dialect_voice(dialect: str) -> bool:
    """是否真的有该方言的专属音色（而非回落普通话）。"""
    return dialect in DIALECT_VOICES


async def synthesize(text: str, dialect: str = "zh-CN") -> tuple[bytes, str]:
    """合成语音，返回 (MP3字节, 实际使用的音色)。

    Raises SpeechServiceError:
      503 未安装依赖或被显式关闭
      422 文本不合法 / 无可用音色
      502 上游失败
    """
    if not is_configured():
        raise SpeechServiceError("语音播报服务未启用。", 503)

    cleaned = _clean_text(text)
    if not cleaned:
        raise SpeechServiceError("没有可播报的文字。", 422)
    if len(cleaned) > MAX_TEXT_LENGTH:
        raise SpeechServiceError(f"播报文字过长，请控制在 {MAX_TEXT_LENGTH} 字以内。", 422)

    voice = resolve_voice(dialect)
    if voice is None:
        raise SpeechServiceError("当前没有可用的播报音色。", 422)

    key = _cache_key(cleaned, voice)
    cached = _cache.get(key)
    if cached is not None:
        return cached, voice

    import edge_tts

    communicate = edge_tts.Communicate(
        cleaned, voice, rate=DEFAULT_RATE, pitch=DEFAULT_PITCH
    )
    chunks: list[bytes] = []
    try:
        async with asyncio.timeout(SYNTH_TIMEOUT_SECONDS):
            async for chunk in communicate.stream():
                if chunk.get("type") == "audio" and chunk.get("data"):
                    chunks.append(chunk["data"])
    except TimeoutError as exc:
        raise SpeechServiceError("语音合成超时，请重试。") from exc
    except Exception as exc:
        # 上游异常类型随版本变化，统一收敛为 502，不回显原始消息。
        raise SpeechServiceError("语音合成服务暂时不可用。") from exc

    audio = b"".join(chunks)
    if not audio:
        raise SpeechServiceError("语音合成没有返回音频。")

    _remember(key, audio)
    return audio, voice


def _clean_text(text: str) -> str:
    return " ".join(str(text).split()).strip()


def _cache_key(text: str, voice: str) -> str:
    return hashlib.sha256(f"{voice}|{DEFAULT_RATE}|{DEFAULT_PITCH}|{text}".encode()).hexdigest()


def _remember(key: str, audio: bytes) -> None:
    """近似 FIFO 的小缓存：重复文案（问候语、固定提醒）不再走网络。"""
    if len(_cache) >= CACHE_MAX_ENTRIES:
        oldest = next(iter(_cache), None)
        if oldest is not None:
            _cache.pop(oldest, None)
    _cache[key] = audio
