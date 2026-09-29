from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app

# 后端只在 web/dist 存在时挂载 SPA 回退路由，因此未构建前端时该用例必然失败。
# 先构建再跑：pnpm --dir web build
WEB_DIST = Path(__file__).resolve().parents[2] / "web" / "dist"

pytestmark = pytest.mark.skipif(
    not WEB_DIST.is_dir(),
    reason="未找到 web/dist，请先执行 pnpm --dir web build",
)


def test_known_spa_routes_fallback_to_index_but_unknown_api_stays_404():
    # 不用 `with TestClient(app)`：那会触发 lifespan → init_db，从而要求真实数据库。
    # SPA 回退路由是在导入时按 web/dist 是否存在挂载的，这里不需要生命周期。
    client = TestClient(app)
    for path in ("/reminders", "/health", "/safety", "/family", "/calls", "/call/demo-id"):
        response = client.get(path)
        assert response.status_code == 200, (path, response.text)
        assert response.headers["content-type"].startswith("text/html")
        assert "<html" in response.text.lower()
    assert client.get("/api/not-found").status_code == 404
    assert client.get("/unknown-page").status_code == 404
