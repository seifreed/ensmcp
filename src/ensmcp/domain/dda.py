"""Persistent Declaration of Applicability records and update rules."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import date, datetime
from enum import StrEnum
from typing import Protocol

from ensmcp.domain.models import DimensionLevel, SecurityMeasure, SystemCategory
from ensmcp.domain.profiles import (
    ApplicabilityReason,
    ResolvedComplianceProfile,
    SystemProfile,
    explain_profile_measure,
    resolve_profile_scope,
)
from ensmcp.domain.queries import system_category

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
    XLSX = "xlsx"
    ODS = "ods"
    DOCX = "docx"


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


def _decision_basis(reason: ApplicabilityReason) -> dict[str, object]:
    return {
        "basis": reason.basis,
        "dimensions": [dimension.value for dimension in reason.dimensions],
        "system_level": reason.system_level.value if reason.system_level is not None else None,
        "table_cell": reason.table_cell,
        "profile_chain": list(reason.profile_chain),
        "evidence": [
            {
                "dimension": item.dimension.value,
                "source": item.source,
                "level": item.level.value,
                "justification": item.justification,
            }
            for item in reason.evidence
        ],
    }


def create_dda_record(
    record_id: str,
    profile: SystemProfile,
    measures: Sequence[SecurityMeasure],
    *,
    controls: ResolvedComplianceProfile,
    subsystem_id: str | None,
    now: datetime,
) -> DDARecord:
    scope = resolve_profile_scope(profile, controls, subsystem_id)
    levels = {dimension: value.level for dimension, value in scope.dimensions.items()}
    lines = []
    for measure in measures:
        explanation = explain_profile_measure(measure, scope)
        lines.append(
            DDAMeasure(
                measure_code=measure.code,
                title=measure.title,
                applicable=explanation.applicable,
                required_level=explanation.required_level,
                required_reinforcements=tuple(
                    DDAReinforcement(item.code, item.alternative, item.text)
                    for item in explanation.required_reinforcements
                ),
                decision_basis=_decision_basis(explanation.reason),
                implementation_status=(
                    ImplementationStatus.NOT_ASSESSED
                    if explanation.applicable
                    else ImplementationStatus.EXCLUDED
                ),
                exclusion_reason=(
                    "" if explanation.applicable else f"No aplicable: {explanation.reason.basis}"
                ),
            )
        )
    return DDARecord(
        record_id=record_id,
        profile_id=profile.profile_id,
        system=profile.system,
        scope_id=scope.scope_id,
        scope=scope.scope,
        category=system_category(levels),
        created_at=now,
        updated_at=now,
        measures=tuple(lines),
    )


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
