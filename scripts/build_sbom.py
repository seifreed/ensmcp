"""Build a deterministic CycloneDX BOM from a PEP 751 Python lock."""

from __future__ import annotations

import hashlib
import json
import re
import sys
import tomllib
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5


def _pypi_name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def build(lock_path: Path, output_path: Path) -> None:
    document = tomllib.loads(lock_path.read_text(encoding="utf-8"))
    packages = document["packages"]
    components = []
    for package in packages:
        name = package["name"]
        version = package["version"]
        purl = f"pkg:pypi/{_pypi_name(name)}@{version}"
        component = {
            "type": "library",
            "name": name,
            "version": version,
            "purl": purl,
            "bom-ref": purl,
        }
        marker = package.get("marker")
        if marker is not None:
            component["properties"] = [{"name": "org.python.marker", "value": marker}]
        components.append(component)

    lock_digest = hashlib.sha256(lock_path.read_bytes()).hexdigest()
    bom = {
        "bomFormat": "CycloneDX",
        "specVersion": "1.5",
        "serialNumber": f"urn:uuid:{uuid5(NAMESPACE_URL, f'ensmcp:{lock_digest}')}",
        "version": 1,
        "metadata": {"tools": [{"vendor": "ensmcp", "name": "build_sbom"}]},
        "components": components,
    }
    output_path.write_text(json.dumps(bom, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit("usage: python scripts/build_sbom.py pylock.toml output.json")
    build(Path(sys.argv[1]), Path(sys.argv[2]))


if __name__ == "__main__":
    main()
