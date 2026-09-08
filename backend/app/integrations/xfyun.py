"""Optional iFLYTEK streaming ASR. Credentials never leave the backend.

Protocol: https://www.xfyun.cn/doc/asr/voicedictation/API.html
The dialect domain requires an enabled multi-dialect service on the provider account.
"""
from __future__ import annotations

import asyncio
import base64
import contextlib
from datetime import datetime, timezone
from email.utils import format_datetime
import hashlib
import hmac
import json
import os
from urllib.parse import urlencode

from websockets.asyncio.client import connect
from websockets.exceptions import WebSocketException

HOST = 'iat-api.xfyun.cn'
PATH = '/v2/iat'
MAX_PCM_BYTES = 30 * 16000 * 2
DIALECTS = {'zh-CN', 'yue-HK', 'sichuan', 'northeast'}


class SpeechServiceError(Exception):
    def __init__(self, message: str, status_code: int = 502):
        self.status_code = status_code
        super().__init__(message)


def is_configured() -> bool:
    return all(os.environ.get(name, '').strip()
               for name in ('XFYUN_APP_ID', 'XFYUN_API_KEY', 'XFYUN_API_SECRET'))


def signed_url(api_key: str, api_secret: str, now: datetime | None = None) -> str:
    date = format_datetime((now or datetime.now(timezone.utc)).astimezone(timezone.utc), usegmt=True)
    canonical = f'host: {HOST}\ndate: {date}\nGET {PATH} HTTP/1.1'
    signature = base64.b64encode(hmac.new(api_secret.encode(), canonical.encode(), hashlib.sha256).digest()).decode()
    authorization = f'api_key="{api_key}", algorithm="hmac-sha256", headers="host date request-line", signature="{signature}"'
    return f'wss://{HOST}{PATH}?' + urlencode({
        'authorization': base64.b64encode(authorization.encode()).decode(), 'date': date, 'host': HOST,
    })


def result_text(message: dict) -> tuple[int | None, str, bool]:
    if message.get('code', 0) != 0:
        # Use provider code only: raw provider messages may contain request details.
        raise SpeechServiceError(f'语音服务返回错误 {message.get("code")}，请检查方言授权或服务额度。')
    data = message.get('data') or {}
    result = data.get('result') or {}
    words = ''.join(part.get('cw', [{}])[0].get('w', '')
                    for part in result.get('ws', []) if part.get('cw'))
    return result.get('sn'), words, data.get('status') == 2


async def transcribe_pcm(audio: bytes, dialect: str = 'zh-CN') -> str:
    if not is_configured():
        raise SpeechServiceError('尚未配置讯飞语音服务，请使用设备语音识别或文字输入。', 503)
    if dialect not in DIALECTS:
        raise SpeechServiceError('不支持的方言选项。', 422)
    if len(audio) < 3200 or len(audio) > MAX_PCM_BYTES or len(audio) % 2:
        raise SpeechServiceError('录音须为0.1至30秒、16kHz单声道16位PCM音频。', 422)

    async def send_audio(socket):
        for index, start in enumerate(range(0, len(audio), 1280)):
            frame = {'data': {'status': 0 if index == 0 else 1,
                              'format': 'audio/L16;rate=16000', 'encoding': 'raw',
                              'audio': base64.b64encode(audio[start:start+1280]).decode()}}
            if index == 0:
                frame['common'] = {'app_id': os.environ['XFYUN_APP_ID']}
                frame['business'] = {'language': 'zh_cn', 'accent': 'mandarin',
                                     'domain': 'iat' if dialect == 'zh-CN' else 'xfime-mianqie',
                                     'eos': 5000, 'ptt': 1}
            await socket.send(json.dumps(frame))
            await asyncio.sleep(0.04)
        await socket.send(json.dumps({'data': {'status': 2, 'format': 'audio/L16;rate=16000',
                                              'encoding': 'raw', 'audio': ''}}))

    async def receive(socket):
        fragments: dict[int, str] = {}
        async for raw in socket:
            sequence, words, finished = result_text(json.loads(raw))
            if sequence is not None:
                fragments[int(sequence)] = words
            if finished:
                text = ''.join(fragments[index] for index in sorted(fragments)).strip()
                if not text:
                    raise SpeechServiceError('没有识别到清楚的语音，请重试。', 422)
                return text
        raise SpeechServiceError('语音连接提前结束，请重试。')

    try:
        async with connect(signed_url(os.environ['XFYUN_API_KEY'], os.environ['XFYUN_API_SECRET']),
                           open_timeout=10, close_timeout=3, max_size=256 * 1024) as socket:
            sender = asyncio.create_task(send_audio(socket))
            try:
                return await asyncio.wait_for(receive(socket), timeout=45)
            finally:
                sender.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await sender
    except SpeechServiceError:
        raise
    except (TimeoutError, OSError, WebSocketException, ValueError, KeyError) as exc:
        raise SpeechServiceError('语音服务暂时不可用，请重试或使用文字输入。') from exc
