"""Pure JSON codec for a captured Anexo II corpus.

The live site is only reachable through a headed Chrome that gets past its WAF,
so a server that scrapes on every call needs Chrome, a display and network just
to answer "what does mp.s.4 require". The Anexo II is a Real Decreto, though —
it does not change between two questions. Serialising the scraped corpus once
turns every later query into a dictionary lookup, and lets the server run where
no browser can.

This module is pure: domain objects in, JSON text out, and back. No Patchright,
no network, no filesystem. ``load(dump(x)) == x`` is the contract, which is why
the frozensets are written sorted — two runs of the same corpus must produce
byte-identical text, or the "did the site change?" check would fire on nothing
but dictionary ordering.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

from ensmcp.domain.models import (
    ApplicabilityLevel,
    AuditRequirement,
    Category,
    CategoryGroup,
    Reinforcement,
    SecurityDimension,
    SecurityMeasure,
)
from ensmcp.domain.queries import code_order
from ensmcp.json_codec import load_object, require_string, require_unique

# Bumped when the JSON shape changes in a way an older file cannot satisfy, so
# a stale snapshot is rejected loudly instead of loading with fields missing.
SCHEMA_VERSION = 3


# Las tres celdas Bajo/Medio/Alto de la tabla, o ninguna: una medida construida
# fuera del scraper no lleva ninguna, y de la tabla viva son siempre las tres.
_RAW_LEVEL_COUNTS = frozenset({0, len(ApplicabilityLevel)})


def _boolean(value: object, where: str) -> bool:
    if not isinstance(value, bool):
        raise TypeError(f"{where} is a {type(value).__name__}, expected a boolean")
    return value


def _integer(value: object, where: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise TypeError(f"{where} is a {type(value).__name__}, expected an integer")
    return value


def _raw_levels(value: object) -> tuple[str, ...]:
    """The verbatim level cells, refusing anything else written in their place.

    A bare string is the shape neither the ``KeyError`` nor the ``TypeError``
    guard in ``load`` can see, because it is not an error in Python at all:
    ``tuple("aplica")`` is six one-character entries. So a hand-edit that wrote
    ``"raw_levels": "aplica"`` loaded without a word and served a measure with
    six verbatim level cells where the table has three.

    Y el recuento, que es la otra mitad y faltaba: una **lista** de seis pasaba
    la guarda entera, porque ser una lista era lo único que se miraba. Llegaba
    igual de rota al payload, sólo que allí ``_measure_to_dict`` la empareja con
    "basico/medio/alto" y se queda con las tres primeras — así que las otras tres
    desaparecían sin una palabra, que es exactamente el desenlace del que este
    docstring se queja en el párrafo de arriba.

    ``TypeError`` rather than ``ValueError`` so it lands in that same guard and
    comes out naming the file, like every other malformed-snapshot message.
    """
    if not isinstance(value, list):
        raise TypeError(f"raw_levels is a {type(value).__name__}, expected a list of strings")
    if len(value) not in _RAW_LEVEL_COUNTS:
        raise TypeError(
            f"raw_levels has {len(value)} cell(s), expected "
            f"{len(ApplicabilityLevel)} (Bajo/Medio/Alto) or none"
        )
    return tuple(require_string(item, f"raw_levels[{index}]") for index, item in enumerate(value))


def _measure_to_json(measure: SecurityMeasure) -> dict[str, Any]:
    return {
        "code": measure.code,
        "title": measure.title,
        "description": measure.description,
        "norm_text": measure.norm_text,
        "category_code": measure.category_code,
        "dimensions": sorted(dimension.value for dimension in measure.dimensions),
        "levels": sorted(level.value for level in measure.levels),
        "reinforcements": [
            {
                "code": reinforcement.code,
                "level": reinforcement.level.value,
                "alternative": reinforcement.alternative,
                "text": reinforcement.text,
            }
            # ``code_order`` for the same reason ``guia.codec`` uses it: sorting
            # "R10" as text puts it before "R2". No cell reaches R10 today —
            # the highest is R9 — so this leaves the file byte for byte as it
            # was, and only removes the trap the reinforcement parser already
            # goes out of its way to keep open (it reads "R10" as R10 on
            # purpose, never as R1).
            #
            # ``text`` cierra la clave, y es el campo que faltaba. Un
            # ``Reinforcement`` tiene cuatro, no tres: dos que coincidan en
            # nivel, código y ``alternative`` pero difieran en la redacción son
            # dos miembros distintos del frozenset con la **misma** clave de
            # orden, así que quedaban en el orden de iteración del conjunto —
            # arbitrario— y ``dump`` dejaba de ser función del corpus. Medido:
            # el mismo corpus, volcado dos veces, salía con las dos redacciones
            # intercambiadas. Eso es exactamente lo que este orden existe para
            # impedir, porque ``RefreshingRepository`` decide "la web difiere"
            # comparando este texto contra el del fichero: un empate podía hacer
            # saltar el aviso de regenerar sin que el ENS Navegable cambiara.
            for reinforcement in sorted(
                measure.reinforcements,
                key=lambda item: (
                    item.level.value,
                    code_order(item.code),
                    item.alternative,
                    item.text,
                ),
            )
        ],
        "raw_levels": list(measure.raw_levels),
        # A list, not a sorted set: the questionnaire's own order is data (it
        # is where the restarting "1.1" codes show their grouping), and
        # ``position`` already pins it, so the file stays deterministic.
        "audit_requirements": [
            {
                "position": requirement.position,
                "code": requirement.code,
                "level": requirement.level.value,
                "essential": requirement.essential,
                "question": requirement.question,
                "note": requirement.note,
            }
            for requirement in measure.audit_requirements
        ],
    }


def _measure_from_json(payload: dict[str, Any]) -> SecurityMeasure:
    code = require_string(payload["code"], "measure.code")
    title = require_string(payload["title"], "measure.title")
    description = require_string(payload["description"], "measure.description")
    norm_text = require_string(payload["norm_text"], "measure.norm_text")
    category_code = require_string(payload["category_code"], "measure.category_code")
    dimensions = [
        SecurityDimension(require_string(value, f"measure.dimensions[{index}]"))
        for index, value in enumerate(payload["dimensions"])
    ]
    levels = [
        ApplicabilityLevel(require_string(value, f"measure.levels[{index}]"))
        for index, value in enumerate(payload["levels"])
    ]
    reinforcements = [
        Reinforcement(
            code=require_string(item["code"], "reinforcement.code"),
            level=ApplicabilityLevel(require_string(item["level"], "reinforcement.level")),
            alternative=_boolean(item["alternative"], "reinforcement.alternative"),
            text=require_string(item["text"], "reinforcement.text"),
        )
        for item in payload["reinforcements"]
    ]
    requirements = [
        AuditRequirement(
            position=_integer(item["position"], "audit_requirement.position"),
            code=require_string(item["code"], "audit_requirement.code"),
            level=ApplicabilityLevel(require_string(item["level"], "audit_requirement.level")),
            essential=_boolean(item["essential"], "audit_requirement.essential"),
            question=require_string(item["question"], "audit_requirement.question"),
            note=require_string(item["note"], "audit_requirement.note"),
        )
        for item in payload["audit_requirements"]
    ]
    require_unique(dimensions, f"dimension in measure {code!r}")
    require_unique(levels, f"level in measure {code!r}")
    require_unique(reinforcements, f"reinforcement in measure {code!r}")
    positions = [requirement.position for requirement in requirements]
    require_unique(positions, f"audit requirement position in measure {code!r}")
    if positions != list(range(len(positions))):
        raise ValueError(
            f"audit requirement positions in measure {code!r} are not consecutive from 0: "
            f"{positions}"
        )
    return SecurityMeasure(
        code=code,
        title=title,
        description=description,
        norm_text=norm_text,
        category_code=category_code,
        dimensions=frozenset(dimensions),
        levels=frozenset(levels),
        reinforcements=frozenset(reinforcements),
        raw_levels=_raw_levels(payload["raw_levels"]),
        audit_requirements=tuple(requirements),
    )


def dump(
    categories: Sequence[Category], measures: Sequence[SecurityMeasure], captured_at: str
) -> str:
    """Serialise a scraped corpus to the snapshot's JSON text.

    ``captured_at`` is passed in rather than read from the clock here: this
    module stays pure, and the caller (``scripts/build_snapshot.py``) is the one
    that knows when the capture actually happened.
    """
    document = {
        "schema_version": SCHEMA_VERSION,
        "captured_at": captured_at,
        "categories": [
            {"code": category.code, "name": category.name, "group": category.group.value}
            for category in categories
        ],
        "measures": [_measure_to_json(measure) for measure in measures],
    }
    # sort_keys and a fixed indent so two dumps of the same corpus differ only
    # when the corpus differs — that byte comparison is the freshness check.
    return json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def load(text: str) -> tuple[list[Category], list[SecurityMeasure], str]:
    """Parse snapshot JSON back into ``(categories, measures, captured_at)``.

    A snapshot written by a different schema version is refused outright: the
    alternative is loading it with whatever fields happen to match and serving
    measures silently missing their reinforcements.

    A file that *declares* the right version but does not have its shape —
    truncated by a partial write, mangled by a bad merge, hand-edited — is
    refused the same way. The version check alone cannot catch that, and
    without this the failure was a bare ``KeyError: 'title'`` or, for a JSON
    array, ``AttributeError: 'list' object has no attribute 'get'``: neither
    says which file is at fault nor that the file is the problem at all. The
    scraping layer already holds itself to this (see ``_parse_row``, which
    turns a short row into a message instead of an ``IndexError``); this is
    the same contract on the other way in.

    ``ValueError`` is in that guard for the same reason the other two are, and
    was the hole left in it: every enum this rebuilds raises one on a value it
    does not know, and ``'xx' is not a valid CategoryGroup`` has exactly the
    defect this docstring complains about in ``KeyError: 'title'`` — it does not
    say which file is at fault, nor that a file is the problem at all.
    """
    document = load_object(text, "snapshot", SCHEMA_VERSION)
    try:
        categories = [
            Category(
                code=require_string(payload["code"], "category.code"),
                name=require_string(payload["name"], "category.name"),
                group=CategoryGroup(require_string(payload["group"], "category.group")),
            )
            for payload in document["categories"]
        ]
        measures = [_measure_from_json(payload) for payload in document["measures"]]
        captured_at = require_string(document["captured_at"], "captured_at")
        require_unique([category.code for category in categories], "category code")
        require_unique([measure.code for measure in measures], "measure code")
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(
            f"snapshot declares schema version {SCHEMA_VERSION} but does not have its shape "
            f"({type(exc).__name__}: {exc})"
        ) from exc
    return categories, measures, captured_at
