from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app


def test_known_spa_routes_fallback_to_index_but_unknown_api_stays_404():
    with TestClient(app) as client:
        for path in ("/reminders", "/health", "/safety", "/family", "/calls", "/call/demo-id"):
            response = client.get(path)
            assert response.status_code == 200, (path, response.text)
            assert response.headers["content-type"].startswith("text/html")
            assert "<html" in response.text.lower()
        assert client.get("/api/not-found").status_code == 404
        assert client.get("/unknown-page").status_code == 404
