"""System profile aggregation and compliance-profile inheritance."""

from __future__ import annotations

import pytest

from ensmcp.domain.models import DimensionLevel, SecurityDimension
from ensmcp.domain.profiles import (
    ComplianceProfile,
    DimensionAssessment,
    DimensionProfile,
    EffectiveDimension,
    ProfileComponent,
    effective_dimensions,
    resolve_compliance_profile,
)
from tests.support import check, require


def _assessment(level: DimensionLevel, reason: str) -> DimensionAssessment:
    return DimensionAssessment(level, reason)


def test_effective_dimensions_take_maxima_and_keep_every_justification() -> None:
    result = effective_dimensions(
        DimensionProfile(confidencialidad=_assessment(DimensionLevel.BAJO, "base")),
        source="system:portal",
        information_assets=(
            ProfileComponent(
                "contracts",
                "Contratos",
                DimensionProfile(
                    confidencialidad=_assessment(DimensionLevel.ALTO, "datos reservados")
                ),
            ),
        ),
        services=(
            ProfileComponent(
                "tendering",
                "Licitación",
                DimensionProfile(disponibilidad=_assessment(DimensionLevel.MEDIO, "servicio 24x7")),
            ),
        ),
    )

    confidentiality = result[SecurityDimension.CONFIDENCIALIDAD]
    check(confidentiality.level is DimensionLevel.ALTO)
    check([item.justification for item in confidentiality.evidence] == ["base", "datos reservados"])
    check(result[SecurityDimension.DISPONIBILIDAD].level is DimensionLevel.MEDIO)


def test_subsystem_can_inherit_evidence_and_apply_a_profile_override() -> None:
    inherited = {
        SecurityDimension.INTEGRIDAD: EffectiveDimension(
            DimensionLevel.ALTO,
            (),
        )
    }
    result = effective_dimensions(
        DimensionProfile(autenticidad=_assessment(DimensionLevel.BAJO, "cuentas locales")),
        source="subsystem:backoffice",
        inherited=inherited,
        overrides=DimensionProfile(integridad=_assessment(DimensionLevel.MEDIO, "PCE aprobado")),
    )

    check(result[SecurityDimension.INTEGRIDAD].level is DimensionLevel.MEDIO)
    check(result[SecurityDimension.INTEGRIDAD].evidence[-1].source == "compliance_profile")
    check(result[SecurityDimension.AUTENTICIDAD].level is DimensionLevel.BAJO)


def test_compliance_profile_inheritance_resolves_child_precedence() -> None:
    profiles = (
        ComplianceProfile(
            "base",
            profile_overrides=DimensionProfile(
                disponibilidad=_assessment(DimensionLevel.MEDIO, "base")
            ),
            additional_measures=("org.1", "op.pl.1"),
            excluded_measures=("mp.if.1",),
        ),
        ComplianceProfile(
            "sector",
            inherits="base",
            profile_overrides=DimensionProfile(
                disponibilidad=_assessment(DimensionLevel.ALTO, "sector")
            ),
            additional_measures=("mp.if.1",),
            excluded_measures=("op.pl.1",),
        ),
    )

    result = resolve_compliance_profile(profiles, "sector")

    check(result.chain == ("base", "sector"))
    check(require(result.overrides.disponibilidad).level is DimensionLevel.ALTO)
    check(result.additional_measures == frozenset({"org.1", "mp.if.1"}))
    check(result.excluded_measures == frozenset({"op.pl.1"}))
    check(resolve_compliance_profile(profiles, None).chain == ())


def test_compliance_profile_rejects_duplicates_unknown_parents_and_cycles() -> None:
    with pytest.raises(ValueError, match="deben ser únicos"):
        resolve_compliance_profile((ComplianceProfile("same"), ComplianceProfile("same")), "same")
    with pytest.raises(ValueError, match="desconocido"):
        resolve_compliance_profile((ComplianceProfile("child", inherits="missing"),), "child")
    with pytest.raises(ValueError, match="circular"):
        resolve_compliance_profile(
            (ComplianceProfile("a", inherits="b"), ComplianceProfile("b", inherits="a")),
            "a",
        )
    with pytest.raises(ValueError, match="añadir y excluir"):
        resolve_compliance_profile(
            (
                ComplianceProfile(
                    "contradictory",
                    additional_measures=("org.1",),
                    excluded_measures=("org.1",),
                ),
            ),
            "contradictory",
        )
