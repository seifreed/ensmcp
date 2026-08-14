"""Tests for the ENS domain value objects."""

from __future__ import annotations

import dataclasses
from dataclasses import FrozenInstanceError

import pytest

from ensmcp.domain import models
from ensmcp.domain.models import (
    ApplicabilityLevel,
    Category,
    CategoryGroup,
    Guia808,
    Reinforcement,
    SecurityDimension,
    SecurityMeasure,
)
from tests.support import check

# Anotaciones cuyo tipo es mutable. `frozenset[...]` y `tuple[...]` no empiezan
# por ninguna de ellas, que es justo la distinción que interesa.
_MUTABLE_ANNOTATIONS = ("list", "dict", "set[")


def test_every_domain_value_object_is_frozen_and_deeply_immutable() -> None:
    """El invariante que el docstring de ``models`` promete, sobre todos.

    Hasta ahora cada modelo se comprobaba a mano, y a los cuatro que trajo la
    CCN-STIC 808 nadie les escribió test: así se coló un ``dict`` dentro de una
    dataclass ``frozen``. ``frozen=True`` impide reasignar el campo, no mutar lo
    que hay dentro, y estos objetos se cargan una vez y viven lo que dura el
    proceso — compartidos por cada petición. Recorrer el módulo entero cubre
    también el modelo que alguien añada mañana.
    """
    value_objects = [
        obj
        for obj in vars(models).values()
        if isinstance(obj, type)
        and dataclasses.is_dataclass(obj)
        and obj.__module__ == models.__name__
    ]

    # Guarda contra un pase en vacío, y prueba que el descubrimiento alcanza los
    # dos corpus: el Anexo II y la guía.
    check({SecurityMeasure, Guia808} <= set(value_objects), f"sólo encontré {value_objects}")

    for value_object in value_objects:
        # Por `vars()` y no `value_object.__dataclass_params__`: typeshed no
        # declara ese atributo, así que el acceso directo es un error de
        # mypy --strict que sólo se calla con un `# type: ignore`, prohibido
        # aquí. La búsqueda en el `__dict__` de la clase lee lo mismo.
        options = vars(value_object)["__dataclass_params__"]
        check(options.frozen, f"{value_object.__name__} no es frozen")
        check(options.slots, f"{value_object.__name__} no lleva slots")
        for field in dataclasses.fields(value_object):
            check(
                not str(field.type).startswith(_MUTABLE_ANNOTATIONS),
                f"{value_object.__name__}.{field.name} es {field.type}: mutable pese al frozen",
            )


def test_category_is_immutable_and_holds_its_fields() -> None:
    category = Category(
        code="mp.if", name="Protección de las instalaciones", group=CategoryGroup.MEDIDAS_PROTECCION
    )

    check(category.code == "mp.if")
    check(category.name == "Protección de las instalaciones")
    check(category.group is CategoryGroup.MEDIDAS_PROTECCION)
    # setattr() through a variable field name, not `category.code = ...`: the
    # latter is a *static* mypy error on a frozen dataclass field, but what
    # we want to prove here is the *runtime* guarantee.
    mutable_field = "code"
    with pytest.raises(FrozenInstanceError):
        setattr(category, mutable_field, "org")


def test_security_measure_is_immutable_and_holds_dimensions_and_levels() -> None:
    measure = SecurityMeasure(
        code="mp.if.3",
        title="Protección de las instalaciones",
        description="Descripción de la medida.",
        category_code="mp.if",
        dimensions=frozenset({SecurityDimension.DISPONIBILIDAD}),
        levels=frozenset({ApplicabilityLevel.MEDIO, ApplicabilityLevel.ALTO}),
    )

    check(measure.code == "mp.if.3")
    check(SecurityDimension.DISPONIBILIDAD in measure.dimensions)
    check(ApplicabilityLevel.BASICO not in measure.levels)
    # Regression: reinforcements and raw_levels were added after this shape was
    # already in use, so building a measure without them has to stay valid.
    check(measure.reinforcements == frozenset())
    check(measure.raw_levels == ())
    mutable_field = "code"
    with pytest.raises(FrozenInstanceError):
        setattr(measure, mutable_field, "mp.if.4")


def test_reinforcement_is_immutable_and_pairs_a_code_with_its_level() -> None:
    # A reinforcement only means something paired with its level: "R2" alone
    # says nothing, "R2 en nivel medio" is what a DdA has to state.
    reinforcement = Reinforcement(code="R2", level=ApplicabilityLevel.MEDIO)

    check(reinforcement.code == "R2")
    check(reinforcement.level is ApplicabilityLevel.MEDIO)
    # Required unless the table bracketed it as one option among several.
    check(reinforcement.alternative is False)
    check(
        reinforcement != Reinforcement(code="R2", level=ApplicabilityLevel.MEDIO, alternative=True)
    )
    # Hashable, because SecurityMeasure holds these in a frozenset field.
    check(
        {reinforcement, Reinforcement(code="R2", level=ApplicabilityLevel.MEDIO)} == {reinforcement}
    )
    check(reinforcement != Reinforcement(code="R2", level=ApplicabilityLevel.ALTO))
    mutable_field = "code"
    with pytest.raises(FrozenInstanceError):
        setattr(reinforcement, mutable_field, "R3")


# Every enum value below is wire format, not an internal name: they are what
# list_measures accepts for its `dimension`/`level` filters and what the tool
# payloads carry back in "dimensions", "levels" and "group". Renaming one
# breaks every MCP client, so each set is pinned exactly — which also catches a
# member added or dropped, unlike checking a few values one by one.


def test_category_group_values_match_ens_annex_ii_prefixes() -> None:
    check({group.value for group in CategoryGroup} == {"org", "op", "mp"})


def test_security_dimension_values_are_the_ens_dimension_names() -> None:
    check(
        {dimension.value for dimension in SecurityDimension}
        == {"confidencialidad", "integridad", "disponibilidad", "autenticidad", "trazabilidad"}
    )


def test_applicability_level_values_are_the_ens_level_names() -> None:
    check({level.value for level in ApplicabilityLevel} == {"basico", "medio", "alto"})
