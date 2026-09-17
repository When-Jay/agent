"""Dataclass <-> JSON-safe dict serialization for evaluation aggregates.

SQLAlchemy store payloads and API responses share this encoding; datetimes
are ISO-8601 strings, enums serialize via their value, nested dataclasses
recurse.
"""

import dataclasses
import datetime as dt
import enum
import types
import typing
from typing import Any


def dump(value: Any) -> Any:
    """Convert dataclasses/datetime/enum values into JSON-safe structures."""
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {f.name: dump(getattr(value, f.name)) for f in dataclasses.fields(value)}
    if isinstance(value, dt.datetime):
        return value.isoformat()
    if isinstance(value, enum.Enum):
        return value.value
    if isinstance(value, (list, tuple)):
        return [dump(item) for item in value]
    if isinstance(value, dict):
        return {key: dump(item) for key, item in value.items()}
    return value


def _is_optional(hint: Any) -> tuple[bool, Any]:
    origin = typing.get_origin(hint)
    # `X | None` (types.UnionType) and Optional[X] (typing.Union) both occur.
    if origin is typing.Union or origin is types.UnionType:
        args = [a for a in typing.get_args(hint) if a is not type(None)]
        if len(args) == 1:
            return True, args[0]
    return False, hint


def load(cls: type, data: dict[str, Any]) -> Any:
    """Rebuild a dataclass instance from `dump` output.

    Datetimes, enums, nested dataclasses and lists of dataclasses are
    resolved from field annotations; unknown keys are ignored so stored
    payloads stay forward-compatible.
    """
    hints = typing.get_type_hints(cls)
    kwargs: dict[str, Any] = {}
    for field in dataclasses.fields(cls):
        if field.name not in data:
            continue
        value = data[field.name]
        optional, hint = _is_optional(hints.get(field.name, Any))
        if value is None:
            kwargs[field.name] = None
            continue
        if hint is dt.datetime:
            kwargs[field.name] = dt.datetime.fromisoformat(value)
            continue
        if isinstance(hint, type) and issubclass(hint, enum.Enum):
            kwargs[field.name] = hint(value)
            continue
        if isinstance(value, dict) and dataclasses.is_dataclass(hint):
            kwargs[field.name] = load(hint, value)
            continue
        if (
            isinstance(value, list)
            and typing.get_origin(hint) is list
            and dataclasses.is_dataclass(typing.get_args(hint)[0])
        ):
            item_cls = typing.get_args(hint)[0]
            kwargs[field.name] = [load(item_cls, item) for item in value]
            continue
        kwargs[field.name] = value
    return cls(**kwargs)
