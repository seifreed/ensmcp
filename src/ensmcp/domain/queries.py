"""Pure functions that filter and search already-loaded ENS data.

None of these functions talk to a repository — they operate on plain
sequences of domain objects, which is what keeps them trivially testable
with real value objects and no test doubles.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Mapping, Sequence

from ensmcp.domain.models import (
    ApplicableMeasure,
    AuditRequirement,
    DimensionLevel,
    MaturityLevel,
    SecurityDimension,
    SecurityMeasure,
    SystemCategory,
)


def fold(text: str) -> str:
    """Casefold ``text``, strip its diacritics and drop invisible formatting.

    The corpus is Spanish and its readers type Spanish in a hurry: without this,
    "criptografia" matched nothing at all while "criptografía" matched three
    measures. NFKD splits each accented character into a base letter plus a
    combining mark, so dropping the marks (unicode category "Mn") leaves the
    plain letter behind.

    Format characters (category "Cf") go the same way, and for a stronger
    reason: they are *invisible*. A reader cannot type one, cannot see one, and
    cannot tell why a search failed because of one. The live corpus has one —
    ``org.1`` carries ``art`` + ``á`` + U+00AD SOFT HYPHEN + ``culo``, which
    renders as "artáculo" — and before this, not even copying that displayed
    text back into a query matched anything.

    What this does **not** do is repair the corpus. That word is CCN-CERT's own
    typo for "artículo", vowel and all, and folding cannot invent the "í" back:
    searching "artículo" still finds nothing there, correctly. The text is left
    exactly as the site serves it, and it is *matching* that stretches to reach
    it — the same division of labour ``raw_levels`` follows.

    Safe to apply to a value that is about to be parsed as a code or an enum:
    every ENS code and every enum value here is ASCII, which has neither
    combining marks nor format characters — so it only ever widens what
    matches, never narrows it.
    """
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    return "".join(
        char
        for char in decomposed
        if not unicodedata.combining(char) and unicodedata.category(char) != "Cf"
    )


def code_order(code: str) -> tuple[str | int, ...]:
    """Sort key that orders the numbers inside a code by value, not by spelling.

    ``sorted`` on the bare string puts ``op.exp.10`` between ``op.exp.1`` and
    ``op.exp.2``, because ``"1" < "10" < "2"`` character by character. That is
    not a hypothetical: ``op.exp.10`` is a real measure of the Anexo II, and it
    is the only two-digit one, which is exactly why it went unnoticed —
    ``evidencias_auditoria`` served it third while every other tool serves the
    same corpus in the table's own order, so the two lists a client is told to
    read side by side disagreed on one row out of 73.

    Splitting on digit runs keeps the key type-safe by construction: ``re.split``
    with a capturing group always alternates text, number, text, ..., so a given
    position holds the same kind for every code and two keys never compare an
    ``int`` against a ``str``. The same key does for ``R1``/``R10``, which the
    table does not use today but the reinforcement parser deliberately accepts.
    """
    return tuple(int(part) if part.isdigit() else part for part in re.split(r"(\d+)", code))


def _matches_category(measure: SecurityMeasure, category_code: str) -> bool:
    return measure.category_code == category_code or measure.category_code.startswith(
        f"{category_code}."
    )


def filter_measures(
    measures: Sequence[SecurityMeasure],
    category_code: str | None = None,
    dimension: SecurityDimension | None = None,
    level: DimensionLevel | None = None,
) -> list[SecurityMeasure]:
    """Return the measures matching every filter that was provided."""
    result = list(measures)
    if category_code is not None:
        result = [m for m in result if _matches_category(m, category_code)]
    if dimension is not None:
        result = [m for m in result if dimension in m.dimensions]
    if level is not None:
        result = [m for m in result if level in m.levels]
    return result


# El orden del RD, de menor a mayor. Los enums de Python no se comparan con <,
# y el orden alfabético de los valores diría que "alto" < "basico" < "medio",
# que es justo al revés de lo que hace falta para un máximo.
_LEVEL_RANK = {level: rank for rank, level in enumerate(DimensionLevel)}
_CATEGORY_RANK = {category: rank for rank, category in enumerate(SystemCategory)}

DimensionLevels = Mapping[SecurityDimension, DimensionLevel]


def _highest(levels: Iterable[DimensionLevel]) -> DimensionLevel | None:
    """El mayor nivel de los dados, o ``None`` si no hay ninguno."""
    return max(levels, key=lambda level: _LEVEL_RANK[level], default=None)


def system_category(levels: DimensionLevels) -> SystemCategory:
    """La categoría del sistema según el Anexo I, ap. 4 del RD 311/2022.

    ALTA si alguna dimensión alcanza el nivel ALTO; MEDIA si el máximo es
    MEDIO; BÁSICA si el máximo es BAJO. Es decir: el máximo de los niveles
    valorados. Las dimensiones que el sistema no valora no aparecen en
    ``levels`` y no cuentan.

    La categoría se modela como ``SystemCategory`` para conservar la
    terminología oficial, aunque comparta la escala ordinal con los niveles.
    """
    level = _highest(levels.values())
    if level is None:
        raise ValueError("hay que valorar al menos una dimensión para categorizar el sistema")
    return tuple(SystemCategory)[_LEVEL_RANK[level]]


def required_level(measure: SecurityMeasure, levels: DimensionLevels) -> DimensionLevel | None:
    """El nivel del Anexo II al que se le exige ``measure`` a este sistema.

    Una fila marcada "Categoría" se selecciona por la categoría del sistema y
    una fila con dimensiones por el nivel de esas dimensiones — pero
    ``parse_dimension_labels`` ya modela "Categoría" como las cinco dimensiones,
    y el máximo sobre las cinco *es* la categoría del sistema. Las dos ramas de
    la regla colapsan así en una sola fórmula, sin caso especial que mantener.

    ``None`` cuando ninguna de las dimensiones que protege la medida está
    valorada: no hay nivel desde el que exigirla.
    """
    return _highest(levels[dimension] for dimension in measure.dimensions if dimension in levels)


def applicable_measures(
    measures: Sequence[SecurityMeasure], levels: DimensionLevels
) -> list[ApplicableMeasure]:
    """La Declaración de Aplicabilidad de un sistema con estos niveles.

    Para cada medida se mira **una sola celda** de la tabla, la de su nivel
    exigible: las celdas del Anexo II son autocontenidas y ya listan los
    refuerzos acumulados hasta ese nivel (la de ``op.exp.8`` en alto dice
    "+ R1 + R2 + R3 + R4 + R5"), así que sumar columnas los duplicaría.

    La medida entra si esa celda no dice "n.a.", que es exactamente lo que
    ``measure.levels`` ya registra.
    """
    if not levels:
        raise ValueError("hay que valorar al menos una dimensión para generar la DdA")
    applicable: list[ApplicableMeasure] = []
    for measure in measures:
        level = required_level(measure, levels)
        if level is None or level not in measure.levels:
            continue
        applicable.append(
            ApplicableMeasure(
                measure=measure,
                required_level=level,
                reinforcements=frozenset(
                    reinforcement
                    for reinforcement in measure.reinforcements
                    if reinforcement.level is level
                ),
            )
        )
    return applicable


# CCN-STIC 808 (la del RD 311/2022), §6: el nivel mínimo de madurez CMM que el
# auditor exige a cada medida, según la categoría del sistema. Los nombres son
# los de la tabla de la guía, literales.
_MATURITY_BY_CATEGORY = {
    SystemCategory.BASICA: MaturityLevel("L2", "Reproducible, pero intuitivo"),
    SystemCategory.MEDIA: MaturityLevel("L3", "Proceso definido"),
    SystemCategory.ALTA: MaturityLevel("L4", "Gestionado y medible"),
}


def required_maturity_level(category: SystemCategory) -> MaturityLevel:
    """El nivel CMM mínimo que la CCN-STIC 808 §6 exige a esa categoría.

    L2 para BÁSICA, L3 para MEDIA y L4 para ALTA. No es por medida: el auditor
    lo aplica a todas las del sistema.

    Devuelve el nivel con su nombre y no sólo el código. La guía caracteriza
    cada uno —L2 es "reproducible, pero intuitivo": hay prácticas y métricas
    básicas, pero los procedimientos no están suficientemente documentados— y
    esa caracterización es justo lo que separa "me exigen L3" de saber qué hay
    que enseñarle al auditor. Sólo el código obliga a ir a buscar la guía.
    """
    return _MATURITY_BY_CATEGORY[category]


def required_audit_requirements(
    measure: SecurityMeasure, level: DimensionLevel
) -> tuple[AuditRequirement, ...]:
    """Los requisitos de auditoría exigibles a una medida en ``level``.

    **Acumulativo**, y no por el tramo suelto. La CCN-STIC 808 §5 clasifica cada
    requisito por a qué categorías se exige: los de la sección "Categoría
    Básica" son "exigible a todas las categorías", los de "Categoría Media"
    "exigible a categorías MEDIA y ALTA", y los de "Categoría Alta" "exigible a
    categoría ALTA". Un sistema medio, por tanto, responde los de básica **y**
    los de media.

    El nombre de la sección engaña: "Categoría Básica" no es "para sistemas
    básicos", es "para todos". Leerla como el tramo suelto deja fuera la mayor
    parte del temario — 73 preguntas en vez de 382 en un sistema real.
    """
    return tuple(
        requirement
        for requirement in measure.audit_requirements
        if _CATEGORY_RANK[SystemCategory(requirement.level.value)] <= _LEVEL_RANK[level]
    )


def find_measure_by_code(measures: Sequence[SecurityMeasure], code: str) -> SecurityMeasure | None:
    """Return the measure with the exact given code, or None if absent."""
    for measure in measures:
        if measure.code == code:
            return measure
    return None


def search_measures_by_text(
    measures: Sequence[SecurityMeasure], query: str
) -> list[SecurityMeasure]:
    """Return measures whose text contains ``query``, ignoring case and accents.

    "Text" means the code, the title, the description, **the RD's wording for
    the measure** and **the wording of each reinforcement**. Leaving either of
    the last two out makes obligations unreachable that the corpus does carry:
    without the refuerzo wording, "OTP" and "doble factor" found nothing even
    though ``op.acc.5`` and ``op.acc.6`` demand exactly that; without the
    measure's own, a term the RD uses but the questionnaire happens to phrase
    differently misses its measure the same way.

    The audit questionnaire needs no field of its own here — ``description`` is
    that block flattened, so a question or its "NOTA:" is already searchable.

    Matching is **every word of the query**, not the query as one run of
    characters. The corpus is prose and a reader quotes it from memory, so one
    dropped article was enough to answer "nothing": ``op.exp.8`` is titled
    "Registro de la actividad" and searching "registro de actividad" — its own
    name, minus one word — found it not at all. Same for "politica seguridad"
    against ``org.1`` and "segregacion funciones" against ``op.acc.3``. A tool
    whose job is to find measures answered nothing for the near-exact name of
    one that exists.

    This only ever *widens* what matches: if the whole query appears as one run,
    every word of it appears too, so nothing that matched before stops matching
    — verified over the 73 titles and then some. A single-word query is
    unaffected, which is most of them.

    A query with nothing left in it after folding returns nothing, and the test
    is on the **folded** query rather than on what the caller typed. Those are
    not the same set: ``fold`` drops the invisible format characters (category
    "Cf") on purpose, so a query of one U+00AD SOFT HYPHEN — the very character
    the live ``org.1`` carries, and the one a reader is most likely to drag
    along in a paste — is a non-empty string that folds to ``""``. And ``""`` is
    a substring of everything, so it matched all 73 measures: a query that means
    nothing answered with the entire corpus. Same for U+200B, U+FEFF and a bare
    combining mark. Checking before folding cannot see any of that. Splitting
    keeps that guard by construction: a query that folds to nothing has no
    words, and "every word appears" over none of them must not be read as true.
    """
    needles = tuple(fold(query).split())
    if not needles:
        return []
    return [measure for measure in measures if _contains(measure, needles)]


def _contains(measure: SecurityMeasure, needles: tuple[str, ...]) -> bool:
    """Whether one of a measure's texts holds **every** already-folded word."""
    # Field by field rather than one joined string: joining would let a query
    # match across a boundary (title into description), a false positive that
    # does not exist today. That is also why every word has to land in the
    # *same* field rather than anywhere in the measure — spreading them across
    # fields is the same false positive by another route. The reinforcement
    # texts go through a set because the same refuerzo repeats once per level
    # with identical wording, and folding it three times is work for nothing.
    fields = (
        measure.code,
        measure.title,
        measure.description,
        measure.norm_text,
        *{reinforcement.text for reinforcement in measure.reinforcements},
    )
    return any(all(needle in folded for needle in needles) for folded in map(fold, fields))
