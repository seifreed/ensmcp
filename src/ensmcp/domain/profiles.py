"""System profiles and deterministic inheritance for ENS applicability."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from ensmcp.domain.models import (
    DimensionLevel,
    Reinforcement,
    SecurityDimension,
    SecurityMeasure,
    SystemCategory,
)
from ensmcp.domain.queries import applicable_measures, code_order, required_level, system_category

_DIMENSION_FIELDS = tuple((dimension, dimension.value) for dimension in SecurityDimension)
_LEVEL_RANK = {level: rank for rank, level in enumerate(DimensionLevel)}
_LEVELS = tuple(DimensionLevel)


@dataclass(frozen=True, slots=True)
class DimensionAssessment:
    level: DimensionLevel
    justification: str = ""


@dataclass(frozen=True, slots=True)
class DimensionProfile:
    confidencialidad: DimensionAssessment | None = None
    integridad: DimensionAssessment | None = None
    disponibilidad: DimensionAssessment | None = None
    autenticidad: DimensionAssessment | None = None
    trazabilidad: DimensionAssessment | None = None


@dataclass(frozen=True, slots=True)
class ProfileComponent:
    component_id: str
    name: str
    dimensions: DimensionProfile = field(default_factory=DimensionProfile)


@dataclass(frozen=True, slots=True)
class ComplianceProfile:
    profile_id: str
    inherits: str | None = None
    profile_overrides: DimensionProfile = field(default_factory=DimensionProfile)
    additional_measures: tuple[str, ...] = ()
    excluded_measures: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class SubsystemProfile:
    subsystem_id: str
    name: str
    scope: str = ""
    inheritance: bool = True
    dimensions: DimensionProfile = field(default_factory=DimensionProfile)
    information_assets: tuple[ProfileComponent, ...] = ()
    services: tuple[ProfileComponent, ...] = ()


@dataclass(frozen=True, slots=True)
class SystemProfile:
    profile_id: str
    system: str
    scope: str
    dimensions: DimensionProfile = field(default_factory=DimensionProfile)
    information_assets: tuple[ProfileComponent, ...] = ()
    services: tuple[ProfileComponent, ...] = ()
    subsystems: tuple[SubsystemProfile, ...] = ()
    compliance_profiles: tuple[ComplianceProfile, ...] = ()
    active_compliance_profile: str | None = None


@dataclass(frozen=True, slots=True)
class DimensionEvidence:
    source: str
    level: DimensionLevel
    justification: str


@dataclass(frozen=True, slots=True)
class EffectiveDimension:
    level: DimensionLevel
    evidence: tuple[DimensionEvidence, ...]


@dataclass(frozen=True, slots=True)
class ResolvedComplianceProfile:
    chain: tuple[str, ...] = ()
    overrides: DimensionProfile = field(default_factory=DimensionProfile)
    additional_measures: frozenset[str] = frozenset()
    excluded_measures: frozenset[str] = frozenset()


@dataclass(frozen=True, slots=True)
class ResolvedProfileScope:
    scope_id: str
    scope: str
    dimensions: Mapping[SecurityDimension, EffectiveDimension]
    controls: ResolvedComplianceProfile


@dataclass(frozen=True, slots=True)
class ProfileScopeEvaluation:
    scope_id: str
    scope: str
    category: SystemCategory
    dimensions: Mapping[SecurityDimension, EffectiveDimension]
    compliance_profile_chain: tuple[str, ...]
    applicable_measure_codes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ApplicabilityEvidence:
    dimension: SecurityDimension
    source: str
    level: DimensionLevel
    justification: str


@dataclass(frozen=True, slots=True)
class ApplicabilityReason:
    basis: str
    dimensions: tuple[SecurityDimension, ...]
    system_level: DimensionLevel | None
    table_cell: str | None
    profile_chain: tuple[str, ...]
    evidence: tuple[ApplicabilityEvidence, ...]


@dataclass(frozen=True, slots=True)
class MeasureApplicability:
    scope_id: str
    measure_code: str
    applicable: bool
    required_level: DimensionLevel | None
    reason: ApplicabilityReason
    required_reinforcements: tuple[Reinforcement, ...]


def dimension_items(
    dimensions: DimensionProfile,
) -> tuple[tuple[SecurityDimension, DimensionAssessment], ...]:
    return tuple(
        (dimension, assessment)
        for dimension, field_name in _DIMENSION_FIELDS
        if (assessment := getattr(dimensions, field_name)) is not None
    )


def _merge_dimensions(parent: DimensionProfile, child: DimensionProfile) -> DimensionProfile:
    values = {dimension.value: assessment for dimension, assessment in dimension_items(parent)}
    values.update({dimension.value: assessment for dimension, assessment in dimension_items(child)})
    return DimensionProfile(**values)


def resolve_compliance_profile(
    profiles: Sequence[ComplianceProfile], active_profile_id: str | None
) -> ResolvedComplianceProfile:
    if active_profile_id is None:
        return ResolvedComplianceProfile()

    by_id = {profile.profile_id: profile for profile in profiles}
    if len(by_id) != len(profiles):
        raise ValueError("los profile_id de compliance deben ser únicos")

    def visit(profile_id: str, trail: tuple[str, ...]) -> ResolvedComplianceProfile:
        if profile_id in trail:
            raise ValueError(f"herencia circular de perfiles: {' -> '.join((*trail, profile_id))}")
        profile = by_id.get(profile_id)
        if profile is None:
            raise ValueError(f"perfil de compliance desconocido: {profile_id!r}")
        parent = (
            visit(profile.inherits, (*trail, profile_id))
            if profile.inherits is not None
            else ResolvedComplianceProfile()
        )
        own_additional = frozenset(profile.additional_measures)
        own_excluded = frozenset(profile.excluded_measures)
        result = ResolvedComplianceProfile(
            chain=(*parent.chain, profile.profile_id),
            overrides=_merge_dimensions(parent.overrides, profile.profile_overrides),
            additional_measures=(parent.additional_measures | own_additional) - own_excluded,
            excluded_measures=(parent.excluded_measures | own_excluded) - own_additional,
        )
        return result

    return visit(active_profile_id, ())


def effective_dimensions(
    dimensions: DimensionProfile,
    *,
    source: str,
    information_assets: Sequence[ProfileComponent] = (),
    services: Sequence[ProfileComponent] = (),
    inherited: Mapping[SecurityDimension, EffectiveDimension] | None = None,
    overrides: DimensionProfile | None = None,
) -> dict[SecurityDimension, EffectiveDimension]:
    evidence: dict[SecurityDimension, list[DimensionEvidence]] = {
        dimension: list(value.evidence) for dimension, value in (inherited or {}).items()
    }
    forced: dict[SecurityDimension, DimensionLevel] = {}

    def add(values: DimensionProfile, item_source: str) -> None:
        for dimension, assessment in dimension_items(values):
            evidence.setdefault(dimension, []).append(
                DimensionEvidence(item_source, assessment.level, assessment.justification)
            )

    add(dimensions, source)
    for component in information_assets:
        add(component.dimensions, f"information_asset:{component.component_id}")
    for component in services:
        add(component.dimensions, f"service:{component.component_id}")
    if overrides is not None:
        for dimension, assessment in dimension_items(overrides):
            add(DimensionProfile(**{dimension.value: assessment}), "compliance_profile")
            forced[dimension] = assessment.level

    return {
        dimension: EffectiveDimension(
            forced.get(
                dimension,
                max(items, key=lambda item: _LEVEL_RANK[item.level]).level,
            ),
            tuple(items),
        )
        for dimension, items in evidence.items()
    }


def resolve_profile_scope(
    profile: SystemProfile,
    controls: ResolvedComplianceProfile,
    subsystem_id: str | None = None,
) -> ResolvedProfileScope:
    base = effective_dimensions(
        profile.dimensions,
        source=f"system:{profile.profile_id}",
        information_assets=profile.information_assets,
        services=profile.services,
        overrides=controls.overrides,
    )
    if subsystem_id is None:
        return ResolvedProfileScope(profile.profile_id, profile.scope, base, controls)

    by_id = {subsystem.subsystem_id: subsystem for subsystem in profile.subsystems}
    if len(by_id) != len(profile.subsystems):
        raise ValueError("los subsystem_id deben ser únicos")
    subsystem = by_id.get(subsystem_id)
    if subsystem is None:
        raise ValueError(f"subsistema desconocido: {subsystem_id!r}")
    dimensions = effective_dimensions(
        subsystem.dimensions,
        source=f"subsystem:{subsystem.subsystem_id}",
        information_assets=subsystem.information_assets,
        services=subsystem.services,
        inherited=base if subsystem.inheritance else None,
        overrides=controls.overrides,
    )
    return ResolvedProfileScope(subsystem.subsystem_id, subsystem.scope, dimensions, controls)


def evaluate_profile_scope(
    scope: ResolvedProfileScope, measures: Sequence[SecurityMeasure]
) -> ProfileScopeEvaluation:
    levels = {dimension: value.level for dimension, value in scope.dimensions.items()}
    calculated = {item.measure.code for item in applicable_measures(measures, levels)}
    applicable = tuple(
        sorted(
            (calculated | scope.controls.additional_measures) - scope.controls.excluded_measures,
            key=code_order,
        )
    )
    return ProfileScopeEvaluation(
        scope.scope_id,
        scope.scope,
        system_category(levels),
        scope.dimensions,
        scope.controls.chain,
        applicable,
    )


def explain_profile_measure(
    measure: SecurityMeasure, scope: ResolvedProfileScope
) -> MeasureApplicability:
    levels = {dimension: value.level for dimension, value in scope.dimensions.items()}
    level = required_level(measure, levels)
    matched_dimensions = tuple(
        dimension
        for dimension in SecurityDimension
        if level is not None and dimension in measure.dimensions and levels.get(dimension) is level
    )
    table_cell = (
        measure.raw_levels[_LEVELS.index(level)]
        if level is not None and len(measure.raw_levels) == len(_LEVELS)
        else None
    )
    if measure.code in scope.controls.excluded_measures:
        applicable, basis = False, "profile_exclusion"
    elif measure.code in scope.controls.additional_measures:
        applicable, basis = True, "profile_addition"
    elif level is None:
        applicable, basis = False, "unvalued_dimension"
    else:
        applicable = level in measure.levels
        basis = "category" if measure.dimensions == frozenset(SecurityDimension) else "dimension"

    reason = ApplicabilityReason(
        basis,
        matched_dimensions,
        level,
        table_cell,
        scope.controls.chain,
        tuple(
            ApplicabilityEvidence(
                dimension,
                item.source,
                item.level,
                item.justification,
            )
            for dimension in matched_dimensions
            for item in scope.dimensions[dimension].evidence
        ),
    )
    reinforcements = tuple(
        reinforcement
        for reinforcement in sorted(
            measure.reinforcements,
            key=lambda item: (code_order(item.code), item.alternative, item.text),
        )
        if level is not None and reinforcement.level is level
    )
    return MeasureApplicability(
        scope.scope_id,
        measure.code,
        applicable,
        level,
        reason,
        reinforcements,
    )
