from types import SimpleNamespace
from uuid import uuid4

import pytest
import requests
from flask import Flask, request

from app.gateway.proxy import build_upstream_url, forward_request
from app.gateway.resolver import GatewayResolutionError, resolve_gateway_request
from app.services.api_service import validate_base_url
from app.services.gateway_service import request_id_from


class FakeSession:
    def __init__(self, api, routes):
        self.api = api
        self.routes = routes

    def scalar(self, statement):
        text = str(statement)
        if "api_routes" in text:
            return None
        return self.api

    def scalars(self, statement):
        return _Result(self.routes)


class _Result:
    def __init__(self, values):
        self.values = values

    def all(self):
        return self.values


def test_base_url_rejects_private_and_unsupported_addresses(monkeypatch):
    monkeypatch.setattr("socket.getaddrinfo", lambda *args, **kwargs: [(None, None, None, None, ("127.0.0.1", 80))])
    assert not validate_base_url("http://localhost")
    assert not validate_base_url("ftp://example.com")


def test_resolver_rejects_wrong_method():
    api = SimpleNamespace(id=uuid4(), owner_id=uuid4(), slug="weather-api", is_active=True)
    route = SimpleNamespace(api_id=api.id, path="/current", method="GET", is_active=True, target_path="/v1/current")
    session = FakeSession(api, [route])
    with pytest.raises(GatewayResolutionError) as error:
        resolve_gateway_request(session, "weather-api", "current", "POST", api.owner_id)
    assert error.value.status == 405


def test_request_id_is_preserved_or_generated():
    app = Flask(__name__)
    with app.test_request_context(headers={"X-Request-ID": "client-request-42"}):
        assert request_id_from(request) == "client-request-42"
    with app.test_request_context():
        generated = request_id_from(request)
        assert len(generated) == 36


def test_proxy_forwards_query_body_and_safe_headers(monkeypatch):
    captured = {}

    def fake_request(**kwargs):
        captured.update(kwargs)
        return requests.Response()

    monkeypatch.setattr("app.gateway.proxy.requests.request", fake_request)
    monkeypatch.setattr("app.gateway.proxy.validate_base_url", lambda value: True)
    app = Flask(__name__)
    resolved = SimpleNamespace(
        api=SimpleNamespace(base_url="https://example.com", timeout_seconds=7),
        target_path="/v1/current",
    )
    with app.test_request_context("/gateway/weather/current?city=Kolkata", method="POST", data='{"ok":true}', headers={"Content-Type": "application/json", "X-API-Key": "secret", "X-Request-ID": "request-1"}):
        forward_request(resolved, request, "request-1")
    assert captured["url"] == "https://example.com/v1/current"
    assert captured["params"] == [("city", "Kolkata")]
    assert captured["data"] == b'{"ok":true}'
    assert captured["headers"] == {"Content-Type": "application/json", "X-Request-ID": "request-1"}
    assert captured["timeout"] == 7
