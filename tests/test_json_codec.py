"""Tests for validation shared by both JSON codecs."""

from __future__ import annotations

import json
from collections.abc import Callable

import pytest

from ensmcp.guia.codec import SCHEMA_VERSION as GUIDE_SCHEMA_VERSION
from ensmcp.guia.codec import load as load_guide
from ensmcp.snapshot.codec import SCHEMA_VERSION as SNAPSHOT_SCHEMA_VERSION
from ensmcp.snapshot.codec import load as load_snapshot

_CODECS: list[tuple[Callable[[str], object], str, int]] = [
    (load_snapshot, "snapshot", SNAPSHOT_SCHEMA_VERSION),
    (load_guide, "guia_808", GUIDE_SCHEMA_VERSION),
]


@pytest.mark.parametrize(("load", "name", "_schema_version"), _CODECS, ids=["snapshot", "guide"])
@pytest.mark.parametrize(
    ("text", "expected"),
    [("", "Expecting value"), ('{"schema_version": 3, "data', "Unterminated string")],
    ids=["empty", "truncated"],
)
def test_codecs_refuse_invalid_json(
    load: Callable[[str], object], name: str, _schema_version: int, text: str, expected: str
) -> None:
    with pytest.raises(ValueError, match=rf"{name} is not valid JSON") as excinfo:
        load(text)

    message = str(excinfo.value)
    if expected not in message or "truncated or corrupt" not in message:
        raise AssertionError(message)


@pytest.mark.parametrize(("load", "name", "_schema_version"), _CODECS, ids=["snapshot", "guide"])
@pytest.mark.parametrize("document", ["[]", "null", '"text"', "3"], ids=list("alti"))
def test_codecs_require_a_json_object(
    load: Callable[[str], object], name: str, _schema_version: int, document: str
) -> None:
    with pytest.raises(ValueError, match=rf"{name} is a JSON .* expected an object"):
        load(document)


@pytest.mark.parametrize("version_kind", ["missing", "wrong", "float", "boolean"])
@pytest.mark.parametrize(("load", "name", "schema_version"), _CODECS, ids=["snapshot", "guide"])
def test_codecs_require_the_exact_integer_schema_version(
    load: Callable[[str], object], name: str, schema_version: int, version_kind: str
) -> None:
    versions: dict[str, object] = {
        "missing": None,
        "wrong": schema_version + 1,
        "float": float(schema_version),
        "boolean": True,
    }
    version = versions[version_kind]
    text = json.dumps({} if version is None else {"schema_version": version})

    with pytest.raises(
        ValueError, match=rf"{name} schema version {version!r}, expected {schema_version}"
    ):
        load(text)
