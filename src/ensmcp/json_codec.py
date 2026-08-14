"""Validation shared by the package's JSON codecs."""

from __future__ import annotations

import json
from collections.abc import Hashable, Sequence
from typing import Any, cast


def load_object(text: str, name: str, schema_version: int) -> dict[str, Any]:
    """Decode a versioned JSON object, naming malformed input consistently."""
    try:
        document = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"{name} is not valid JSON ({exc}): the file is truncated or corrupt"
        ) from exc
    if not isinstance(document, dict):
        raise ValueError(f"{name} is a JSON {type(document).__name__}, expected an object")
    version = document.get("schema_version")
    if not isinstance(version, int) or isinstance(version, bool) or version != schema_version:
        raise ValueError(f"{name} schema version {version!r}, expected {schema_version}")
    return cast("dict[str, Any]", document)


def require_string(value: object, where: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{where} is a {type(value).__name__}, expected a string")
    return value


def require_unique(values: Sequence[Hashable], what: str) -> None:
    seen: set[Hashable] = set()
    for value in values:
        if value in seen:
            raise ValueError(f"duplicate {what}: {value!r}")
        seen.add(value)
