from __future__ import annotations

import logging

from app.main import _AccessTokenRedactor


def test_access_log_redacts_query_token_but_keeps_request_line():
    record = logging.LogRecord(
        "uvicorn.access",
        logging.INFO,
        __file__,
        1,
        '%s - "%s %s HTTP/%s" %d',
        ("127.0.0.1", "GET", "/call/abc?token=secret-value&x=1", "1.1", 200),
        None,
    )
    _AccessTokenRedactor().filter(record)
    rendered = record.getMessage()
    assert "secret-value" not in rendered
    assert "/call/abc?token=<redacted>&x=1" in rendered
    assert "200" in rendered
