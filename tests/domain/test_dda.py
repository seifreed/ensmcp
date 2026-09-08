"""Declaration of Applicability domain rules."""

from datetime import UTC, date, datetime
from typing import Any

import pytest

from ensmcp.application.dda import dda_summary, dda_to_dict
from ensmcp.domain.dda import (
    DDAMeasure,
    DDAMeasureUpdate,
    DDARecord,
    DDAReinforcement,
    ImplementationStatus,
    update_dda_measure,
)
from ensmcp.domain.models import DimensionLevel, SystemCategory
from tests.support import check


def sample_dda() -> DDARecord:
    now = datetime(2026, 9, 8, tzinfo=UTC)
    return DDARecord(
        record_id="portal-2026",
        profile_id="portal",
        system="Portal",
        scope_id="portal",
        scope="Producción",
        category=SystemCategory.MEDIA,
        created_at=now,
        updated_at=now,
        measures=(
            DDAMeasure(
                "org.1",
                "Política de seguridad",
                True,
                DimensionLevel.MEDIO,
                (DDAReinforcement("R1", False, "Aprobación"),),
                {"basis": "category"},
            ),
            DDAMeasure(
                "mp.if.3",
                "Protección de instalaciones",
                False,
                implementation_status=ImplementationStatus.EXCLUDED,
                exclusion_reason="No aplica",
            ),
        ),
    )


def test_update_keeps_the_record_and_serializes_all_fields() -> None:
    updated_at = datetime(2026, 9, 9, tzinfo=UTC)
    updated = update_dda_measure(
        sample_dda(),
        "org.1",
        DDAMeasureUpdate(
            ImplementationStatus.IMPLEMENTED,
            justification="Control operativo",
            owner="CISO",
            evidence_references=("ev-1",),
            surveillance_measures=("revisión trimestral",),
            target_date=date(2026, 10, 1),
            review_date=date(2027, 1, 1),
        ),
        updated_at,
    )

    payload: Any = dda_to_dict(updated)
    measure = payload["measures"][0]
    check(measure["implementation_status"] == "implemented")
    check(measure["required_reinforcements"][0]["code"] == "R1")
    check(measure["target_date"] == "2026-10-01")
    check(measure["review_date"] == "2027-01-01")
    check(payload["updated_at"] == updated_at.isoformat())
    summary: Any = dda_summary(updated)
    check(summary["status_counts"]["implemented"] == 1)
    check(summary["status_counts"]["excluded"] == 1)


def test_update_validates_exclusions_compensation_and_measure_code() -> None:
    now = datetime.now(UTC)
    with pytest.raises(ValueError, match="exclusion_reason"):
        update_dda_measure(
            sample_dda(), "org.1", DDAMeasureUpdate(ImplementationStatus.EXCLUDED), now
        )
    with pytest.raises(ValueError, match="compensatory_measures"):
        update_dda_measure(
            sample_dda(), "org.1", DDAMeasureUpdate(ImplementationStatus.COMPENSATED), now
        )
    with pytest.raises(ValueError, match="compensatory_measures"):
        update_dda_measure(
            sample_dda(),
            "org.1",
            DDAMeasureUpdate(ImplementationStatus.COMPENSATED, compensatory_measures=(" ",)),
            now,
        )
    with pytest.raises(ValueError, match="no contiene"):
        update_dda_measure(
            sample_dda(), "missing", DDAMeasureUpdate(ImplementationStatus.IMPLEMENTED), now
        )


def test_serialization_uses_null_for_dates_not_recorded() -> None:
    payload: Any = dda_to_dict(sample_dda())
    measure = payload["measures"][1]
    check(measure["target_date"] is None)
    check(measure["review_date"] is None)
