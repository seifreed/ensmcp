"""Versioned, independently loaded compliance crosswalks."""

from __future__ import annotations

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
