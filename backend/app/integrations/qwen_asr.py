"""Optional Qwen-Omni audio recognition for Chinese dialects.

Unlike the iFLYTEK streaming adapter, which returns a bare transcript, this
adapter sends audio to a multimodal model and asks it to return **normalized
Mandarin**. That makes dialect speech directly consumable by the local
rule-based assistant without maintaining a dialect lexicon.

Credentials are read from the environment and never leave the backend.
Audio is never written to disk.

Protocol reference: DashScope compatible-mode chat completions with an
``input_audio`` content part.
"""
from __future__ import annotations

import base64
import os
from urllib.parse import urlsplit

import httpx

DEFAULT_ENDPOINT = (
    "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
)
DEFAULT_MODEL = "qwen-omni-turbo"
MAX_AUDIO_BYTES = 10 * 1024 * 1024
DIALECTS = {"zh-CN", "yue-HK", "sichuan", "northeast"}

# 方言标识 -> 提示词里描述的目标口音。
# 不认识的方言回落到通用转写，而不是猜测。
DIALECT_LABELS = {
    "sichuan": "四川方言",
    "northeast": "东北方言",
    "yue-HK": "粤语",
}

_TRANSCRIBE_PROMPT = (
    "请听这段音频，准确转写其中的语音内容。"
    "只输出转写文本，不要任何解释、不要添加标点之外的标记。"
)

_NORMALIZE_PROMPT = (
    "你是{dialect}识别专家。请听这段音频，把它转写成**规范的普通话书面语**，"
    "保留原意，不要逐字音译方言词。"
    "例如把“脑瓜子疼”写成“头疼”，把“咋整”写成“怎么办”。"
    "只输出转写结果，不要任何解释。"
)

# 音频容器 -> MIME / 传给模型的 format 参数。
_FORMATS = {
    "mp3": ("audio/mpeg", "mp3"),
    "wav": ("audio/wav", "wav"),
    "m4a": ("audio/mp4", "m4a"),
    "mp4": ("audio/mp4", "mp4"),
    "ogg": ("audio/ogg", "ogg"),
    "flac": ("audio/flac", "flac"),
    "webm": ("audio/webm", "webm"),
    "pcm": ("audio/wav", "wav"),
    "l16": ("audio/wav", "wav"),
}


class SpeechServiceError(Exception):
    def __init__(self, message: str, status_code: int = 502):
        self.status_code = status_code
        super().__init__(message)


def is_configured() -> bool:
    return bool(os.environ.get("QWEN_API_KEY", "").strip())


def _endpoint() -> str:
    return (
        os.environ.get("QWEN_ASR_URL", "").strip()
        or os.environ.get("QWEN_API_URL", "").strip()
        or DEFAULT_ENDPOINT
    )


def _model() -> str:
    return os.environ.get("QWEN_ASR_MODEL", "").strip() or DEFAULT_MODEL


def _host_of(url: str) -> str:
    """只用于日志和错误提示，避免把完整路径（可能含参数）写进日志。"""
    try:
        return urlsplit(url).hostname or "unknown"
    except ValueError:
        return "unknown"


def build_prompt(dialect: str, *, normalize: bool = True) -> str:
    if not normalize:
        return _TRANSCRIBE_PROMPT
    label = DIALECT_LABELS.get(dialect)
    if not label:
        return _TRANSCRIBE_PROMPT
    return _NORMALIZE_PROMPT.format(dialect=label)


def _resolve_format(filename_or_ext: str) -> tuple[str, str]:
    ext = filename_or_ext.rsplit(".", 1)[-1].lower() if "." in filename_or_ext else filename_or_ext.lower()
    return _FORMATS.get(ext, ("audio/wav", "wav"))


async def transcribe_audio(
    audio: bytes,
    dialect: str = "zh-CN",
    *,
    extension: str = "wav",
    normalize: bool = True,
    timeout: float = 30.0,
) -> str:
    """把音频转成文字；方言默认归一化为普通话书面语。

    Raises SpeechServiceError with a status code the API layer can forward:
      503 未配置凭据
      422 输入不合法
      502 上游失败
    """
    if not is_configured():
        raise SpeechServiceError(
            "尚未配置通义千问语音服务，请使用设备语音识别或文字输入。", 503
        )
    if dialect not in DIALECTS:
        raise SpeechServiceError("不支持的方言选项。", 422)
    if not audio:
        raise SpeechServiceError("没有收到音频数据。", 422)
    if len(audio) > MAX_AUDIO_BYTES:
        raise SpeechServiceError("音频文件过大，请缩短录音后重试。", 422)

    mime, fmt = _resolve_format(extension)
    data_uri = f"data:{mime};base64,{base64.b64encode(audio).decode()}"
    url = _endpoint()
    payload = {
        "model": _model(),
        "messages": [
            {
                "role": "system",
                "content": [{"type": "text", "text": build_prompt(dialect, normalize=normalize)}],
            },
            {
                "role": "user",
                "content": [
                    {"type": "input_audio", "input_audio": {"data": data_uri, "format": fmt}},
                    {"type": "text", "text": "请按要求输出。"},
                ],
            },
        ],
        "temperature": 0.1,
    }
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {os.environ['QWEN_API_KEY']}",
    }

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(url, json=payload, headers=headers)
    except httpx.TimeoutException as exc:
        raise SpeechServiceError("语音识别超时，请重试或改用文字输入。") from exc
    except httpx.HTTPError as exc:
        raise SpeechServiceError("语音服务暂时不可用，请重试或使用文字输入。") from exc

    if response.status_code >= 400:
        # 只上报状态码和上游主机，不回显响应体：其中可能包含请求细节。
        raise SpeechServiceError(
            f"语音识别服务返回错误 {response.status_code}（{_host_of(url)}），"
            "请检查服务授权或额度。"
        )

    try:
        data = response.json()
    except ValueError as exc:
        raise SpeechServiceError("语音服务返回了无法解析的结果，请重试。") from exc

    text = _extract_text(data)
    if not text:
        raise SpeechServiceError("没有识别到清楚的语音，请重试。", 422)
    return text


def _extract_text(data: dict) -> str:
    """兼容多种返回结构：content 可能是字符串或分片数组。"""
    raw = (
        (data.get("choices") or [{}])[0].get("message", {}).get("content")
        or (data.get("output", {}).get("choices") or [{}])[0].get("message", {}).get("content")
        or data.get("output", {}).get("text")
        or ""
    )
    if isinstance(raw, list):
        raw = "".join(
            part if isinstance(part, str) else (part or {}).get("text", "")
            for part in raw
        )
    return _clean(str(raw))


def wrap_pcm_as_wav(pcm: bytes, *, sample_rate: int = 16_000, channels: int = 1) -> bytes:
    """给裸 PCM 补一个 WAV 头。

    前端上传的是 audio/L16 原始字节，没有容器信息。多模态模型需要能自解释的
    音频，所以这里现场拼一个 44 字节的 WAV 头送过去。不写磁盘。
    """
    import struct

    bits = 16
    byte_rate = sample_rate * channels * bits // 8
    block_align = channels * bits // 8
    header = b"RIFF" + struct.pack("<I", 36 + len(pcm)) + b"WAVE"
    header += b"fmt " + struct.pack(
        "<IHHIIHH", 16, 1, channels, sample_rate, byte_rate, block_align, bits
    )
    header += b"data" + struct.pack("<I", len(pcm))
    return header + pcm


def _clean(text: str) -> str:
    """去掉模型可能加上的对话式前缀和引号包裹。"""
    value = text.strip()
    value = value.replace("\n", "").strip()
    for prefix in ("识别结果", "转写结果", "唱词", "音频内容", "转写"):
        if value.startswith(prefix):
            value = value[len(prefix):].lstrip("：: ").strip()
    if len(value) >= 2 and value[0] in "「『\"“" and value[-1] in "」』\"”":
        value = value[1:-1].strip()
    return value
