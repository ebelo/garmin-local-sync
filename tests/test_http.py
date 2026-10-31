"""Transport failure policies, with synthetic responses and no network access."""

import base64
import json
import time
from types import SimpleNamespace

import pytest
import requests

from garmin_local_sync.adapters import http
from garmin_local_sync.adapters.http import Api, ApiError


@pytest.fixture
def api(tmp_path, monkeypatch):
    key = tmp_path / "key"
    key.write_text("test-only-not-a-real-key")
    key.chmod(0o600)
    monkeypatch.setattr(http.time, "sleep", lambda _: None)
    return Api(key, delay=0)


def test_authentication_caches_token_privately_and_reuses_it(api, monkeypatch):
    payload = base64.urlsafe_b64encode(json.dumps({"exp": time.time() + 3600}).encode()).decode().rstrip("=")
    token = f"synthetic.{payload}.synthetic"
    calls = []

    def request(method, path, **kwargs):
        calls.append((method, path))
        return SimpleNamespace(status_code=200, text=token)

    monkeypatch.setattr(api, "_request", request)
    api.authenticate()
    assert api.cache.stat().st_mode & 0o777 == 0o600
    cached = Api(api.key_file, delay=0)
    monkeypatch.setattr(cached, "_request", request)
    cached.authenticate()
    assert cached.token == token
    assert len(calls) == 1


@pytest.mark.parametrize("text", ["not-a-token", "x.e30.x"])
def test_invalid_authentication_response_is_not_cached(api, monkeypatch, text):
    monkeypatch.setattr(api, "_request", lambda *a, **kw: SimpleNamespace(status_code=200, text=text))
    with pytest.raises(RuntimeError, match="valid token response"):
        api.authenticate()
    assert not api.cache.exists()


@pytest.mark.parametrize("status,attempts", [(500, 3), (429, 7)])
def test_transient_http_failures_have_a_bounded_retry_budget(api, monkeypatch, status, attempts):
    calls = []

    def request(*args, **kwargs):
        calls.append(kwargs)
        return SimpleNamespace(status_code=status, headers={"Retry-After": "0"})

    monkeypatch.setattr(api.session, "request", request)
    with pytest.raises(ApiError) as error:
        api._request("GET", "/synthetic")
    assert error.value.status == status
    assert len(calls) == attempts
    assert all(c["allow_redirects"] is False and c["timeout"] == (15, 90) for c in calls)


def test_network_failures_are_bounded_and_do_not_echo_sensitive_exception_text(api, monkeypatch):
    calls = []

    def request(*args, **kwargs):
        calls.append(1)
        raise requests.ConnectionError("synthetic-sensitive-request-text")

    monkeypatch.setattr(api.session, "request", request)
    with pytest.raises(RuntimeError, match="Network failure") as error:
        api._request("GET", "/synthetic")
    assert len(calls) == 7
    assert "synthetic-sensitive-request-text" not in str(error.value)


def test_long_retry_after_stops_without_sleeping_for_hours(api, monkeypatch):
    monkeypatch.setattr(
        api.session,
        "request",
        lambda *a, **kw: SimpleNamespace(status_code=429, headers={"Retry-After": "3600"}),
    )
    with pytest.raises(RuntimeError, match="long cooldown"):
        api._request("GET", "/synthetic")


@pytest.mark.parametrize(
    "header,expected",
    [
        ("4", 4),
        ("Thu, 01 Jan 1970 00:16:48 GMT", 8),
        ("nan", 2),
        ("-1", 2),
        ("not-a-date", 2),
    ],
)
def test_retry_after_formats_and_invalid_values_preserve_retry_policy(api, monkeypatch, header, expected):
    sleeps = []
    responses = iter(
        [
            SimpleNamespace(status_code=429, headers={"Retry-After": header}),
            SimpleNamespace(status_code=200),
        ]
    )
    monkeypatch.setattr(http.time, "sleep", sleeps.append)
    monkeypatch.setattr(http.time, "time", lambda: 1000)
    monkeypatch.setattr(api.session, "request", lambda *a, **kw: next(responses))
    response = api._request("GET", "/synthetic")
    assert response.status_code == 200
    assert [delay for delay in sleeps if delay > 0] == [expected]
