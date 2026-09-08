import asyncio
import base64
from datetime import datetime, timezone
import hashlib
import hmac
from urllib.parse import parse_qs, urlparse

import pytest

from app.integrations.xfyun import SpeechServiceError, is_configured, result_text, signed_url, transcribe_pcm


def test_signature_matches_protocol():
    now = datetime(2026, 9, 8, 4, 0, tzinfo=timezone.utc)
    query = parse_qs(urlparse(signed_url('test-key', 'test-secret', now)).query)
    date = 'Tue, 08 Sep 2026 04:00:00 GMT'
    expected = base64.b64encode(hmac.new(b'test-secret',
        f'host: iat-api.xfyun.cn\ndate: {date}\nGET /v2/iat HTTP/1.1'.encode(), hashlib.sha256).digest()).decode()
    auth = base64.b64decode(query['authorization'][0]).decode()
    assert f'signature="{expected}"' in auth
    assert query['date'] == [date]


def test_missing_configuration_never_returns_fake_transcription(monkeypatch):
    for key in ['XFYUN_APP_ID', 'XFYUN_API_KEY', 'XFYUN_API_SECRET']:
        monkeypatch.delenv(key, raising=False)
    assert not is_configured()
    with pytest.raises(SpeechServiceError) as error:
        asyncio.run(transcribe_pcm(bytes(3200)))
    assert error.value.status_code == 503


def test_results_use_top_candidate_and_preserve_sequence():
    assert result_text({'code': 0, 'data': {'status': 2, 'result': {'sn': 3, 'ws': [
        {'cw': [{'w': '你好'}, {'w': '其他'}]}, {'cw': [{'w': '通通'}]}]}}}) == (3, '你好通通', True)
    with pytest.raises(SpeechServiceError, match='11200'):
        result_text({'code': 11200, 'message': 'not authorized'})
