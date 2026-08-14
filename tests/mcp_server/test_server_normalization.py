"""Offline tests for the MCP boundary: normalization and payload ordering.

``_normalize_filter_value`` is pure (no repository, no browser), so the
empty/whitespace/wrongcase edge cases that the boundary fix targets can be
exercised here without the live site.
"""

from __future__ import annotations

import pytest
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import CallToolResult

from ensmcp.domain.models import (
    ApplicabilityLevel,
    SecurityDimension,
    SecurityMeasure,
)
from ensmcp.mcp_server.server import _normalize_filter_value, build_server
from ensmcp.scraping.parsers import parse_category, parse_measure
from ensmcp.snapshot.codec import dump
from ensmcp.snapshot.repository import SnapshotRepository
from tests.support import a_reinforcement_tie_the_set_iterates_backwards, check

# Bajo/Medio/Alto y C/I/D/A/T en el orden que el ENS los jerarquiza y los
# nombra, que es el que el payload usa — nunca el alfabético de sus valores.
_LEVELS = [level.value for level in ApplicabilityLevel]
_DIMENSIONS = [dimension.value for dimension in SecurityDimension]


def test_normalize_filter_value_treats_none_empty_and_whitespace_as_no_filter() -> None:
    check(_normalize_filter_value(None) is None)
    check(_normalize_filter_value("") is None)
    check(_normalize_filter_value("   ") is None)
    check(_normalize_filter_value("\t\n") is None)


def test_normalize_filter_value_strips_and_casefolds_a_real_value() -> None:
    check(_normalize_filter_value("MP") == "mp")
    check(_normalize_filter_value("  mp.if  ") == "mp.if")
    check(_normalize_filter_value("Confidencialidad") == "confidencialidad")


async def test_the_wire_payload_writes_every_frozenset_sorted() -> None:
    """El mismo invariante que ``snapshot.codec``, en el otro lado.

    ``_measure_to_dict`` ordena ``dimensions``, ``levels`` y ``reinforcements``
    porque los tres salen de un frozenset, cuyo orden de iteración es arbitrario
    entre procesos: sin ordenarlos, dos servidores con la misma versión del
    corpus contestarían la misma pregunta en órdenes distintos, y un cliente que
    compare respuestas vería cambios donde no los hay. Es el mismo descuido que
    ``_applicable_to_dict`` tenía en su clave de orden.

    Y en **el orden del ENS**, no en el de la grafía. Este test comparaba contra
    ``sorted(...)``, o sea que exigía el alfabético y por tanto consagraba el
    fallo: ``levels`` salía ``["alto", "basico", "medio"]`` —la escala del ENS
    del revés— y ``dimensions`` abría por "autenticidad", en el campo que más se
    lee de cada medida de cada payload. La regla ya estaba escrita en
    ``_LEVEL_SORT_ORDER`` y sólo la cumplían los refuerzos.

    Sobre el corpus real y sus 73 medidas: con una sola, un orden accidental
    podría cuadrar por casualidad.
    """
    server = build_server(SnapshotRepository.from_package_data())
    result = await server.call_tool("list_measures", {})
    # Estrechado como en test_server_integration: `call_tool` puede devolver un
    # `InputRequiredResult`, y el gate prohíbe callar a mypy con un ignore.
    if not isinstance(result, CallToolResult):
        raise TypeError(f"se esperaba un CallToolResult, llegó {type(result).__name__}")
    measures = (result.structured_content or {})["result"]

    check(len(measures) == 73, f"llegaron {len(measures)} medidas")
    alphabetical = 0
    for measure in measures:
        for field, order in (("dimensions", _DIMENSIONS), ("levels", _LEVELS)):
            keys = [order.index(value) for value in measure[field]]
            check(keys == sorted(keys), f"{measure['code']}: {field} sin ordenar: {measure[field]}")
            alphabetical += measure[field] != sorted(measure[field])
        reinforcements = [
            (_LEVELS.index(r["level"]), r["code"], r["alternative"])
            for r in measure["reinforcements"]
        ]
        check(
            reinforcements == sorted(reinforcements),
            f"{measure['code']}: refuerzos sin ordenar: {reinforcements}",
        )
    # Que el orden del ENS y el alfabético difieran en la mayoría de las medidas
    # es lo que hace que el bucle de arriba diga algo: si coincidieran, pasaría
    # igual con el orden equivocado, que es exactamente como pasaba antes.
    check(alphabetical > 73, f"sólo {alphabetical} campos distinguen los dos órdenes")


@pytest.mark.parametrize(
    ("tool", "arguments", "expected"),
    [
        # "Bajo" es la cabecera de la primera columna de niveles del Anexo II.
        ("list_measures", {"level": "bajo"}, "level='bajo'"),
        ("list_measures", {"dimension": "confidencial"}, "dimension='confidencial'"),
        # BÁSICA/MEDIA/ALTA es como nombra el RD las categorías, y como este
        # mismo servidor contesta `categoria_sistema`.
        ("requisitos_auditoria", {"level": "básica"}, "level='básica'"),
        ("declaracion_aplicabilidad", {"confidencialidad": "ALTA"}, "confidencialidad='ALTA'"),
    ],
    ids=["bajo", "dimension-a-medias", "basica", "alta-en-mayusculas"],
)
async def test_a_bad_enum_argument_says_what_would_have_worked(
    tool: str, arguments: dict[str, str], expected: str
) -> None:
    """El único sitio por el que entra input de fuera, y el de peor mensaje.

    Cada uno de estos valores es una equivocación que un cliente comete leyendo
    el vocabulario del propio ENS, y el ``ValueError`` del enum contestaba
    "'bajo' is not a valid ApplicabilityLevel": nombra una clase de Python que
    no sale en ningún esquema, docstring ni payload, y se calla las tres
    palabras que habrían funcionado.
    """
    server = build_server(SnapshotRepository.from_package_data())

    with pytest.raises(ToolError) as raised:
        await server.call_tool(tool, arguments)

    message = str(raised.value)
    # El valor va tal como lo tecleó quien llama, no plegado: a quien mandó
    # "ALTA" decirle que "'alta'" no vale le esconde la normalización.
    check(expected in message, f"no cita el argumento ni el valor: {message!r}")
    check("no es un valor válido" in message, f"mensaje inesperado: {message!r}")
    for value in ("basico, medio, alto", "confidencialidad, integridad"):
        if value.split(",")[0] in message:
            break
    else:
        raise AssertionError(f"no enumera los valores aceptados: {message!r}")
    check(
        "ApplicabilityLevel" not in message and "SecurityDimension" not in message,
        f"filtra el nombre de una clase interna: {message!r}",
    )


async def test_a_valid_enum_argument_still_works_however_it_is_typed() -> None:
    # El arreglo es del mensaje: lo que ya se aceptaba se sigue aceptando, y en
    # blanco sigue queriendo decir "sin filtro".
    server = build_server(SnapshotRepository.from_package_data())

    for arguments, expected in (
        ({}, 73),
        ({"level": "", "dimension": "   "}, 73),
        ({"level": "basico"}, 52),
        ({"level": "Básico"}, 52),
        ({"level": " ALTO "}, 73),
    ):
        result = await server.call_tool("list_measures", arguments)
        if not isinstance(result, CallToolResult):
            raise TypeError(f"se esperaba un CallToolResult, llegó {type(result).__name__}")
        measures = (result.structured_content or {})["result"]
        check(len(measures) == expected, f"{arguments} dio {len(measures)}, esperaba {expected}")


def _two_digit_reinforcement_server() -> MCPServer:
    """A server over a corpus whose cell names R2 and R10 at the same level.

    Built through the real parser and the real codec — no fixture JSON written
    by hand — so what it pins is the pipeline, not a literal.
    """
    measure = parse_measure(
        code="op.exp.1",
        title="Medida con refuerzos de dos cifras",
        dimension_column="Categoría",
        level_cells=["+ R2 + R10", "n.a.", "n.a."],
    )
    snapshot = dump(
        [parse_category("op.exp", "Explotación")], [measure], "2026-01-01T00:00:00+00:00"
    )
    return build_server(SnapshotRepository(snapshot))


async def test_the_payload_orders_a_two_digit_reinforcement_by_its_number() -> None:
    """R10 va detrás de R2, no delante.

    El corpus de hoy llega a R9, así que ordenar los códigos como texto acierta
    por casualidad y ninguna medida real distingue las dos claves — que es
    exactamente por lo que hace falta este test. El parser lee "R10" como R10 y
    nunca como R1 **a propósito** (hay test suyo), así que dejar la ordenación
    en orden de texto es la misma trampa que ``op.exp.10`` ya destapó en
    ``evidencias_auditoria``.
    """
    server = _two_digit_reinforcement_server()

    listed = await server.call_tool("list_measures", {})
    if not isinstance(listed, CallToolResult):
        raise TypeError(f"se esperaba un CallToolResult, llegó {type(listed).__name__}")
    reinforcements = (listed.structured_content or {})["result"][0]["reinforcements"]
    check(
        [r["code"] for r in reinforcements] == ["R2", "R10"],
        f"list_measures los ordenó {[r['code'] for r in reinforcements]}",
    )

    # La otra ordenación, la de la Declaración de Aplicabilidad.
    dda = await server.call_tool("declaracion_aplicabilidad", {"confidencialidad": "basico"})
    if not isinstance(dda, CallToolResult):
        raise TypeError(f"se esperaba un CallToolResult, llegó {type(dda).__name__}")
    required = (dda.structured_content or {})["measures"][0]["required_reinforcements"]
    check(
        [r["code"] for r in required] == ["R2", "R10"],
        f"la DdA los ordenó {[r['code'] for r in required]}",
    )


async def test_the_payload_order_is_decided_by_the_data_and_not_by_the_set() -> None:
    """La misma clave incompleta que el codec, en las dos ordenaciones del payload.

    El comentario que la justifica enumeraba tres campos de un ``Reinforcement``
    —nivel, código y ``alternative``— y tiene cuatro. Dos que sólo difieran en
    la redacción empatan, y el desempate lo acababa poniendo el orden de
    iteración del frozenset, que es justo el bamboleo entre ejecuciones que esa
    clave existe para impedir.

    La pareja se **busca** en vez de fijarse, por lo que explica
    ``a_reinforcement_tie_the_set_iterates_backwards``: con dos textos fijos,
    esta afirmación sólo distingue en la mitad de los arranques.
    """
    tied, first, second = a_reinforcement_tie_the_set_iterates_backwards()
    check(len(tied) == 2, "el fixture ya no produce dos miembros distintos")
    measure = SecurityMeasure(
        code="org.1",
        title="T",
        description="",
        norm_text="",
        category_code="org",
        dimensions=frozenset({SecurityDimension.CONFIDENCIALIDAD}),
        levels=frozenset({ApplicabilityLevel.BASICO}),
        reinforcements=tied,
        raw_levels=("+ [R1]", "n.a.", "n.a."),
    )
    server = build_server(
        SnapshotRepository(
            dump([parse_category("org", "Marco")], [measure], "2026-01-01T00:00:00+00:00")
        )
    )

    listed = await server.call_tool("list_measures", {})
    if not isinstance(listed, CallToolResult):
        raise TypeError(f"se esperaba un CallToolResult, llegó {type(listed).__name__}")
    served = (listed.structured_content or {})["result"][0]["reinforcements"]
    check(
        [item["text"] for item in served] == [first, second],
        f"list_measures lo ordenó {[i['text'] for i in served]}",
    )

    dda = await server.call_tool("declaracion_aplicabilidad", {"confidencialidad": "basico"})
    if not isinstance(dda, CallToolResult):
        raise TypeError(f"se esperaba un CallToolResult, llegó {type(dda).__name__}")
    required = (dda.structured_content or {})["measures"][0]["required_reinforcements"]
    check(
        [item["text"] for item in required] == [first, second],
        f"la DdA lo ordenó {[i['text'] for i in required]}",
    )
