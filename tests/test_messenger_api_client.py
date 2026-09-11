import io
import json
import unittest
from urllib.error import HTTPError

import pytest

try:
    from ut_messenger.client.controllers import api_client as api_client_module
    from ut_messenger.client.controllers.api_client import ApiClient, ApiClientError
except ImportError:
    raise unittest.SkipTest("ut_messenger package not installed")


class _DummyHeaders:
    def get_content_charset(self):
        return "utf-8"


class _DummyResponse:
    def __init__(self, payload):
        self._payload = payload
        self.headers = _DummyHeaders()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return json.dumps(self._payload).encode("utf-8")


def test_fetch_contacts_routes_to_expected_endpoint_with_auth(monkeypatch):
    calls = {}

    def fake_urlopen(req, timeout):
        calls["url"] = req.full_url
        calls["auth"] = req.get_header("Authorization")
        calls["timeout"] = timeout
        return _DummyResponse({"users": []})

    monkeypatch.setattr(api_client_module, "urlopen", fake_urlopen)
    client = ApiClient(server_host="ws://localhost:9001", auth_token="secret-token")

    rows = client.fetch_contacts(user_id="artist", timeout=1.25)

    assert rows == []
    assert calls["url"] == "http://localhost:9001/api/users?exclude_user_id=artist"
    assert calls["auth"] == "Bearer secret-token"
    assert calls["timeout"] == 1.25


def test_http_error_is_mapped_to_api_client_error(monkeypatch):
    def fake_urlopen(req, timeout):
        raise HTTPError(
            url=req.full_url,
            code=403,
            msg="Forbidden",
            hdrs=None,
            fp=io.BytesIO(b'{"detail":"denied"}'),
        )

    monkeypatch.setattr(api_client_module, "urlopen", fake_urlopen)
    client = ApiClient(server_host="ws://localhost:8000", auth_token="x")

    with pytest.raises(ApiClientError) as exc_info:
        client.fetch_capabilities(timeout=1.0)

    exc = exc_info.value
    assert exc.status_code == 403
    assert "/api/capabilities" in exc.url


def test_fetch_groups_falls_back_to_legacy_when_v2_fails(monkeypatch):
    client = ApiClient(server_host="ws://localhost:8000", auth_token="x")

    def fake_v2(timeout):
        raise ApiClientError(message="v2 unavailable")

    def fake_legacy(user_id, timeout):
        return [{"id": 12, "name": "legacy"}]

    monkeypatch.setattr(client, "fetch_channels_v2", fake_v2)
    monkeypatch.setattr(client, "fetch_legacy_groups", fake_legacy)

    rows = client.fetch_groups(user_id="artist", prefer_v2=True, timeout=1.0)
    assert rows == [{"id": 12, "name": "legacy"}]
