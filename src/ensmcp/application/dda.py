"""Application projections for Declaration of Applicability records."""

from __future__ import annotations

from collections.abc import Mapping

from ensmcp.domain.dda import DDARecord, ImplementationStatus


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
