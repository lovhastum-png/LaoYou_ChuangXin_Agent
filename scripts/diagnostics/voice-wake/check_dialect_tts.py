"""实测各方言的 TTS 合成是否真的可用（只读，不写任何业务数据）。

用法：backend/.venv/Scripts/python.exe -X utf8 tmp/check_dialect_tts.py
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

for key in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY", "all_proxy", "ALL_PROXY"):
    os.environ.pop(key, None)

BASE = "http://127.0.0.1:8000/api"


def request(method, path, payload=None, token=None):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=40) as resp:
            return resp.status, resp.read(), dict(resp.headers)
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read(), dict(exc.headers)


status, body, _ = request("POST", "/auth/login", {"username": "admin", "password": "Laoyou123!"})
print("login:", status)
if status != 200:
    raise SystemExit("login failed")
token = json.loads(body)["token"]

status, body, _ = request("GET", "/elders", token=token)
print("GET /elders:", status)
elders = json.loads(body) if status == 200 else []
if isinstance(elders, dict):
    elders = elders.get("items", [])
if not elders:
    raise SystemExit(f"no elder: {body[:200]!r}")
elder = elders[0]
print("elder:", elder["id"], elder.get("name"), "dialect =", elder.get("dialect"))

TEXT = "你好，我是通通。今天天气不错。"
for dialect in ("zh-CN", "yue-HK", "northeast", "sichuan"):
    status, body, headers = request(
        "POST",
        f"/elders/{elder['id']}/speech/synthesize",
        {"text": TEXT, "dialect": dialect},
        token=token,
    )
    detail = ""
    if status != 200:
        detail = " <- " + body.decode("utf-8", "replace")[:120]
    print(
        f"  {dialect:10s} status={status} bytes={len(body):7d} "
        f"X-Voice={headers.get('X-Voice', '-')} X-Dialect={headers.get('X-Dialect', '-')}{detail}"
    )
