from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.api_version import APIVersion
from app.models.api_route import APIRoute
from app.models.schema import GatewaySchema


@dataclass(frozen=True)
class ValidationResult:
    valid: bool
    errors: tuple[dict, ...] = ()
    status: int = 200
    message: str | None = None


def validate_schema_definition(definition: object) -> None:
    try:
        from jsonschema import Draft202012Validator
    except ImportError as error:
        raise ValueError("JSON Schema dependency is not installed") from error
    if not isinstance(definition, dict):
        raise ValueError("Schema definition must be an object")
    reference = definition.get("$ref")
    if isinstance(reference, str) and (reference.startswith(("http:", "https:")) or reference not in {"#", ""}):
        raise ValueError("External schema references are not allowed")
    try:
        Draft202012Validator.check_schema(definition)
    except Exception as error:
        raise ValueError("Invalid JSON Schema definition") from error


def get_effective_schema(session: Session, api_id: UUID, version_id: UUID, route_id: UUID, schema_type: str) -> GatewaySchema | None:
    route_schema = session.scalar(select(GatewaySchema).where(GatewaySchema.api_id == api_id, GatewaySchema.version_id == version_id, GatewaySchema.route_id == route_id, GatewaySchema.schema_type == schema_type, GatewaySchema.is_active.is_(True)))
    if route_schema:
        return route_schema
    return session.scalar(select(GatewaySchema).where(GatewaySchema.api_id == api_id, GatewaySchema.version_id == version_id, GatewaySchema.route_id.is_(None), GatewaySchema.schema_type == schema_type, GatewaySchema.is_active.is_(True)))


def validate_payload(schema: GatewaySchema | None, payload: object, content_type: str | None, has_body: bool) -> ValidationResult:
    if schema is None:
        return ValidationResult(True)
    if not content_type or "application/json" not in content_type.lower():
        return ValidationResult(False, status=415, message="Unsupported media type")
    if not has_body:
        return ValidationResult(False, ({"path": "$", "message": "request body is required", "keyword": "required"},), 400, "Request validation failed")
    try:
        from jsonschema import Draft202012Validator, FormatChecker
    except ImportError as error:
        return ValidationResult(False, status=503, message="JSON Schema dependency is not installed")
    validator = Draft202012Validator(schema.schema_definition, format_checker=FormatChecker())
    errors = []
    for item in validator.iter_errors(payload):
        path = "$" + "".join(f"[{part}]" if isinstance(part, int) else f".{part}" for part in item.absolute_path)
        errors.append({"path": path, "message": item.message, "keyword": item.validator})
        if len(errors) >= 20:
            break
    return ValidationResult(not errors, tuple(errors), 200 if not errors else 400, None if not errors else "Request validation failed")
