"""MCP server exposing ENS domain queries as tools.

This is the only place domain objects get flattened into plain dicts for
the wire — the domain layer stays free of any MCP/JSON concern, and the
scraping layer is only referenced through the MeasureRepository port.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Collection, Iterable, Sequence
from enum import Enum
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

from ensmcp.domain.models import (
    ApplicableMeasure,
    ArticleCheck,
    AuditRequirement,
    Category,
    DimensionLevel,
    Guia808,
    MaturityLevel,
    MeasureEvidence,
    SecurityDimension,
    SecurityMeasure,
    SystemCategory,
)
from ensmcp.domain.queries import (
    applicable_measures,
    code_order,
    filter_measures,
    find_measure_by_code,
    fold,
    required_audit_requirements,
    required_maturity_level,
    search_measures_by_text,
    system_category,
)
from ensmcp.domain.repository import MeasureRepository

RefreshHandler = Callable[[], Awaitable[None]]
# Returns whatever the data source wants to report about its own freshness. The
# server forwards it untouched, so nothing here has to know that a snapshot
# exists — the same reason ``refresh`` is a callable and not a repository method.
StatusHandler = Callable[[], dict[str, object]]

_READ_ONLY = ToolAnnotations(read_only_hint=True, idempotent_hint=True, open_world_hint=False)
_EXTERNAL_READ = ToolAnnotations(read_only_hint=True, idempotent_hint=True, open_world_hint=True)

# Bajo/Medio/Alto, in the order the ENS itself ranks them. Sorting the wire
# payload by level *value* would instead give "alto, bajo, medio", alphabetical
# and meaningless to a reader.
_LEVEL_SORT_ORDER = tuple(DimensionLevel)
_LEVEL_COLUMN_NAMES = tuple(level.value for level in _LEVEL_SORT_ORDER)

# Y lo mismo para las dimensiones, que también tienen orden propio: la columna
# de la tabla las escribe "C I D A T", que es el del RD y el que declara
# ``SecurityDimension``. Alfabéticamente saldría "autenticidad,
# confidencialidad, disponibilidad, integridad, trazabilidad", que no es el de
# ninguna fuente.
_DIMENSION_SORT_ORDER = tuple(SecurityDimension)


def _ordered[E: Enum](members: frozenset[E], order: tuple[E, ...]) -> list[str]:
    """Los valores de un conjunto de enums, en el orden en que el RD los nombra.

    Un ``sorted`` pelado sobre los valores es lo que había, y es justo lo que el
    comentario de ``_LEVEL_SORT_ORDER`` dice que no vale: dejaba ``levels`` como
    ``["alto", "bajo", "medio"]`` en **cada** medida de **cada** payload, o sea
    la escala del ENS puesta del revés en el campo que más se lee. La regla ya
    estaba escrita ahí arriba y sólo la cumplían los refuerzos.
    """
    return [member.value for member in order if member in members]


def _category_to_dict(category: Category) -> dict[str, str]:
    return {"code": category.code, "name": category.name, "group": category.group.value}


def _measure_to_dict(
    measure: SecurityMeasure, *, compact: bool = False, include_norm_text: bool = True
) -> dict[str, Any]:
    if compact:
        payload: dict[str, Any] = {
            "code": measure.code,
            "title": measure.title,
            "category_code": measure.category_code,
            "dimensions": _ordered(measure.dimensions, _DIMENSION_SORT_ORDER),
            "levels": _ordered(measure.levels, _LEVEL_SORT_ORDER),
        }
        if include_norm_text:
            payload["norm_text"] = measure.norm_text
        return payload

    payload = {
        "code": measure.code,
        "title": measure.title,
        "description": measure.description,
        # Lo que exige el RD, junto a lo que pregunta la 808 (``description``).
        # Los dos, y no uno: quien prepara una auditoría quiere el cuestionario,
        # quien implanta la medida quiere el requisito, y la pregunta no permite
        # reconstruir el requisito.
        "norm_text": measure.norm_text,
        "category_code": measure.category_code,
        "dimensions": _ordered(measure.dimensions, _DIMENSION_SORT_ORDER),
        "levels": _ordered(measure.levels, _LEVEL_SORT_ORDER),
        "reinforcements": [
            {
                "code": reinforcement.code,
                "level": reinforcement.level.value,
                # True = "apply one of the alternatives flagged this way at
                # this level"; False = required outright. Collapsing the two
                # would misstate what a system actually has to implement.
                "alternative": reinforcement.alternative,
                "text": reinforcement.text,
            }
            for reinforcement in sorted(
                measure.reinforcements,
                # ``alternative`` closes the key, as it does in snapshot.codec:
                # reinforcements come out of a frozenset, whose iteration order
                # is arbitrary, so a key that does not separate two otherwise
                # equal entries would let the payload's order wobble between
                # runs. Nothing in the real table produces such a pair, which is
                # exactly why the guard belongs in the key rather than in a test.
                # ``code_order`` on the code, as in both codecs: plain string
                # order would put an "R10" ahead of "R2". And ``text`` closes
                # the key — the reasoning above enumerated three fields of a
                # ``Reinforcement`` and it has four, so two entries differing
                # only in their wording tied and came out in the frozenset's
                # arbitrary order, which is the wobble this key exists to stop.
                key=lambda item: (
                    _LEVEL_SORT_ORDER.index(item.level),
                    code_order(item.code),
                    item.alternative,
                    item.text,
                ),
            )
        ],
        # zip without strict= on purpose: a measure built outside the scraper
        # carries no raw cells at all, and {} says exactly that. From the live
        # table the tuple always has the three cells.
        "raw_levels": dict(zip(_LEVEL_COLUMN_NAMES, measure.raw_levels, strict=False)),
    }
    if not include_norm_text:
        payload.pop("norm_text")
    return payload


def _applicable_to_dict(
    applicable: ApplicableMeasure, *, compact: bool = False, include_norm_text: bool = True
) -> dict[str, Any]:
    """One Declaración de Aplicabilidad line: the measure, plus what it demands.

    The measure keeps the exact shape every other tool returns, so a client
    parses one kind of measure object. ``required_reinforcements`` drops the
    per-reinforcement ``level``, which would only repeat ``required_level``.
    """
    return {
        **_measure_to_dict(
            applicable.measure, compact=compact, include_norm_text=include_norm_text
        ),
        "required_level": applicable.required_level.value,
        "required_reinforcements": [
            {
                "code": reinforcement.code,
                "alternative": reinforcement.alternative,
                "text": reinforcement.text,
            }
            # ``alternative`` closes the key here for the same reason it does in
            # ``_measure_to_dict`` and in ``snapshot.codec``, and this was the
            # one of the three that lacked it. Every reinforcement in this set
            # already shares one level (``applicable_measures`` filtered them to
            # it), so the code is the *only* other field — and a cell naming one
            # both inside and outside its brackets yields the pair
            # (R1, alternative) and (R1, required), which the code alone cannot
            # separate. Out of a frozenset, whose iteration order is arbitrary,
            # that pair would then come out in a different order between runs.
            for reinforcement in sorted(
                applicable.reinforcements,
                key=lambda item: (code_order(item.code), item.alternative, item.text),
            )
        ],
    }


def _requirement_to_dict(measure_code: str, requirement: AuditRequirement) -> dict[str, Any]:
    """One audit question, carrying the measure it belongs to.

    The list is flat and each item names its measure, so a client asking for a
    whole level does not have to walk a nested structure to know what it is
    looking at.
    """
    return {
        "measure_code": measure_code,
        "position": requirement.position,
        "code": requirement.code,
        "level": requirement.level.value,
        "essential": requirement.essential,
        "question": requirement.question,
        "note": requirement.note,
    }


def _audited_to_dict(
    applicable: ApplicableMeasure,
    *,
    compact: bool = False,
    include_norm_text: bool = True,
    include_questions: bool = True,
) -> dict[str, Any]:
    """One ``alcance_auditoria`` line: the measure, and what it gets asked.

    The sibling of ``_applicable_to_dict`` — same measure shape, same
    ``required_level``, but carrying the questionnaire for that level instead of
    the reinforcements. Both live here rather than inline in their tool so the
    domain-to-wire flattening stays in one place in this module.
    """
    payload = {
        **_measure_to_dict(
            applicable.measure, compact=compact, include_norm_text=include_norm_text
        ),
        "required_level": applicable.required_level.value,
    }
    if include_questions:
        payload["audit_requirements"] = [
            _requirement_to_dict(applicable.measure.code, requirement)
            for requirement in required_audit_requirements(
                applicable.measure, applicable.required_level
            )
        ]
    return payload


def _article_to_dict(article: ArticleCheck) -> dict[str, Any]:
    return {
        "reference": article.reference,
        "title": article.title,
        "evidence": list(article.evidence),
        "questions": [
            {"reference": question.reference, "question": question.question}
            for question in article.questions
        ],
    }


def _evidence_to_dict(item: MeasureEvidence) -> dict[str, Any]:
    return {"measure_code": item.measure_code, "evidence": list(item.evidence)}


def _maturity_to_dict(level: MaturityLevel) -> dict[str, str]:
    """The CMM level with its name, never the bare code.

    Every other code this server puts on the wire travels with what it means: a
    reinforcement with its ``text``, a requirement with its ``question``. This
    one used to be the exception — a plain ``"L4"``, which is only useful to
    someone who already has the guide open.
    """
    return {"code": level.code, "name": level.name}


def _normalize(value: str) -> str:
    """Strip, casefold and unaccent one boundary string.

    Real ENS codes and enum values are lowercase ASCII, so this lets ``"MP"`` /
    ``"Confidencialidad"`` / ``"ORG.1"`` match without altering any valid value
    — and ``level="Básico"`` resolve to ``bajo`` instead of failing as an
    unknown enum member, which is what a Spanish speaker will type. The one
    transform every free-text tool argument needs before it reaches domain code.
    """
    return fold(value.strip())


def _normalize_filter_value(value: str | None) -> str | None:
    """Normalize an optional string filter at the tool boundary.

    An empty or whitespace-only value means "no filter" (return ``None``), the
    same as if the argument had not been supplied at all. This keeps the three
    ``list_measures`` filters consistent: ``dimension=""`` and ``dimension="   "``
    both behave like ``dimension=None``.
    """
    if value is None:
        return None
    return _normalize(value) or None


def _category_vocabulary(measures: Iterable[SecurityMeasure]) -> set[str]:
    """Every code ``category_code`` could match, taken from the measures.

    Not from category headers: a table can legitimately carry measure rows
    with no category header above them — rows are recognised by their own class,
    never by what precedes them, and ``_reject_a_measureless_corpus`` says so in
    as many words. Validating against the header rows therefore refused every
    filter on a corpus that has none, which is a real shape and one the fixtures
    rely on.

    Taking it from the measures instead makes the vocabulary exactly what the
    filter operates on: a measure's own ``category_code`` and each of its dotted
    prefixes, because ``_matches_category`` accepts a group ("mp") as readily as
    a subcategory ("mp.if"). On the live corpus that is the same 18 codes
    ``list_categories`` serves, derived rather than assumed.
    """
    codes: set[str] = set()
    for measure in measures:
        parts = measure.category_code.split(".")
        codes.update(".".join(parts[: depth + 1]) for depth in range(len(parts)))
    return codes


def _known_code(raw: str | None, known: Collection[str], argument: str, no_such: str) -> str | None:
    """One argument naming something the corpus has, refusing what it does not.

    Blank still means "no filter", exactly as ``_normalize_filter_value`` says.
    What changes is the unknown code, which used to answer with an empty list —
    and an empty list is *also* a real answer here, which is the whole problem:
    the CCN-STIC 808 writes no questions at all for ``mp.com.2`` below nivel
    alto, so ``requisitos_auditoria(code="mp.com.2", level="basico")`` is
    legitimately empty. The two were the same bytes on the wire, so a typo came
    back indistinguishable from a fact about the ENS.

    And it reads as the fact, not as the typo. ``op.acc`` stops at 6, so a
    caller that asks about "op.acc.9" was told, in effect, that the ENS defines
    no audit requirements for it — a false statement about a Real Decreto,
    produced by a compliance tool, with nothing in the payload to hint that the
    code was never real.

    ``list_measures``' ``category_code`` has the same defect and gets the same
    answer: every one of the 18 categories has at least one measure, so an empty
    result from that filter *alone* never meant "this category is empty" — it
    only ever meant the argument was not a category. (With a dimension or a
    level alongside it, empty is real: ``op.cont`` protects only disponibilidad,
    so pairing it with any other dimension is legitimately nothing. Checking the
    category against the vocabulary on its own keeps those apart.)

    Same reasoning as ``_parse_optional_enum`` below, and the same shape of
    answer: the callers are language models that will guess a code as readily as
    they guess an enum value, and a client cannot correct itself from silence.
    ``raw`` is quoted as the caller typed it, not folded, for the reason given
    there.
    """
    wanted = _normalize_filter_value(raw)
    if wanted is None or wanted in known:
        return wanted
    raise ValueError(f"{argument}={raw!r} {no_such}")


def _paginate[T](
    items: Sequence[T], limit: int | None, cursor: str | None
) -> list[T] | dict[str, Any]:
    """Keep legacy list responses until a caller explicitly asks for a page."""
    if limit is None and cursor is None:
        return list(items)
    if limit is None:
        limit = 50
    if not 1 <= limit <= 500:
        raise ValueError("limit debe estar entre 1 y 500")
    try:
        start = 0 if cursor is None else int(cursor)
    except ValueError as exc:
        raise ValueError("cursor debe ser un índice entero") from exc
    if start < 0 or start > len(items):
        raise ValueError("cursor fuera del rango de resultados")
    end = min(start + limit, len(items))
    return {
        "items": list(items[start:end]),
        "next_cursor": str(end) if end < len(items) else None,
    }


def _filter_measure_codes(
    measures: Sequence[SecurityMeasure], codes: list[str] | None
) -> list[SecurityMeasure]:
    if codes is None:
        return list(measures)
    known = {measure.code for measure in measures}
    wanted = {_known_code(code, known, "measure_codes", _NO_SUCH_MEASURE) for code in codes}
    return [measure for measure in measures if measure.code in wanted]


_NO_SUCH_MEASURE = "no es ninguna medida del Anexo II; use list_measures para ver las que existen"
_NO_SUCH_CATEGORY = (
    "no es ninguna categoría del Anexo II; use list_categories para ver las que existen"
)


def _parse_optional_enum[E: Enum](enum_type: type[E], raw: str | None, argument: str) -> E | None:
    """Resolve one enum-valued tool argument, or say what would have worked.

    Blank still means "no filter", exactly as ``_normalize_filter_value`` says.
    What changes is the failure: this is the only place input from outside the
    process arrives, and its callers are language models that will guess.

    Every guess in the ENS's own vocabulary is wrong. The Anexo II table heads
    its first level column "Bajo"; the RD names the categories "BÁSICA / MEDIA
    / ALTA"; this very server answers ``categoria_sistema: "alta"``. Yet the
    enum's own ``ValueError`` said only "'bajo' is not a valid
    ApplicabilityLevel" — naming a Python class that appears in no tool schema,
    no docstring and no payload, while withholding the three words that would
    have worked. A client cannot correct itself from that.

    ``raw`` is quoted as the caller typed it, not folded. Being told "'alta' is
    not a valid..." after sending ``"ALTA"`` hides the normalisation and reads
    like the server mangled the argument.
    """
    normalized = _normalize_filter_value(raw)
    if normalized is None:
        return None
    try:
        return enum_type(normalized)
    except ValueError:
        accepted = ", ".join(member.value for member in enum_type)
        raise ValueError(
            f"{argument}={raw!r} no es un valor válido; use uno de: {accepted}"
        ) from None


def _parse_dimension_levels(**by_name: str | None) -> dict[SecurityDimension, DimensionLevel]:
    """Turn the five optional tool arguments into the domain's level mapping.

    An omitted (or blank) dimension is one the system does not value, so it is
    left out of the mapping entirely rather than defaulted to a level — a
    defaulted "bajo" would silently pull in measures nobody asked for.
    """
    levels: dict[SecurityDimension, DimensionLevel] = {}
    for name, raw in by_name.items():
        # ``name`` is this function's own keyword, never client input, so only
        # the level needs the boundary's message.
        level = _parse_optional_enum(DimensionLevel, raw, name)
        if level is None:
            continue
        levels[SecurityDimension(name)] = level
    return levels


def build_server(
    repository: MeasureRepository,
    *,
    refresh: RefreshHandler | None = None,
    status: StatusHandler | None = None,
    guia: Guia808 | None = None,
) -> MCPServer:
    """Build the MCP server, wiring each tool to ``repository``.

    Each query re-fetches from ``repository`` rather than caching here: it is
    ``NavegableRepository`` that re-scrapes the live DOM on every call (see
    its own docstring), so a query made right after ``refresh_live_page``
    always reflects the reloaded page.

    ``refresh`` and ``status`` are infrastructure (reloading the live page,
    reporting how fresh the served data is), not domain queries, so they stay
    here in the server wiring rather than on the ``MeasureRepository`` port.
    Each one supplied exposes its tool; each one left ``None`` (e.g. a plain
    fixture server) omits it. ``guia`` is the same idea for the CCN-STIC 808
    data, which comes from the guide rather than from the site: without it the
    server still answers everything the ENS Navegable publishes.
    """
    server: MCPServer = MCPServer(
        name="ensmcp",
        instructions="Consulta las medidas de seguridad del ENS Navegable (CCN-CERT).",
    )

    def _resource_json(value: object) -> str:
        return json.dumps(value, ensure_ascii=False, sort_keys=True)

    @server.resource(
        "ens://anexo-ii",
        name="anexo-ii",
        title="ENS Anexo II",
        description="Snapshot completo de categorías y medidas del Anexo II.",
        mime_type="application/json",
    )
    async def anexo_ii_resource() -> str:
        categories, measures = await repository.fetch_corpus()
        return _resource_json(
            {
                "categories": [_category_to_dict(category) for category in categories],
                "measures": [_measure_to_dict(measure) for measure in measures],
            }
        )

    @server.resource(
        "ens://measures/{code}",
        name="measure",
        title="ENS measure",
        description="Una medida del Anexo II por código.",
        mime_type="application/json",
    )
    async def measure_resource(code: str) -> str:
        _, measures = await repository.fetch_corpus()
        measure = find_measure_by_code(measures, _normalize(code))
        if measure is None:
            raise ValueError(f"{code!r} no es una medida del Anexo II")
        return _resource_json(_measure_to_dict(measure))

    @server.resource(
        "ens://categories/{code}",
        name="category",
        title="ENS category",
        description="Una categoría del Anexo II por código.",
        mime_type="application/json",
    )
    async def category_resource(code: str) -> str:
        categories, _ = await repository.fetch_corpus()
        wanted = _normalize(code)
        category = next((item for item in categories if item.code == wanted), None)
        if category is None:
            raise ValueError(f"{code!r} no es una categoría del Anexo II")
        return _resource_json(_category_to_dict(category))

    @server.resource(
        "ens://data/status",
        name="data-status",
        title="ENS data status",
        description="Origen y estado de frescura del corpus servido.",
        mime_type="application/json",
    )
    async def data_status_resource() -> str:
        payload = status() if status is not None else {"source": "repository"}
        return _resource_json(payload)

    if guia is not None:

        @server.resource(
            "ens://guide/808/articles",
            name="guide-808-articles",
            title="CCN-STIC 808 articles",
            description="Comprobaciones sobre el articulado del RD 311/2022.",
            mime_type="application/json",
        )
        async def guide_articles_resource() -> str:
            return _resource_json([_article_to_dict(article) for article in guia.articles])

        @server.resource(
            "ens://guide/808/evidence/{code}",
            name="guide-808-evidence",
            title="CCN-STIC 808 evidence",
            description="Evidencias de auditoría de una medida.",
            mime_type="application/json",
        )
        async def guide_evidence_resource(code: str) -> str:
            wanted = _normalize(code)
            items = [
                _evidence_to_dict(item)
                for item in guia.measure_evidence
                if item.measure_code == wanted
            ]
            if not items:
                raise ValueError(f"{code!r} no tiene evidencias en CCN-STIC 808")
            return _resource_json(items[0])

    @server.tool(annotations=_READ_ONLY, structured_output=True)
    async def list_categories() -> list[dict[str, str]]:
        """Lista todas las categorías del Anexo II (org, op.pl, mp.if, ...)."""
        categories, _ = await repository.fetch_corpus()
        return [_category_to_dict(category) for category in categories]

    @server.tool(annotations=_READ_ONLY, structured_output=True)
    async def list_measures(
        category_code: str | None = None,
        dimension: str | None = None,
        level: str | None = None,
        limit: int | None = None,
        cursor: str | None = None,
        compact: bool = False,
        include_norm_text: bool = True,
    ) -> list[dict[str, Any]] | dict[str, Any]:
        """Lista medidas de seguridad con filtros opcionales.

        category_code: código de categoría o grupo, p. ej. "mp.if" o "mp".
            Uno que no sea una categoría del Anexo II es un error: todas las
            categorías tienen medidas, así que una lista vacía sólo podía
            significar que el argumento no era una categoría.
        dimension: "confidencialidad", "integridad", "disponibilidad",
            "autenticidad" o "trazabilidad".
        level: "bajo", "medio" o "alto". Se acepta "basico" por compatibilidad.
        """
        _, measures = await repository.fetch_corpus()
        filtered = filter_measures(
            measures,
            category_code=_known_code(
                category_code,
                _category_vocabulary(measures),
                "category_code",
                _NO_SUCH_CATEGORY,
            ),
            dimension=_parse_optional_enum(SecurityDimension, dimension, "dimension"),
            level=_parse_optional_enum(DimensionLevel, level, "level"),
        )
        return _paginate(
            [
                _measure_to_dict(measure, compact=compact, include_norm_text=include_norm_text)
                for measure in filtered
            ],
            limit,
            cursor,
        )

    @server.tool(annotations=_READ_ONLY, structured_output=True)
    async def get_measure(code: str) -> dict[str, Any] | None:
        """Obtiene una medida de seguridad por su código exacto, p. ej. "org.1"."""
        _, measures = await repository.fetch_corpus()
        # _normalize() makes the lookup case-insensitive at the boundary, like
        # list_measures's category_code: a code pasted in upper case ("ORG.1")
        # resolves to the same measure instead of silently returning None. An
        # empty/whitespace code has no match (no measure has an empty code).
        measure = find_measure_by_code(measures, _normalize(code))
        return _measure_to_dict(measure) if measure is not None else None

    @server.tool(annotations=_READ_ONLY, structured_output=True)
    async def search_measures(
        query: str,
        limit: int | None = None,
        cursor: str | None = None,
        compact: bool = False,
        include_norm_text: bool = True,
    ) -> list[dict[str, Any]] | dict[str, Any]:
        """Busca medidas por texto: código, título, cuestionario, redacción del RD.

        Mira el `code`, el `title`, la `description` (el cuestionario de la
        CCN-STIC 808), el `norm_text` (lo que exige el RD 311/2022) y el `text`
        de cada refuerzo. Ignora mayúsculas y tildes.
        """
        _, measures = await repository.fetch_corpus()
        # Stripping and the "a query that means nothing matches nothing" rule
        # both live in ``search_measures_by_text``, on the folded needle: doing
        # it here, on what the caller typed, missed every query made only of
        # characters that folding removes (see that function's docstring).
        matches = search_measures_by_text(measures, query)
        return _paginate(
            [
                _measure_to_dict(measure, compact=compact, include_norm_text=include_norm_text)
                for measure in matches
            ],
            limit,
            cursor,
        )

    @server.tool(annotations=_READ_ONLY, structured_output=True)
    async def declaracion_aplicabilidad(
        confidencialidad: str | None = None,
        integridad: str | None = None,
        disponibilidad: str | None = None,
        autenticidad: str | None = None,
        trazabilidad: str | None = None,
        measure_codes: list[str] | None = None,
        limit: int | None = None,
        cursor: str | None = None,
        compact: bool = False,
        include_norm_text: bool = True,
    ) -> dict[str, Any]:
        """Medidas y refuerzos exigibles a un sistema, para su DdA.

        Cada dimensión toma "bajo", "medio" o "alto" — el nivel al que está
        valorada en ese sistema (Anexo I). Se acepta "basico" por compatibilidad
        y se omite si el sistema no la
        valora. Hay que valorar al menos una.

        Devuelve la categoría del sistema (el mayor de esos niveles) y, por
        cada medida exigible, el nivel al que se le exige y los refuerzos de
        ese nivel. Un refuerzo con `alternative: true` es una opción entre
        varias: basta implantar uno de los marcados así en ese nivel.
        """
        levels = _parse_dimension_levels(
            confidencialidad=confidencialidad,
            integridad=integridad,
            disponibilidad=disponibilidad,
            autenticidad=autenticidad,
            trazabilidad=trazabilidad,
        )
        _, measures = await repository.fetch_corpus()
        applicable = applicable_measures(_filter_measure_codes(measures, measure_codes), levels)
        page: list[dict[str, Any]] | dict[str, Any] = _paginate(
            [
                _applicable_to_dict(item, compact=compact, include_norm_text=include_norm_text)
                for item in applicable
            ],
            limit,
            cursor,
        )
        return {
            "categoria_sistema": system_category(levels).value,
            "measures": page,
        }

    @server.tool(annotations=_READ_ONLY, structured_output=True)
    async def alcance_auditoria(
        confidencialidad: str | None = None,
        integridad: str | None = None,
        disponibilidad: str | None = None,
        autenticidad: str | None = None,
        trazabilidad: str | None = None,
        measure_codes: list[str] | None = None,
        limit: int | None = None,
        cursor: str | None = None,
        compact: bool = False,
        include_norm_text: bool = True,
        include_questions: bool = True,
        include_evidence: bool = False,
    ) -> dict[str, Any]:
        """El temario de auditoría de un sistema: qué le van a preguntar.

        Mismos argumentos que `declaracion_aplicabilidad` — el nivel de cada
        dimensión, u omitida si el sistema no la valora.

        Devuelve sólo las medidas que le aplican y, por cada una, los requisitos
        de verificación exigibles **acumulados**: los de "Categoría Básica" se
        exigen a todas las categorías, los de "Media" a MEDIA y ALTA, y los de
        "Alta" sólo a ALTA (CCN-STIC 808 §5). Un sistema medio responde los de
        básica y los de media.

        `nivel_madurez_requerido` es el mínimo CMM que el auditor exige a cada
        medida según la categoría (CCN-STIC 808 §6), con su `code` y su
        `name`: BÁSICA → L2 "Reproducible, pero intuitivo", MEDIA → L3
        "Proceso definido", ALTA → L4 "Gestionado y medible". `essential` marca los
        requisitos cuyo incumplimiento hace que la medida entera cuente como no
        implantada.
        """
        levels = _parse_dimension_levels(
            confidencialidad=confidencialidad,
            integridad=integridad,
            disponibilidad=disponibilidad,
            autenticidad=autenticidad,
            trazabilidad=trazabilidad,
        )
        _, measures = await repository.fetch_corpus()
        category = system_category(levels)
        audited = applicable_measures(_filter_measure_codes(measures, measure_codes), levels)
        page_items = [
            _audited_to_dict(
                item,
                compact=compact,
                include_norm_text=include_norm_text,
                include_questions=include_questions,
            )
            for item in audited
        ]
        if include_evidence and guia is not None:
            evidence_by_code = {
                item.measure_code: list(item.evidence) for item in guia.measure_evidence
            }
            for item in page_items:
                item["evidence"] = evidence_by_code.get(item["code"], [])
        return {
            "categoria_sistema": category.value,
            "nivel_madurez_requerido": _maturity_to_dict(required_maturity_level(category)),
            "measures": _paginate(page_items, limit, cursor),
        }

    @server.tool(annotations=_READ_ONLY, structured_output=True)
    async def requisitos_auditoria(
        code: str | None = None,
        level: str | None = None,
        limit: int | None = None,
        cursor: str | None = None,
        essential_only: bool = False,
    ) -> list[dict[str, Any]] | dict[str, Any]:
        """Preguntas del cuestionario de auditoría (CCN-STIC 808), en bruto.

        code: una medida concreta, p. ej. "org.1". Omitido, devuelve el
            cuestionario entero. Un código que no sea una medida del Anexo II
            es un error, no una lista vacía: hay medidas cuyo cuestionario está
            legítimamente vacío en un tramo, y las dos cosas no pueden
            contestarse igual.
        level: "basica", "media" o "alta" — las categorías oficiales del sistema.
            tools. Filtra por **la sección** en que la guía imprime el
            requisito ("Categoría Básica", "Media" y "Alta" respectivamente),
            que NO es el temario de un sistema de esa categoría: los requisitos
            son acumulativos y los de "Categoría Básica" se exigen a todas.
            Para el temario real de un sistema usa `alcance_auditoria`.

        Cada elemento trae `essential`: si uno esencial no se cumple, el auditor
        considera la medida entera como no implantada. Ojo con `code` dentro de
        una medida — es la etiqueta que imprime el sitio y se repite (hay cinco
        "1.1" distintos en op.acc.5); lo que identifica un requisito es
        `position`.
        """
        _, measures = await repository.fetch_corpus()
        measure_code = _known_code(
            code, {measure.code for measure in measures}, "code", _NO_SUCH_MEASURE
        )
        if measure_code is not None:
            measures = [measure for measure in measures if measure.code == measure_code]
        wanted = _parse_optional_enum(SystemCategory, level, "level")
        requirements = [
            _requirement_to_dict(measure.code, requirement)
            for measure in measures
            for requirement in measure.audit_requirements
            if (wanted is None or requirement.level is wanted)
            and (not essential_only or requirement.essential)
        ]
        return _paginate(requirements, limit, cursor)

    if guia is not None:

        @server.tool(annotations=_READ_ONLY, structured_output=True)
        async def requisitos_articulos() -> list[dict[str, Any]]:
            """Comprobaciones de auditoría sobre el articulado del RD 311/2022.

            Una auditoría verifica el articulado además del Anexo II, y esta es
            esa mitad: las preguntas documentales y de gobierno (Declaración de
            Aplicabilidad firmada, categorización, INES, perfiles...) por las
            que suele empezar el auditor.

            `evidence` son los documentos que la guía propone que pida.
            Fuente: CCN-STIC 808 §6.1, no el ENS Navegable.
            """
            return [_article_to_dict(article) for article in guia.articles]

        @server.tool(annotations=_READ_ONLY, structured_output=True)
        async def evidencias_auditoria(code: str | None = None) -> list[dict[str, Any]]:
            """Qué documentación puede pedir el auditor, por medida.

            code: una medida concreta, p. ej. "org.1". Omitido, todas. Un
                código que no sea una medida del Anexo II es un error.

            Responde a "¿qué papeles preparo?", que es el trabajo de las
            semanas previas a la auditoría. Se une por `measure_code` con lo que
            devuelven `alcance_auditoria` y `declaracion_aplicabilidad`.
            Fuente: CCN-STIC 808 §6.2, no el ENS Navegable.
            """
            wanted = _known_code(
                code,
                {item.measure_code for item in guia.measure_evidence},
                "code",
                _NO_SUCH_MEASURE,
            )
            # No sorting here: Guia808 arrives in the Anexo II's own order (see
            # its codec), which is the order every other tool serves, so this
            # only filters.
            return [
                _evidence_to_dict(item)
                for item in guia.measure_evidence
                if wanted is None or item.measure_code == wanted
            ]

    if refresh is not None:

        @server.tool(annotations=_EXTERNAL_READ, structured_output=True)
        async def refresh_live_page() -> dict[str, str]:
            """Comprueba ahora la página live de ENS Navegable y actualiza si cambió."""
            await refresh()
            return {"status": "ok"}

    if status is not None:

        @server.tool(annotations=_READ_ONLY, structured_output=True)
        async def snapshot_status() -> dict[str, object]:
            """Origen y frescura de los datos servidos.

            `captured_at`: cuándo se capturó lo que se está sirviendo — la fecha
                del snapshot, o el momento de la comprobación si la web difería
                y ya se sirve lo suyo (`live_check: "updated"`).
            `live_check`: "pending" (aún sin comprobar), "unchanged" (la web
                coincide), "updated" (la web difería y se sirve ya lo nuevo) o
                "unavailable" (no se pudo abrir la web: sin Chrome, sin display
                o sin red — se sigue sirviendo el snapshot).
            `guia_808`: de qué edición de la CCN-STIC 808 salieron
                `requisitos_articulos` y `evidencias_auditoria`. Sólo aparece si
                el servidor lleva la guía cargada.
            """
            payload = status()
            # La atribución de la guía se extrae de su portada, viaja en el
            # fichero y hasta aquí no salía del proceso: ninguna tool la ponía en
            # el cable. Y la edición es justo lo que decide si el dato vale — la
            # serie 800 sigue circulando en ediciones escritas para el RD 3/2010,
            # que el RD 311/2022 derogó, y ``guia.codec`` descarta cuatro por eso.
            # Sin esto, quien audita no puede saber contra qué versión lo hace,
            # que es literalmente lo que ``parse_source`` dice que este campo
            # existe para impedir. Va aquí, donde ya se contesta el origen y la
            # frescura del otro corpus, y como clave nueva: nada de lo que ya se
            # servía cambia de forma.
            if guia is not None:
                payload["guia_808"] = guia.source
            return payload

    return server
