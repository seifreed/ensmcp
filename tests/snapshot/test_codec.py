"""Tests for the snapshot JSON codec. Pure: no browser, no network, no disk."""

from __future__ import annotations

import json

import pytest

from ensmcp.domain.models import (
    ApplicabilityLevel,
    AuditRequirement,
    CategoryGroup,
    SecurityDimension,
    SecurityMeasure,
)
from ensmcp.scraping.parsers import parse_category, parse_measure
from ensmcp.snapshot.codec import SCHEMA_VERSION, dump, load
from ensmcp.snapshot.repository import SnapshotRepository, default_snapshot_text
from tests import support
from tests.support import a_reinforcement_tie_the_set_iterates_backwards, check, set_json_value

_CAPTURED_AT = "2026-08-06T17:29:08+00:00"

# Real rows, including op.acc.5 — the one measure mixing a choice group with a
# required reinforcement, which is what a lossy codec would flatten.
_CATEGORIES = [parse_category("mp.s", "Protección de los servicios")]
_REQUIREMENTS = (
    AuditRequirement(
        position=0,
        code="1.1",
        level=ApplicabilityLevel.ALTO,
        essential=True,
        question="¿Se ha contratado protección frente a DoS?",
        note="NOTA: vale un servicio del proveedor de tránsito.",
    ),
    # Same printed code, different position: the shape a codec keyed by code
    # would silently collapse.
    AuditRequirement(
        position=1,
        code="1.1",
        level=ApplicabilityLevel.ALTO,
        essential=False,
        question="¿Se revisa periódicamente?",
    ),
)
_MEASURES = [
    parse_measure(
        code="mp.s.4",
        title="Protección frente a denegación de servicio",
        dimension_column="D",
        level_cells=["n.a.", "aplica", "+ R1"],
        description="Descripción de la medida.",
        norm_text="Se establecerán medidas preventivas frente a DoS.",
        reinforcement_texts={"R1": "R1-Protección frente a DoS."},
        audit_requirements=_REQUIREMENTS,
    ),
    parse_measure(
        code="op.acc.5",
        title="Mecanismo de autenticación (usuarios externos)",
        dimension_column="A T",
        level_cells=["+ [R1 o R2 o R3 o R4]", "+ [R2 o R3 o R4] + R5", "+ [R2 o R3 o R4] + R5"],
        reinforcement_texts={"R5": "R5-Doble factor."},
    ),
]


_REAL_MEASURES = SnapshotRepository.from_package_data().measures


def _measure_payload(**overrides: object) -> dict[str, object]:
    """Un payload de medida con la forma completa, con un campo estropeado.

    Los casos "short-*" de abajo prueban lo que falta; éste prueba lo que está
    pero mal escrito, que necesita el resto del payload intacto para llegar
    hasta él.
    """
    payload = json.loads(dump(_CATEGORIES, _MEASURES, _CAPTURED_AT))["measures"][0]
    return {**payload, **overrides}


def test_round_trip_preserves_the_corpus_exactly() -> None:
    categories, measures, captured_at = load(dump(_CATEGORIES, _MEASURES, _CAPTURED_AT))

    check(categories == _CATEGORIES)
    check(measures == _MEASURES, "a measure did not survive the round trip")
    check(captured_at == _CAPTURED_AT)


def test_round_trip_keeps_the_choice_semantics() -> None:
    # The distinction that matters for a Declaración de Aplicabilidad: R5 is
    # required at medio, R2/R3/R4 are alternatives to pick one from.
    _, measures, _ = load(dump(_CATEGORIES, _MEASURES, _CAPTURED_AT))

    op_acc_5 = next(measure for measure in measures if measure.code == "op.acc.5")
    medio = {
        reinforcement.code: reinforcement.alternative
        for reinforcement in op_acc_5.reinforcements
        if reinforcement.level is ApplicabilityLevel.MEDIO
    }
    check(medio == {"R2": True, "R3": True, "R4": True, "R5": False}, f"medio was {medio}")
    texts = {r.code: r.text for r in op_acc_5.reinforcements}
    check(texts["R5"] == "R5-Doble factor.")


def test_round_trip_keeps_dimensions_levels_and_verbatim_cells() -> None:
    _, measures, _ = load(dump(_CATEGORIES, _MEASURES, _CAPTURED_AT))

    mp_s_4 = next(measure for measure in measures if measure.code == "mp.s.4")
    check(mp_s_4.dimensions == frozenset({SecurityDimension.DISPONIBILIDAD}))
    check(mp_s_4.levels == frozenset({ApplicabilityLevel.MEDIO, ApplicabilityLevel.ALTO}))
    check(mp_s_4.raw_levels == ("n.a.", "aplica", "+ R1"))
    check(mp_s_4.description == "Descripción de la medida.")
    # Los dos textos de una medida viajan por separado y no se confunden: el
    # cuestionario de la 808 y lo que exige el RD.
    check(mp_s_4.norm_text == "Se establecerán medidas preventivas frente a DoS.")
    op_acc_5 = next(measure for measure in measures if measure.code == "op.acc.5")
    check(op_acc_5.norm_text == "", "una medida sin redacción no puede inventarse una")
    check(_CATEGORIES[0].group is CategoryGroup.MEDIDAS_PROTECCION)


def test_round_trip_keeps_the_audit_questionnaire() -> None:
    # Order and position are the identity here, because the printed code
    # repeats: a codec that reordered or deduplicated would lose questions an
    # auditor is going to ask.
    _, measures, _ = load(dump(_CATEGORIES, _MEASURES, _CAPTURED_AT))

    mp_s_4 = next(measure for measure in measures if measure.code == "mp.s.4")
    check(mp_s_4.audit_requirements == _REQUIREMENTS, "el cuestionario no sobrevivió igual")
    check([r.position for r in mp_s_4.audit_requirements] == [0, 1])
    check(mp_s_4.audit_requirements[0].note.startswith("NOTA:"))
    check(mp_s_4.audit_requirements[1].note == "")


def test_dumping_the_same_corpus_twice_is_byte_identical() -> None:
    # The freshness check compares serialised text, so an unstable dump — a
    # frozenset iterating in a different order — would report the live site as
    # "changed" on every startup and never converge.
    first = dump(_CATEGORIES, _MEASURES, _CAPTURED_AT)
    second = dump(list(_CATEGORIES), list(_MEASURES), _CAPTURED_AT)

    check(first == second)
    check(first.endswith("\n"), "the file must end in a newline")


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("categories", 0, "code"), 7),
        (("categories", 0, "name"), 7),
        (("categories", 0, "group"), 7),
        (("measures", 0, "code"), 7),
        (("measures", 0, "title"), 7),
        (("measures", 0, "description"), 7),
        (("measures", 0, "norm_text"), 7),
        (("measures", 0, "category_code"), 7),
        (("measures", 0, "dimensions", 0), 7),
        (("measures", 0, "levels", 0), 7),
        (("measures", 0, "reinforcements", 0, "code"), 7),
        (("measures", 0, "reinforcements", 0, "level"), 7),
        (("measures", 0, "reinforcements", 0, "alternative"), "false"),
        (("measures", 0, "reinforcements", 0, "text"), 7),
        (("measures", 0, "raw_levels", 0), 7),
        (("measures", 0, "audit_requirements", 0, "position"), True),
        (("measures", 0, "audit_requirements", 0, "code"), 7),
        (("measures", 0, "audit_requirements", 0, "level"), 7),
        (("measures", 0, "audit_requirements", 0, "essential"), "false"),
        (("measures", 0, "audit_requirements", 0, "question"), 7),
        (("measures", 0, "audit_requirements", 0, "note"), 7),
    ],
)
def test_load_refuses_wrong_scalar_types(path: tuple[str | int, ...], value: object) -> None:
    document = json.loads(dump(_CATEGORIES, _MEASURES, _CAPTURED_AT))
    set_json_value(document, path, value)

    with pytest.raises(ValueError, match=r"snapshot declares schema version .* shape"):
        load(json.dumps(document))


@pytest.mark.parametrize(
    ("collection", "identity"),
    [("categories", "category code"), ("measures", "measure code")],
)
def test_load_refuses_duplicate_root_identifiers(collection: str, identity: str) -> None:
    document = json.loads(dump(_CATEGORIES, _MEASURES, _CAPTURED_AT))
    document[collection].append(document[collection][0])

    with pytest.raises(ValueError, match=r"snapshot declares schema version .* shape") as excinfo:
        load(json.dumps(document))

    check(f"duplicate {identity}" in str(excinfo.value))


def test_load_refuses_duplicate_audit_requirement_positions_within_a_measure() -> None:
    document = json.loads(dump(_CATEGORIES, _MEASURES, _CAPTURED_AT))
    requirements = document["measures"][0]["audit_requirements"]
    requirements.append(requirements[0])

    with pytest.raises(ValueError, match=r"snapshot declares schema version .* shape") as excinfo:
        load(json.dumps(document))

    check("duplicate audit requirement position" in str(excinfo.value))
    check("mp.s.4" in str(excinfo.value))


@pytest.mark.parametrize("field", ["dimensions", "levels", "reinforcements"])
def test_load_refuses_a_duplicate_set_member(field: str) -> None:
    document = json.loads(dump(_CATEGORIES, _MEASURES, _CAPTURED_AT))
    values = document["measures"][0][field]
    values.append(values[0])

    with pytest.raises(ValueError, match=r"snapshot declares schema version .* shape") as excinfo:
        load(json.dumps(document))

    check("duplicate" in str(excinfo.value))


def test_load_refuses_nonconsecutive_audit_requirement_positions() -> None:
    document = json.loads(dump(_CATEGORIES, _MEASURES, _CAPTURED_AT))
    document["measures"][0]["audit_requirements"][1]["position"] = 2

    with pytest.raises(ValueError, match=r"positions.*not consecutive from 0"):
        load(json.dumps(document))


def test_the_schema_version_moved_when_the_shape_did() -> None:
    # La versión 2 es la de antes de que la medida llevase la redacción del RD.
    # Un fichero de entonces carga sin quejarse si nadie sube el número, y sirve
    # 73 medidas con `norm_text` ausente — que es justo lo que la versión existe
    # para impedir. Si esto falla tras cambiar la forma, el número no se subió.
    check(SCHEMA_VERSION == 3, f"la versión declarada es {SCHEMA_VERSION}")

    v2 = json.loads(dump(_CATEGORIES, _MEASURES, _CAPTURED_AT))
    v2["schema_version"] = 2
    for measure in v2["measures"]:
        del measure["norm_text"]

    with pytest.raises(ValueError, match="snapshot schema version 2"):
        load(json.dumps(v2))


@pytest.mark.parametrize(
    ("document", "expected"),
    [
        ({"schema_version": SCHEMA_VERSION}, "KeyError"),
        ({"schema_version": SCHEMA_VERSION, "categories": [], "measures": []}, "captured_at"),
        (
            {
                "schema_version": SCHEMA_VERSION,
                "captured_at": _CAPTURED_AT,
                "categories": [{"code": "mp.s"}],
                "measures": [],
            },
            "name",
        ),
        (
            {
                "schema_version": SCHEMA_VERSION,
                "captured_at": _CAPTURED_AT,
                "categories": [],
                "measures": [{"code": "mp.s.4"}],
            },
            "title",
        ),
        (
            {
                "schema_version": SCHEMA_VERSION,
                "captured_at": _CAPTURED_AT,
                "categories": "no es una lista",
                "measures": [],
            },
            "TypeError",
        ),
        (
            {
                "schema_version": SCHEMA_VERSION,
                "captured_at": _CAPTURED_AT,
                "categories": [{"code": "mp.s", "name": "Servicios", "group": "xx"}],
                "measures": [],
            },
            "CategoryGroup",
        ),
        (
            {
                "schema_version": SCHEMA_VERSION,
                "captured_at": _CAPTURED_AT,
                "categories": [],
                "measures": [_measure_payload(raw_levels="aplica")],
            },
            "raw_levels is a str",
        ),
        (
            {
                "schema_version": SCHEMA_VERSION,
                "captured_at": _CAPTURED_AT,
                "categories": [],
                # Una lista, así que la guarda de tipo la deja pasar — y con seis
                # celdas llega al payload, donde `_measure_to_dict` las empareja
                # con basico/medio/alto y se queda con las tres primeras. Las
                # otras tres desaparecen sin una palabra: el mismo desenlace que
                # la cadena de arriba, por la puerta que quedaba abierta.
                "measures": [
                    _measure_payload(raw_levels=["a", "p", "l", "i", "c", "a"]),
                ],
            },
            "raw_levels has 6 cell",
        ),
    ],
    ids=[
        "nothing-but-the-version",
        "no-captured-at",
        "short-category",
        "short-measure",
        "wrong-type",
        "unknown-enum-value",
        "raw-levels-as-a-string",
        "raw-levels-with-the-wrong-cell-count",
    ],
)
def test_load_refuses_a_file_that_declares_the_version_without_the_shape(
    document: dict[str, object], expected: str
) -> None:
    # El check de versión no puede cazar esto: un fichero truncado por una
    # escritura a medias, o estropeado por un merge, declara la versión correcta
    # y no tiene la forma. Antes reventaba con un `KeyError: 'title'` pelado,
    # que no dice ni qué fichero es ni que el fichero sea el problema.
    support.check_invalid_json_shape(load, document, expected)


def test_the_file_writes_every_frozenset_sorted() -> None:
    """La promesa que el docstring del módulo hace de sí mismo, afirmada.

    "Los frozensets se escriben ordenados, porque dos volcados del mismo corpus
    tienen que dar texto idéntico byte a byte, o la comprobación de '¿ha
    cambiado la web?' saltaría por nada más que el orden de un diccionario."
    Eso no es cosmética: `RefreshingRepository` compara el texto de `dump` para
    decidir si la web difiere del fichero, así que un orden inestable haría que
    esa comprobación gritase "updated" en cada arranque del proceso.

    Y `test_dumping_the_same_corpus_twice_is_byte_identical` **no** puede
    cazarlo: los dos volcados corren en el mismo proceso, donde el orden de
    iteración de un frozenset es estable aunque no esté ordenado. Sólo mirando
    que las listas del fichero salgan ordenadas se afirma el invariante sin
    depender de que el proceso vecino tenga la misma semilla de hash.
    """
    document = json.loads(dump(_CATEGORIES, _REAL_MEASURES, _CAPTURED_AT))

    for measure in document["measures"]:
        for field in ("dimensions", "levels"):
            check(
                measure[field] == sorted(measure[field]),
                f"{measure['code']}: {field} salió sin ordenar: {measure[field]}",
            )
        keys = [(r["level"], r["code"], r["alternative"]) for r in measure["reinforcements"]]
        check(keys == sorted(keys), f"{measure['code']}: refuerzos sin ordenar: {keys}")


def test_load_refuses_a_captured_at_that_is_not_a_string() -> None:
    # La tercera forma que la guarda no ve sola, después de las dos que cubre
    # ``_texts``: un número aquí no es un error de Python, así que entraba y
    # salía tal cual —anotado ``str`` y llevando lo que dijera el fichero—. Y
    # quien lo sirve es ``snapshot_status``, así que un fichero editado a mano
    # reventaba en la *primera llamada a una tool* en vez de al arrancar, que es
    # lo único que ``SnapshotRepository`` promete que no puede pasar.
    document = json.loads(dump([], [], "2026-01-01T00:00:00+00:00"))
    document["captured_at"] = 20260101

    with pytest.raises(ValueError, match="captured_at is a int, expected a string"):
        load(json.dumps(document))


def test_dump_orders_a_two_digit_reinforcement_by_its_number() -> None:
    # La misma clave que usan el servidor y guia.codec. El fichero sólo necesita
    # ser determinista, pero se lee y se diffea a mano, y "R10" antes que "R2"
    # es la trampa que op.exp.10 ya destapó en el orden de las evidencias.
    measure = parse_measure(
        code="op.exp.1",
        title="T",
        dimension_column="Categoría",
        level_cells=["+ R2 + R10", "n.a.", "n.a."],
    )

    document = json.loads(dump([parse_category("op.exp", "Explotación")], [measure], _CAPTURED_AT))

    codes = [item["code"] for item in document["measures"][0]["reinforcements"]]
    check(codes == ["R2", "R10"], f"el fichero los escribió {codes}")


def test_the_shipped_file_is_exactly_what_dump_writes() -> None:
    """El fichero commiteado tiene que ser, byte a byte, la salida de ``dump``.

    Es la premisa de la que cuelga toda la comprobación de frescura: el chequeo
    en vivo decide "la web difiere" comparando ``dump(scrape)`` contra el texto
    del fichero, así que cualquier deriva entre el generador y el artefacto
    —perder ``sort_keys``, cambiar el ``indent``, el ``ensure_ascii`` o el salto
    final— haría saltar ese aviso sin que el ENS Navegable hubiera cambiado
    nada, y mandaría a regenerar un fichero que ya estaba bien.

    Los tests de round-trip que ya había comparan ``dump`` contra ``dump``, así
    que se mueven los dos lados a la vez y no ven ninguno de esos cambios: se
    podía quitar ``sort_keys=True`` con la suite entera en verde. Éste compara
    contra el fichero de verdad, que es el único lado que no se mueve.
    """
    shipped = default_snapshot_text()
    categories, measures, captured_at = load(shipped)

    check(dump(categories, measures, captured_at) == shipped, "el fichero no es la salida de dump")


def test_dump_is_a_function_of_the_corpus_and_not_of_set_iteration_order() -> None:
    """Dos volcados del mismo corpus tienen que salir byte a byte iguales.

    Es la premisa del chequeo de frescura, y la clave de orden no la cumplía:
    enumeraba tres campos de un ``Reinforcement`` —nivel, código y
    ``alternative``— y tiene cuatro. Dos refuerzos que coincidan en los tres
    pero difieran en la redacción son dos miembros distintos del frozenset con
    la **misma** clave, así que salían en el orden de iteración del conjunto,
    que es arbitrario. Medido sobre corpus generados: el mismo corpus, volcado
    dos veces, con las dos redacciones intercambiadas.

    El corpus real no tiene ese empate —``parse_reinforcements`` busca la
    redacción por código, así que un código dado siempre trae la misma— pero el
    modelo lo admite, y un ``anexo_ii.json`` editado a mano lo produce. La
    consecuencia era el aviso que este orden existe para evitar:
    ``RefreshingRepository`` decide "la web difiere" comparando este texto
    contra el del fichero, así que un empate mandaba a regenerar sin que el ENS
    Navegable hubiera cambiado nada.
    """
    tied, first_text, second_text = a_reinforcement_tie_the_set_iterates_backwards()
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
        raw_levels=("aplica", "n.a.", "n.a."),
    )
    categories = [parse_category("org", "Marco organizativo")]

    once = dump(categories, [measure], _CAPTURED_AT)
    reloaded_categories, reloaded_measures, reloaded_at = load(once)

    check(reloaded_measures == [measure], "el round-trip no conserva el corpus")
    check(
        dump(reloaded_categories, reloaded_measures, reloaded_at) == once,
        "el mismo corpus se volcó de dos maneras distintas",
    )
    # Lo que de verdad cierra el agujero: el orden lo decide **el dato**, no el
    # conjunto. Comprobar sólo que dos volcados coinciden no bastaría, porque
    # con la clave incompleta también coinciden — el orden es arbitrario pero
    # estable dentro de un proceso. Afirmar el orden exacto sí, y con una pareja
    # que el conjunto itera al revés lo afirma sea cual sea la semilla de hash:
    # sin ``text`` en la clave, ``sorted`` es estable y deja el orden del
    # conjunto, o sea el contrario. Con la pareja fija que había aquí eso sólo
    # era cierto en la mitad de los arranques — ver
    # ``a_reinforcement_tie_the_set_iterates_backwards``.
    written = json.loads(once)["measures"][0]["reinforcements"]
    check(
        [item["text"] for item in written] == [first_text, second_text],
        f"el orden lo puso el conjunto y no el dato: {[i['text'] for i in written]}",
    )
