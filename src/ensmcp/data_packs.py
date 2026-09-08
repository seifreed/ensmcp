"""Load independent JSON data packs configured outside the core package."""

from __future__ import annotations

import os
import re
from pathlib import Path

from pydantic import TypeAdapter, ValidationError

from ensmcp.domain.crosswalk import DATA_PACK_SCHEMA_VERSION, DataPack

DATA_PACKS_ENV_VAR = "ENSMCP_DATA_PACKS"
_ADAPTER = TypeAdapter(DataPack)
_PACK_ID = re.compile(r"[a-z0-9][a-z0-9._-]{0,127}")


def load_data_pack(path: Path) -> DataPack:
    try:
        pack = _ADAPTER.validate_json(path.read_bytes())
    except (OSError, ValidationError) as exc:
        raise ValueError(f"data pack inválido: {path}") from exc
    if pack.schema_version != DATA_PACK_SCHEMA_VERSION:
        raise ValueError(f"schema_version incompatible en data pack: {path}")
    if _PACK_ID.fullmatch(pack.pack_id) is None:
        raise ValueError(f"pack_id inválido en data pack: {path}")
    references = [mapping.external_reference.casefold() for mapping in pack.mappings]
    if len(references) != len(set(references)):
        raise ValueError(f"referencias duplicadas en data pack: {path}")
    return pack


def load_configured_data_packs() -> tuple[DataPack, ...]:
    configured = os.environ.get(DATA_PACKS_ENV_VAR, "").strip()
    if not configured:
        return ()
    items = configured.split(os.pathsep)
    if any(not item.strip() for item in items):
        raise ValueError(f"{DATA_PACKS_ENV_VAR} contiene rutas vacías")
    paths = []
    for item in items:
        path = Path(item).expanduser()
        paths.extend(sorted(path.glob("*.json"))) if path.is_dir() else paths.append(path)
    packs = tuple(load_data_pack(path) for path in paths)
    pack_ids = [pack.pack_id for pack in packs]
    if len(pack_ids) != len(set(pack_ids)):
        raise ValueError("los pack_id configurados deben ser únicos")
    return packs
