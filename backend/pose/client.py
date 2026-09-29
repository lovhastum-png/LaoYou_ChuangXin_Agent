"""后端观测注入客户端。

复用老友项目已有的 POST /api/elders/{elder_id}/observations 接口，
不新增后端接口、不改动现有规则引擎。

后端约束（见 backend/app/schemas.py 与 main.py 的 _validate_observation_input）：
- kind 必须是 Literal 枚举内取值，"fall" 合法。
- source 不得以 live / live_ 开头（会被 422 拒绝）。
- source 以 camera 开头时，要求 elder.camera_enabled 为真（否则 409）。
- 只有 child / admin 角色可注入。
"""

from __future__ import annotations

import json
import os
import time
import uuid

import requests


class ObservationClient:
    """登录并注入观测的最小客户端。"""

    def __init__(self, cfg):
        self.cfg = cfg
        self._token: str | None = None
        self.session = requests.Session()
        self.session.trust_env = False  # 避免走系统代理访问本机后端
        self._last_sent: dict[str, float] = {}  # 限流用：tag -> 上次发送时刻

    # ------------------------------------------------------------------ #
    def _url(self, path: str) -> str:
        return f"{self.cfg.base_url.rstrip('/')}/{path.lstrip('/')}"

    def login(self) -> str:
        if not self.cfg.password:
            raise RuntimeError(
                "未提供密码。请设置环境变量 LAOYOU_PASSWORD，或用 --password 传入。"
            )
        resp = self.session.post(
            self._url("/auth/login"),
            json={"username": self.cfg.username, "password": self.cfg.password},
            timeout=15,
        )
        if resp.status_code != 200:
            raise RuntimeError(f"登录失败 HTTP {resp.status_code}: {resp.text[:200]}")
        data = resp.json()
        token = data.get("token") or data.get("access_token")
        if not token:
            raise RuntimeError(f"登录响应缺少 token 字段：{list(data.keys())}")
        self._token = token
        return token

    def _headers(self) -> dict[str, str]:
        if self._token is None:
            self.login()
        return {"Authorization": f"Bearer {self._token}"}

    # ------------------------------------------------------------------ #
    def inject_fall(
        self,
        *,
        elder_id: str | None = None,
        occurred_at: str | None = None,
        extra_value: dict | None = None,
    ) -> dict:
        """注入一条 kind=fall 的观测，返回后端响应。"""
        eid = elder_id or self.cfg.elder_id
        if not eid:
            raise RuntimeError(
                "未指定老人 ID。请用 --elder-id 传入，或设置环境变量 LAOYOU_ELDER_ID。"
            )

        payload: dict = {
            "kind": "fall",
            "value": extra_value,
            "source": self.cfg.source,
            "idempotency_key": f"pose-{uuid.uuid4().hex[:16]}",
        }
        if occurred_at:
            payload["occurred_at"] = occurred_at

        resp = self.session.post(
            self._url(f"/elders/{eid}/observations"),
            json=payload,
            headers=self._headers(),
            timeout=20,
        )
        if resp.status_code == 401:
            self._token = None
            resp = self.session.post(
                self._url(f"/elders/{eid}/observations"),
                json=payload,
                headers=self._headers(),
                timeout=20,
            )
        if resp.status_code >= 400:
            raise RuntimeError(
                f"注入观测失败 HTTP {resp.status_code}: {resp.text[:300]}"
            )
        return resp.json()

    # ------------------------------------------------------------------ #
    def inject_observation(
        self,
        *,
        kind: str,
        value: dict | None = None,
        elder_id: str | None = None,
        occurred_at: str | None = None,
        min_interval_seconds: float = 0.0,
        dedupe_tag: str | None = None,
    ) -> dict | None:
        """注入一条任意 kind 的观测（供平安看护等场景复用）。

        上限流：min_interval_seconds > 0 时，同一 dedupe_tag 在这个间隔内
        只发一次，避免状态抖动导致观测表被灌爆。

        返回后端响应；被限流跳过时返回 None。
        """
        eid = elder_id or self.cfg.elder_id
        if not eid:
            raise RuntimeError(
                "未指定老人 ID。请用 --elder-id 传入，或设置环境变量 LAOYOU_ELDER_ID。"
            )

        if min_interval_seconds > 0 and dedupe_tag:
            now = time.time()
            last = self._last_sent.get(dedupe_tag)
            if last is not None and now - last < min_interval_seconds:
                return None

        payload: dict = {
            "kind": kind,
            "value": value,
            "source": self.cfg.source,
            "idempotency_key": f"pose-{uuid.uuid4().hex[:16]}",
        }
        if occurred_at:
            payload["occurred_at"] = occurred_at

        resp = self.session.post(
            self._url(f"/elders/{eid}/observations"),
            json=payload,
            headers=self._headers(),
            timeout=20,
        )
        if resp.status_code == 401:
            self._token = None
            resp = self.session.post(
                self._url(f"/elders/{eid}/observations"),
                json=payload,
                headers=self._headers(),
                timeout=20,
            )
        if resp.status_code >= 400:
            raise RuntimeError(
                f"注入观测失败 HTTP {resp.status_code}: {resp.text[:300]}"
            )
        if min_interval_seconds > 0 and dedupe_tag:
            self._last_sent[dedupe_tag] = time.time()
        return resp.json()

    # ------------------------------------------------------------------ #
    def save_capture(self, frame_bytes: bytes, stamp: float | None = None) -> str:
        """落盘告警时的快照，便于人工复核误报。

        注意：stamp 传进来的通常是「视频内时间戳」而非墙钟时间，
        只有它是合理的 Unix 时间才用它命名，否则一律用当前时间，
        避免出现 1970 年的文件名。
        """
        os.makedirs(self.cfg.capture_dir, exist_ok=True)
        wall = time.time()
        # 合理的 Unix 时间下限：2001-09-09。视频内时间戳（几秒）会落在此之下。
        if stamp is not None and stamp > 1_000_000_000:
            wall = stamp
        name = time.strftime("fall-%Y%m%d-%H%M%S", time.localtime(wall))
        path = os.path.join(self.cfg.capture_dir, f"{name}.jpg")
        # 同一秒内多次告警时避免互相覆盖
        if os.path.exists(path):
            path = os.path.join(
                self.cfg.capture_dir, f"{name}-{int(wall % 1000):03d}.jpg"
            )
        with open(path, "wb") as fh:
            fh.write(frame_bytes)
        return path
