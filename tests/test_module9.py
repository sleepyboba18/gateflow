from datetime import datetime, timezone

from app.gateway.resolver import GatewayResolutionError
from app.services.schema_service import ValidationResult, validate_payload
from app.services.scope_service import valid_scope_name


def test_version_format_examples():
    import re
    version_pattern = re.compile(r"^v[1-9][0-9]*$")
    assert version_pattern.fullmatch("v1")
    assert version_pattern.fullmatch("v10")
    assert not version_pattern.fullmatch("V1")
    assert not version_pattern.fullmatch("version1")


def test_empty_schema_payload_is_rejected():
    schema = type("Schema", (), {"schema_definition": {"type": "object"}})()
    result = validate_payload(schema, None, "application/json", False)
    assert result.valid is False
    assert result.status == 400
    assert result.errors[0]["path"] == "$"


def test_scope_name_validation_remains_stable():
    assert valid_scope_name("weather:read")
    assert not valid_scope_name("Weather Read")
