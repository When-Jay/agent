"""Minimal JSON Schema validation for tool arguments (mcp-gateway-spec.md section 10).

Supported keywords: type, required, properties, additionalProperties,
enum, items. No ``$ref`` or remote resolution (documented limitation).
Errors are human-readable dotted paths so a model loop can correct its
arguments from the error result alone; argument VALUES never appear in
error messages.
"""

from typing import Any

_SIMPLE_TYPES: dict[str, type | tuple[type, ...]] = {
    "object": dict,
    "array": list,
    "string": str,
    "boolean": bool,
    "null": type(None),
}


def _matches_type(value: Any, expected: Any) -> bool:
    if isinstance(expected, list):
        return any(_matches_type(value, item) for item in expected)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    target = _SIMPLE_TYPES.get(expected)
    if target is None:
        return True  # unknown type keywords are not enforced
    return isinstance(value, target)


def validate_against_schema(
    value: Any, schema: dict[str, Any] | None, path: str = "arguments"
) -> list[str]:
    """Return validation error messages; empty list means valid."""
    if not isinstance(schema, dict) or not schema:
        return []
    errors: list[str] = []
    if "type" in schema and not _matches_type(value, schema["type"]):
        errors.append(
            f"{path}: expected type {schema['type']}, got {type(value).__name__}"
        )
        return errors  # deeper structure checks are meaningless on a type mismatch
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{path}: value is not one of the allowed enum entries")

    if isinstance(value, dict):
        properties = schema.get("properties", {})
        for name in schema.get("required", []):
            if name not in value:
                errors.append(f"{path}.{name}: required property missing")
        additional = schema.get("additionalProperties", True)
        for key, item in value.items():
            child = f"{path}.{key}"
            if key in properties:
                errors.extend(validate_against_schema(item, properties[key], child))
            elif additional is False:
                errors.append(f"{child}: unexpected property")
            elif isinstance(additional, dict):
                errors.extend(validate_against_schema(item, additional, child))
    elif isinstance(value, list):
        items = schema.get("items")
        if isinstance(items, dict):
            for index, item in enumerate(value):
                errors.extend(validate_against_schema(item, items, f"{path}[{index}]"))
    return errors
