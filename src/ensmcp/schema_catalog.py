"""Load the versioned public schema catalog shipped with ensmcp."""

from __future__ import annotations

import json
from importlib import resources
from typing import Any

SCHEMA_VERSION = "1.0.0"


def load_schema_catalog() -> dict[str, Any]:
    path = resources.files("ensmcp") / "schemas" / "v1" / "tools.json"
    catalog: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return catalog
