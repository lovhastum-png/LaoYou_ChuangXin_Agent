"""Cloudflare Realtime TURN 的短期凭据适配器（跨网络通话中继）。

为什么需要它：局域网内两端靠 STUN 直接打洞就能通；一旦跨网络（家庭宽带 +
4G/5G，或两端都在运营商 NAT 之后），P2P 直连经常打不通，必须落到 TURN 中继。
TURN key 是长期密钥，只能留在后端；后端按 TTL 换取短期 username/credential
再下发给浏览器，浏览器拿不到 key 本身。

没有配置 TURN 时返回公共 STUN，行为与改造前一致；通话页拉取失败同样回落到
公共 STUN，不会因为这一个请求把通话整体卡死。

凭据在进程内缓存到接近过期才刷新，避免每次打开通话页都打一次 Cloudflare API。

接口参考：https://developers.cloudflare.com/realtime/turn/generate-credentials/
"""
from __future__ import annotations

import json
import os
import threading
import time
from typing import Any

import httpx

CREDENTIALS_URL = (
    "https://rtc.live.cloudflare.com/v1/turn/keys/{key_id}"
    "/credentials/generate-ice-servers"
)
REQUEST_TIMEOUT_SECONDS = 8.0
DEFAULT_TTL_SECONDS = 3600
MIN_TTL_SECONDS = 300
MAX_TTL_SECONDS = 86400
# 距过期还差这么久就重新申请，避免通话中途凭据失效。
REFRESH_MARGIN_SECONDS = 300

# 无 TURN 配置时的公共 STUN，与通话页改造前的取值保持一致。
FALLBACK_ICE_SERVERS: tuple[dict[str, Any], ...] = (
    {"urls": "stun:stun.l.google.com:19302"},
)
VALID_ICE_PREFIXES = ("stun:", "stuns:", "turn:", "turns:")

_cache_lock = threading.Lock()
_cache: tuple[float, tuple[dict[str, Any], ...]] | None = None


class TurnServiceError(Exception):
    def __init__(self, message: str, status_code: int = 502):
        self.status_code = status_code
        super().__init__(message)


def _env(name: str) -> str:
    return os.environ.get(name, "").strip()


def is_configured() -> bool:
    """Cloudflare TURN 是否可用；key 与 API token 必须成对出现。"""
    return bool(_env("CLOUDFLARE_TURN_KEY_ID") and _env("CLOUDFLARE_TURN_API_TOKEN"))


def clamp_ttl(ttl_seconds: int) -> int:
    return max(MIN_TTL_SECONDS, min(MAX_TTL_SECONDS, int(ttl_seconds)))


def cache_clear() -> None:
    """清空进程内凭据缓存（测试与强制刷新用）。"""
    global _cache
    with _cache_lock:
        _cache = None


def _valid_entry(entry: Any) -> bool:
    if not isinstance(entry, dict):
        return False
    urls = entry.get("urls")
    candidates = [urls] if isinstance(urls, str) else urls if isinstance(urls, list) else []
    return any(
        isinstance(url, str) and url.startswith(VALID_ICE_PREFIXES) for url in candidates
    )


def _static_ice_servers() -> tuple[dict[str, Any], ...] | None:
    """LAOYOU_ICE_SERVERS：自建 coturn 等静态中继的逃生口（JSON 数组或对象）。"""
    raw = _env("LAOYOU_ICE_SERVERS")
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except ValueError as exc:
        raise TurnServiceError(
            "LAOYOU_ICE_SERVERS 不是合法 JSON，无法作为 ICE 配置使用。", 500
        ) from exc
    entries = parsed if isinstance(parsed, list) else [parsed]
    cleaned = tuple(item for item in entries if _valid_entry(item))
    if not cleaned:
        raise TurnServiceError("LAOYOU_ICE_SERVERS 里没有可用的 ICE 条目。", 500)
    return cleaned


def _fetch_from_cloudflare(
    key_id: str, api_token: str, ttl_seconds: int
) -> tuple[dict[str, Any], ...]:
    headers = {
        "Authorization": f"Bearer {api_token}",
        "Content-Type": "application/json",
    }
    try:
        response = httpx.post(
            CREDENTIALS_URL.format(key_id=key_id),
            json={"ttl": ttl_seconds},
            headers=headers,
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
    except httpx.TimeoutException as exc:
        raise TurnServiceError("TURN 凭据申请超时，请重试。", 504) from exc
    except httpx.HTTPError as exc:
        raise TurnServiceError("TURN 服务暂时不可用，请重试。") from exc

    if response.status_code in {401, 403}:
        # 只上报状态码，不回显响应体：其中可能包含请求细节。
        raise TurnServiceError(
            "TURN 凭据被拒绝，请检查 Cloudflare TURN Key 与 API Token。"
        )
    if response.status_code >= 400:
        raise TurnServiceError(
            f"TURN 服务返回错误 {response.status_code}，请检查凭据与免费额度。"
        )

    try:
        payload = response.json()
    except ValueError as exc:
        raise TurnServiceError("TURN 服务返回了无法解析的结果。") from exc

    raw_servers = payload.get("iceServers") if isinstance(payload, dict) else None
    if not isinstance(raw_servers, list):
        raise TurnServiceError("TURN 服务没有返回 iceServers 列表。")
    servers = tuple(item for item in raw_servers if _valid_entry(item))
    if not servers:
        raise TurnServiceError("TURN 服务返回的 iceServers 不可用。")
    return servers


def fetch_ice_servers(
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
) -> tuple[list[dict[str, Any]], str]:
    """返回 ``(iceServers, source)``。

    source 取值：``cloudflare_turn``（动态短期凭据）、``static``（
    LAOYOU_ICE_SERVERS 指定的中继）、``stun_only``（只有公共 STUN）。
    同时配置 Cloudflare 与静态列表时优先 Cloudflare。
    """
    global _cache

    ttl = clamp_ttl(ttl_seconds)

    if is_configured():
        now = time.monotonic()
        with _cache_lock:
            cached = _cache
        if cached is not None and now < cached[0]:
            return [dict(item) for item in cached[1]], "cloudflare_turn"

        servers = _fetch_from_cloudflare(
            _env("CLOUDFLARE_TURN_KEY_ID"), _env("CLOUDFLARE_TURN_API_TOKEN"), ttl
        )
        with _cache_lock:
            _cache = (
                time.monotonic() + max(ttl - REFRESH_MARGIN_SECONDS, MIN_TTL_SECONDS),
                servers,
            )
        return [dict(item) for item in servers], "cloudflare_turn"

    static = _static_ice_servers()
    if static is not None:
        return [dict(item) for item in static], "static"

    return [dict(item) for item in FALLBACK_ICE_SERVERS], "stun_only"
