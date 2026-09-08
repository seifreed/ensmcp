"""Persistent Declaration of Applicability records and update rules."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import date, datetime
from enum import StrEnum
from typing import Protocol

from ensmcp.domain.models import DimensionLevel, SystemCategory

DDA_SCHEMA_VERSION = 1


class ImplementationStatus(StrEnum):
    NOT_ASSESSED = "not_assessed"
    IMPLEMENTED = "implemented"
    PARTIALLY_IMPLEMENTED = "partially_implemented"
    NOT_IMPLEMENTED = "not_implemented"
    EXCLUDED = "excluded"
    COMPENSATED = "compensated"


class ExportFormat(StrEnum):
    JSON = "json"
    CSV = "csv"
    MARKDOWN = "markdown"


@dataclass(frozen=True, slots=True)
class ExportedDocument:
    filename: str
    mime_type: str
    content: bytes


@dataclass(frozen=True, slots=True)
class DDAReinforcement:
    code: str
    alternative: bool
    text: str


@dataclass(frozen=True, slots=True)
class DDAMeasure:
    measure_code: str
    title: str
    applicable: bool
    required_level: DimensionLevel | None = None
    required_reinforcements: tuple[DDAReinforcement, ...] = ()
    decision_basis: Mapping[str, object] = field(default_factory=dict)
    implementation_status: ImplementationStatus = ImplementationStatus.NOT_ASSESSED
    justification: str = ""
    owner: str = ""
    evidence_references: tuple[str, ...] = ()
    exclusion_reason: str = ""
    compensatory_measures: tuple[str, ...] = ()
    surveillance_measures: tuple[str, ...] = ()
    target_date: date | None = None
    review_date: date | None = None


@dataclass(frozen=True, slots=True)
class DDARecord:
    record_id: str
    profile_id: str
    system: str
    scope_id: str
    scope: str
    category: SystemCategory
    created_at: datetime
    updated_at: datetime
    measures: tuple[DDAMeasure, ...]
    schema_version: int = DDA_SCHEMA_VERSION


@dataclass(frozen=True, slots=True)
class DDAMeasureUpdate:
    implementation_status: ImplementationStatus
    justification: str = ""
    owner: str = ""
    evidence_references: tuple[str, ...] = ()
    exclusion_reason: str = ""
    compensatory_measures: tuple[str, ...] = ()
    surveillance_measures: tuple[str, ...] = ()
    target_date: date | None = None
    review_date: date | None = None


class DDAStore(Protocol):
    def create(self, record: DDARecord) -> None: ...

    def save(self, record: DDARecord) -> None: ...

    def load(self, record_id: str) -> DDARecord: ...

    def list_ids(self) -> tuple[str, ...]: ...


def update_dda_measure(
    record: DDARecord,
    measure_code: str,
    update: DDAMeasureUpdate,
    updated_at: datetime,
) -> DDARecord:
    if update.implementation_status is ImplementationStatus.EXCLUDED and not (
        update.exclusion_reason.strip()
    ):
        raise ValueError("una medida excluida requiere exclusion_reason")
    if (
        update.implementation_status is ImplementationStatus.COMPENSATED
        and not update.compensatory_measures
    ):
        raise ValueError("una medida compensada requiere compensatory_measures")

    found = False
    measures = []
    for measure in record.measures:
        if measure.measure_code != measure_code:
            measures.append(measure)
            continue
        found = True
        measures.append(
            replace(
                measure,
                implementation_status=update.implementation_status,
                justification=update.justification,
                owner=update.owner,
                evidence_references=update.evidence_references,
                exclusion_reason=update.exclusion_reason,
                compensatory_measures=update.compensatory_measures,
                surveillance_measures=update.surveillance_measures,
                target_date=update.target_date,
                review_date=update.review_date,
            )
        )
    if not found:
        raise ValueError(f"la DdA no contiene la medida {measure_code!r}")
    return replace(record, measures=tuple(measures), updated_at=updated_at)


def dda_to_dict(record: DDARecord) -> dict[str, object]:
    return {
        "schema_version": record.schema_version,
        "record_id": record.record_id,
        "profile_id": record.profile_id,
        "system": record.system,
        "scope_id": record.scope_id,
        "scope": record.scope,
        "category": record.category.value,
        "created_at": record.created_at.isoformat(),
        "updated_at": record.updated_at.isoformat(),
        "measures": [
            {
                "measure_code": measure.measure_code,
                "title": measure.title,
                "applicable": measure.applicable,
                "required_level": (
                    measure.required_level.value if measure.required_level is not None else None
                ),
                "required_reinforcements": [
                    {
                        "code": item.code,
                        "alternative": item.alternative,
                        "text": item.text,
                    }
                    for item in measure.required_reinforcements
                ],
                "decision_basis": measure.decision_basis,
                "implementation_status": measure.implementation_status.value,
                "justification": measure.justification,
                "owner": measure.owner,
                "evidence_references": list(measure.evidence_references),
                "exclusion_reason": measure.exclusion_reason,
                "compensatory_measures": list(measure.compensatory_measures),
                "surveillance_measures": list(measure.surveillance_measures),
                "target_date": (
                    measure.target_date.isoformat() if measure.target_date is not None else None
                ),
                "review_date": (
                    measure.review_date.isoformat() if measure.review_date is not None else None
                ),
            }
            for measure in record.measures
        ],
    }


def dda_summary(record: DDARecord) -> Mapping[str, object]:
    counts = {status.value: 0 for status in ImplementationStatus}
    for measure in record.measures:
        counts[measure.implementation_status.value] += 1
    return {
        "record_id": record.record_id,
        "system": record.system,
        "scope_id": record.scope_id,
        "category": record.category.value,
        "updated_at": record.updated_at.isoformat(),
        "measures": len(record.measures),
        "status_counts": counts,
    }
