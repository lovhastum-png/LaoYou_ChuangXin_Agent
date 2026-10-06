"""药盒标签识别：把药品包装照片转成结构化的用药提醒字段。

给谁用：不识字、认不清药盒的老人。拍一张药盒照片，自动认出药名和剂量，
由家属或本人核对后一键存成用药提醒。

两条设计底线：

1. **只识别，不落库。** 识别结果回给前端填进表单，仍然走原有的
   ``POST /api/elders/{id}/reminders`` 才能变成真正的提醒——
   模型看错一个字不会直接变成老人的服药安排。
2. **不静默降级。** 没配密钥就显式报 503，绝不返回编造的药品信息。

密钥只留在后端，照片不写盘、只在本次请求里转成 base64 传给模型。

协议：OpenAI 兼容的 chat/completions，``image_url`` 内容块 + data URL。
网关强制要求 ``stream: true``，且必须带 system 消息。
"""
from __future__ import annotations

import base64
import json
import logging
import re
from typing import Any

import httpx

from ..config import Settings

logger = logging.getLogger("laoyou.vlm")

# 请求体全局上限是 4 MB（BodySizeLimitMiddleware），base64 会再膨胀约 1/3，
# 所以原图按 2.5 MB 兜底；前端还会先把长边缩到 1280 再上传，正常只有几百 KB。
MAX_IMAGE_BYTES = 2_500_000

_SYSTEM_PROMPT = (
    "你是药品包装识别助手，帮助不识字的老人的家属录入用药提醒。"
    "你只会看到一张照片，可能是药盒正面、药板背面或说明书。"
    "请识别出最关键的用药信息，并严格按要求返回 JSON。"
)

_USER_PROMPT = (
    "请看这张药品包装照片，识别并返回 JSON：\n"
    "{\n"
    '  "medicine": "药品通用名，如 苯磺酸氨氯地平片；看不清就填空字符串",\n'
    '  "dose": "单次用量含规格，如 5mg x 1片；照片没写就填空字符串",\n'
    '  "frequency": "服用频次，如 每日一次、每晚睡前；没写就填空字符串",\n'
    '  "raw_text": "你在照片上看到的主要文字，原样抄录",\n'
    '  "confidence": "high / medium / low，表示你对上述识别的把握"\n'
    "}\n"
    "只输出 JSON，不要解释，不要 markdown 代码块。\n"
    "绝对不要编造照片上没有的信息；看不清的字段一律留空字符串。"
)

# 频次 -> 建议时间。只做最保守的推断，其余交给用户自己改。
_TIME_HINTS = (
    (("睡前", "晚上", "晚间", "晚"), "20:00"),
    (("中午", "午间"), "12:00"),
    (("早上", "晨起", "清晨", "早", "每日一次", "一天一次"), "08:00"),
)


class LabelRecognitionError(Exception):
    """识别失败。status_code 直接用于 HTTP 响应，消息面向使用者。"""

    def __init__(self, message: str, status_code: int = 502):
        self.status_code = status_code
        super().__init__(message)


def is_configured(settings: Settings) -> bool:
    return bool(settings.vlm_api_key.strip())


def _detect_mime(data: bytes) -> str:
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return "image/jpeg"


def _extract_json(text: str) -> dict[str, Any] | None:
    """模型偶尔会用 ```json 包裹或加一句客套话，这里都兜住。"""
    cleaned = text.strip()
    cleaned = re.sub(r"^```[a-zA-Z]*\s*", "", cleaned)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    for candidate in (cleaned,):
        try:
            obj = json.loads(candidate)
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            pass
    match = re.search(r"\{.*\}", cleaned, re.S)
    if match:
        try:
            obj = json.loads(match.group(0))
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            return None
    return None


def _guess_time(frequency: str) -> str:
    for keys, value in _TIME_HINTS:
        if any(key in frequency for key in keys):
            return value
    return ""


def recognize_medicine_label(
    image_bytes: bytes, settings: Settings, *, actor: str = ""
) -> dict[str, Any]:
    """识别一张药品包装照片，返回可填入提醒表单的字段。

    失败一律抛 :class:`LabelRecognitionError`，调用方直接转成 HTTP 错误。
    """
    if not is_configured(settings):
        raise LabelRecognitionError(
            "未配置药盒识别服务，请在后端设置 LAOYOU_VLM_KEY", status_code=503
        )
    if not image_bytes:
        raise LabelRecognitionError("没有收到照片", status_code=400)
    if len(image_bytes) > MAX_IMAGE_BYTES:
        raise LabelRecognitionError("照片太大，请重新拍一张", status_code=413)

    mime = _detect_mime(image_bytes)
    b64 = base64.b64encode(image_bytes).decode("ascii")
    payload = {
        "model": settings.vlm_model,
        "stream": True,
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": _USER_PROMPT},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:{mime};base64,{b64}"},
                    },
                ],
            },
        ],
    }
    headers = {
        "Authorization": f"Bearer {settings.vlm_api_key}",
        "X-API-Key": settings.vlm_api_key,
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
    }

    logger.info(
        "药盒识别请求 actor=%s bytes=%d mime=%s model=%s",
        actor or "-", len(image_bytes), mime, settings.vlm_model,
    )

    chunks: list[str] = []
    try:
        # 环境里注入了 http_proxy，这里必须绕开，否则本机请求会走代理。
        with httpx.Client(timeout=settings.vlm_timeout_seconds, trust_env=False) as client:
            with client.stream("POST", settings.vlm_url, json=payload, headers=headers) as resp:
                if resp.status_code != 200:
                    resp.read()
                    body = resp.text[:200]
                    logger.warning("药盒识别上游返回 %s: %s", resp.status_code, body)
                    raise LabelRecognitionError(
                        "识别服务暂时不可用，请稍后再试", status_code=502
                    )
                for line in resp.iter_lines():
                    if not line or not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        obj = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    for choice in obj.get("choices", []):
                        delta = choice.get("delta") or {}
                        if delta.get("content"):
                            chunks.append(delta["content"])
    except LabelRecognitionError:
        raise
    except httpx.TimeoutException as exc:
        raise LabelRecognitionError("识别超时，请检查网络后重试", status_code=504) from exc
    except httpx.HTTPError as exc:
        logger.warning("药盒识别网络错误: %s", exc)
        raise LabelRecognitionError("识别服务连接失败，请稍后再试", status_code=502) from exc

    content = "".join(chunks).strip()
    parsed = _extract_json(content)
    if parsed is None:
        logger.warning("药盒识别结果无法解析: %r", content[:200])
        raise LabelRecognitionError("没能看懂这张照片，请对着药盒正面再拍一张", status_code=422)

    medicine = str(parsed.get("medicine") or "").strip()
    dose = str(parsed.get("dose") or "").strip()
    frequency = str(parsed.get("frequency") or "").strip()
    raw_text = str(parsed.get("raw_text") or "").strip()
    confidence = str(parsed.get("confidence") or "").strip().lower()
    if confidence not in {"high", "medium", "low"}:
        confidence = ""

    if not medicine and not dose:
        raise LabelRecognitionError(
            "照片里没认出药品名，请对着药盒正面、光线亮一点再拍一张", status_code=422
        )

    result = {
        "medicine": medicine,
        "dose": dose,
        "frequency": frequency,
        "raw_text": raw_text,
        "confidence": confidence,
        "title": f"{medicine}提醒" if medicine else "用药提醒",
        "suggested_time": _guess_time(frequency),
        "note": "识别结果由大模型生成，可能有误。请核对药名和剂量后再保存。",
    }
    logger.info(
        "药盒识别完成 actor=%s medicine=%r dose=%r confidence=%s",
        actor or "-", medicine, dose, confidence or "-",
    )
    return result
