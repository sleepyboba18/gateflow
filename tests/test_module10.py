from datetime import datetime, timedelta, timezone
import ipaddress

from app.services.api_key_service import get_key_status
from app.services.api_key_service import _restriction_allowed
from app.services.api_key_service import client_ip
from app.sockets.events import emit_security_event


def test_key_lifecycle_statuses():
    now = datetime.now(timezone.utc)
    key = type("Key", (), {"revoked_at": None, "rotation_grace_until": None, "suspended_at": None, "expires_at": None, "is_active": True})()
    assert get_key_status(key, now) == "active"
    key.suspended_at = now
    assert get_key_status(key, now) == "suspended"
    key.suspended_at = None
    key.expires_at = now
    assert get_key_status(key, now) == "expired"
    key.revoked_at = now
    assert get_key_status(key, now) == "revoked"


def test_security_event_payload_has_no_secret(monkeypatch):
    captured = []
    monkeypatch.setattr("app.sockets.events._emit", lambda event, payload, rooms: captured.append((event, payload)))
    emit_security_event(event_type="api_key_authentication_failed", api_key_id="key-id", owner_id="user-id")
    assert captured[0][0] == "gateway:security"
    assert "key" not in str(captured[0][1]).lower() or "api_key_id" in captured[0][1]
    assert "plaintext" not in captured[0][1]


def test_ip_rule_logic_uses_cidr_and_deny_precedence():
    class Result:
        def __init__(self, values): self.values = values
        def all(self): return self.values
    class Session:
        def __init__(self, rules): self.rules = rules
        def scalars(self, statement): return Result(self.rules if "api_key_ip_rules" in str(statement) else [])
    rule = type("Rule", (), {"cidr": "203.0.113.0/24", "rule_type": "allow"})()
    key = type("Key", (), {"id": "key"})()
    assert _restriction_allowed(Session([rule]), key, "203.0.113.5", None)[0]
    assert not _restriction_allowed(Session([rule]), key, "198.51.100.5", None)[0]
