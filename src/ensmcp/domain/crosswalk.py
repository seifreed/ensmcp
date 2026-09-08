"""Versioned, independently loaded compliance crosswalks."""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass
from enum import StrEnum

DATA_PACK_SCHEMA_VERSION = 1


class DataPackStatus(StrEnum):
    ACTIVE = "active"
    WITHDRAWN = "withdrawn"


class DataPackCoverage(StrEnum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    UNAVAILABLE = "unavailable"


class CrosswalkRelation(StrEnum):
    OFFICIAL = "official"
    EDITORIAL = "editorial"


@dataclass(frozen=True, slots=True)
class CrosswalkEntry:
    external_reference: str
    ens_measure_codes: tuple[str, ...]
    relation: CrosswalkRelation
    source_reference: str
    notes: str = ""


@dataclass(frozen=True, slots=True)
class DataPack:
    pack_id: str
    title: str
    framework: str
    framework_version: str
    version: str
    status: DataPackStatus
    coverage: DataPackCoverage
    authority: str
    source_url: str
    source_date: str
    reviewed_at: str
    notes: str
    mappings: tuple[CrosswalkEntry, ...]
    schema_version: int = DATA_PACK_SCHEMA_VERSION


def query_crosswalk_entries(
    pack: DataPack,
    known_ens_codes: Collection[str],
    ens_code: str | None = None,
    external_reference: str | None = None,
) -> tuple[CrosswalkEntry, ...]:
    """Validate and filter one data pack against the current ENS corpus."""
    unknown_codes = sorted(
        {
            code
            for mapping in pack.mappings
            for code in mapping.ens_measure_codes
            if code not in known_ens_codes
        }
    )
    if unknown_codes:
        raise ValueError(
            f"data pack {pack.pack_id!r} contiene medidas ENS desconocidas: {unknown_codes}"
        )

    wanted_reference = external_reference.strip().casefold() if external_reference else None
    return tuple(
        mapping
        for mapping in pack.mappings
        if (ens_code is None or ens_code in mapping.ens_measure_codes)
        and (wanted_reference is None or mapping.external_reference.casefold() == wanted_reference)
    )
