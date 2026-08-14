"""Tests for the norms/ens.js parsers. No network, no browser.

Runs against a real capture of the live asset (``fixtures/ens.js``), not
fabricated data, so what it pins down is the shape the site actually serves.
"""

from __future__ import annotations

import re
from pathlib import Path

from ensmcp.scraping.norm_texts import parse_norm_texts
from ensmcp.snapshot.repository import SnapshotRepository
from tests.support import MINIMAL_ENS_NORM_JS, check

_ENS_NORM_JS = (Path(__file__).resolve().parent / "fixtures" / "ens.js").read_text(encoding="utf-8")
_PARSED_ENS_NORM_JS = parse_norm_texts(_ENS_NORM_JS)
_FIXTURE_MEASURE_TEXTS = _PARSED_ENS_NORM_JS.measure_texts
_FIXTURE_REINFORCEMENT_TEXTS = _PARSED_ENS_NORM_JS.reinforcement_texts


def _measure_texts(source: str) -> dict[str, str]:
    return parse_norm_texts(source).measure_texts


def _reinforcement_texts(source: str) -> dict[str, dict[str, str]]:
    return parse_norm_texts(source).reinforcement_texts


def test_parses_every_reinforcement_block_of_the_real_asset() -> None:
    # The capture holds 133 blocks, each naming exactly one (measure,
    # reinforcement) pair — the count is what catches a parser that starts
    # merging or dropping blocks after an asset rebuild.
    texts = _FIXTURE_REINFORCEMENT_TEXTS

    total = sum(len(per_measure) for per_measure in texts.values())
    check(total == 133, f"expected 133 reinforcement blocks, parsed {total}")


def test_reads_the_blocks_the_asset_wrote_with_single_quotes() -> None:
    # The asset switches quote style for blocks whose own text contains double
    # quotes. mp.s.2's R3 is the only such block in the capture, and a parser
    # that only reads double-quoted literals drops it silently — the live
    # corpus test is what caught it.
    texts = _FIXTURE_REINFORCEMENT_TEXTS

    r3 = texts["mp.s.2"]["R3"]
    check(r3.startswith("R3-Protección de las cachés"), f"heading was {r3[:40]!r}")
    check('"proxies"' in r3, "the embedded double quotes did not survive decoding")
    check("[mp.s.2.r3.1]" in r3)


def test_reinforcement_text_carries_the_wording_of_the_rd() -> None:
    texts = _FIXTURE_REINFORCEMENT_TEXTS

    op_acc_6 = texts["op.acc.6"]["R1"]
    check(op_acc_6.startswith("R1-Contraseñas"), f"heading was {op_acc_6[:40]!r}")
    check("[op.acc.6.r1.1]" in op_acc_6)
    check("Se impondrán normas de complejidad mínima" in op_acc_6)
    # CRLF is normalised, so the requirement list reads as lines everywhere.
    check("\r" not in op_acc_6)
    # The RD bullets its requirements with an en dash, written escaped here so
    # the literal stays unambiguously one character.
    check("\n\u2013 [op.acc.6.r1.2]" in op_acc_6)


def test_a_reinforcement_is_titled_after_itself_not_after_its_measure() -> None:
    # mp.s.4 se titula "Protección frente a denegación de servicio", pero su R1
    # se titula "R1-Detección y reacción": el refuerzo tiene nombre propio. Dar
    # por hecho lo contrario es lo que llevó al README a documentar como `text`
    # el título de la medida, y este es el ejemplo que el README cita.
    r1 = _FIXTURE_REINFORCEMENT_TEXTS["mp.s.4"]["R1"]

    check(r1.startswith("R1-Detección y reacción."), f"el encabezado era {r1[:40]!r}")
    check("[mp.s.4.r1.1] Se establecerá un sistema de detección" in r1, f"texto: {r1[:120]!r}")


def test_every_reinforcement_the_live_table_demands_has_wording() -> None:
    # The summary table names the reinforcements; this asset defines them. A
    # pair demanded there but missing here would ship a reinforcement with an
    # empty text, which is exactly the gap this parser exists to close.
    texts = _FIXTURE_REINFORCEMENT_TEXTS
    codes = {(measure, code) for measure, per in texts.items() for code in per}

    check(("op.acc.5", "R5") in codes)
    check(("op.exp.8", "R4") in codes)
    check(("mp.s.4", "R1") in codes)
    check(all(re.fullmatch(r"R\d+", code) for _, code in codes), "a code is not R<n>")


def test_ignores_the_norm_text_that_defines_no_reinforcement() -> None:
    # The asset is the whole RD: most of its strings are articles and annexes
    # with no [medida.rN.k] marker, and they must not become entries.
    texts = _FIXTURE_REINFORCEMENT_TEXTS

    check("Disposición adicional segunda" not in str(texts))
    check(all(per for per in texts.values()), "a measure got an empty reinforcement map")


def test_parses_a_minimal_asset_shaped_like_the_real_one() -> None:
    texts = _reinforcement_texts(MINIMAL_ENS_NORM_JS)

    # Las dos medidas cuyas filas de fixture piden refuerzo: mp.s.4 ("+ R1") y
    # op.acc.5 ("+ [R1 o R2 o R3 o R4]"). org.1 y mp.if.3 están en el asset pero
    # sin bloque de refuerzo, que es lo que este conjunto afirma que se distingue.
    check(set(texts) == {"mp.s.4", "op.acc.5"}, f"salieron: {sorted(texts)}")
    check(
        texts["mp.s.4"]["R1"] == "R1-Protección frente a denegación de servicio.\n"
        "\u2013 [mp.s.4.r1.1] Se contratará un servicio de protección frente a DoS."
    )


def test_a_marker_the_asset_typed_with_a_space_is_still_read() -> None:
    # El asset real escribe uno de sus marcadores como "[mp.s. 4.r1.2]" (punto
    # y espacio) y otro como "[mp.s 4.1]" (espacio en vez del punto). Si el
    # marcador con la errata fuese el *único* de su bloque, el bloque entero se
    # saltaría y ese texto se publicaría vacío, en silencio. Hoy no pasa —a
    # ambos les precede un marcador limpio del mismo código—, así que esto sólo
    # quita la trampa.
    spaced_dot = _reinforcement_texts('var x=["R1-T.\\r\\n[mp.s. 4.r1.1] uno"]')
    no_dot = _measure_texts('var x=["texto\\r\\n[mp.s 4.1] uno"]')

    check(set(spaced_dot) == {"mp.s.4"}, f"el código quedó como {set(spaced_dot)}")
    check(set(spaced_dot["mp.s.4"]) == {"R1"})
    check(set(no_dot) == {"mp.s.4"}, f"el código quedó como {set(no_dot)}")


def test_a_word_beside_a_marker_is_not_swallowed_into_the_code() -> None:
    # La otra cara de tolerar el espacio como separador: sin acotar el número de
    # segmentos, "[ver mp.s.4.1]" casaba como código "ver.mp.s.4" —que no es
    # ninguna medida— y, como la búsqueda para en el primer marcador, el bloque
    # se archivaba ahí y mp.s.4 se publicaba sin redacción. Acotado, ese marcador
    # no casa y la búsqueda sigue hasta el siguiente, que es el limpio.
    swallowed = _measure_texts('var x=["[ver mp.s.4.1] uno\\r\\n[mp.s.4.2] dos"]')

    check(set(swallowed) == {"mp.s.4"}, f"el código quedó como {set(swallowed)}")
    # Un código ENS tiene dos o tres segmentos; ninguno tiene cuatro.
    check(_measure_texts('var x=["[a.b.c.4.1] uno"]') == {}, "cuatro segmentos no es código")


def test_the_real_asset_parses_the_same_with_or_without_that_tolerance() -> None:
    # La tolerancia no puede cambiar lo que se sirve hoy: los bloques con
    # erratas ya se leían por su primer marcador limpio, así que el corpus
    # resultante es idéntico.
    texts = _FIXTURE_REINFORCEMENT_TEXTS

    check(sum(len(per) for per in texts.values()) == 133, "el recuento de bloques cambió")
    check(texts["mp.s.4"]["R1"].startswith("R1-Detección y reacción."))
    check(all(" " not in code for code in texts), f"un código quedó con espacios: {set(texts)}")
    check(all(" " not in code for code in _FIXTURE_MEASURE_TEXTS), "un código quedó con espacios")


def test_returns_nothing_for_a_source_with_no_markers() -> None:
    for source in ('var x=["texto sin marcadores"]', ""):
        parsed = parse_norm_texts(source)
        check(parsed.measure_texts == {})
        check(parsed.reinforcement_texts == {})


def test_every_measure_of_the_anexo_ii_has_the_wording_of_the_rd() -> None:
    # El asset define las 73, una por fila de #tablaResumen: ni falta ninguna ni
    # sobra un bloque que no sea una medida. Es el recuento lo que caza a un
    # parser que empiece a fundir bloques o a colar artículos del articulado.
    texts = _FIXTURE_MEASURE_TEXTS

    check(len(texts) == 73, f"se esperaban 73 medidas, se parsearon {len(texts)}")
    check(all(text.strip() for text in texts.values()), "una medida quedó con texto vacío")
    expected = {"org.4", "op.exp.7", "mp.s.4", "op.acc.1"}
    check(expected <= set(texts), f"faltan códigos: {sorted(expected - set(texts))}")


def test_measure_text_is_what_the_rd_demands_not_what_the_audit_asks() -> None:
    # La asimetría que este parser cierra: la `description` de org.4 es el
    # cuestionario de la 808 ("¿Se gestionan las autorizaciones...?") y esto es
    # el requisito del RD. Quien pregunta "¿qué exige org.4?" quiere lo segundo.
    org_4 = _FIXTURE_MEASURE_TEXTS["org.4"]

    check(
        org_4.startswith("Se establecerá un proceso formal de autorizaciones"),
        f"el texto empezaba por {org_4[:60]!r}",
    )
    check("[org.4.1] Utilización de instalaciones" in org_4, f"texto: {org_4[:200]!r}")
    check("?" not in org_4, "el texto de la norma no hace preguntas: eso es la 808")
    check("\r" not in org_4)


def test_a_reinforcement_block_is_not_mistaken_for_its_measure() -> None:
    # El R2 de op.exp.7 abre citando "[op.exp.7.1]", el propio requisito que
    # amplía. Sin descartar los bloques con marcador de refuerzo, esa cita haría
    # que la redacción del R2 se publicase como la redacción de la medida.
    op_exp_7 = _FIXTURE_MEASURE_TEXTS["op.exp.7"]

    check(op_exp_7.startswith("\u2013 [op.exp.7.1] Se dispondrá"), f"empezaba {op_exp_7[:40]!r}")
    check("R2 " not in op_exp_7, "se coló el bloque del refuerzo R2")
    check("[op.exp.7.r2.1]" not in op_exp_7, "se coló el bloque del refuerzo R2")


def test_measure_and_reinforcement_blocks_partition_the_markers() -> None:
    # Ningún bloque cuenta dos veces entre las dos lecturas del parser.
    measures = _FIXTURE_MEASURE_TEXTS
    reinforcements = _FIXTURE_REINFORCEMENT_TEXTS

    for code, text in measures.items():
        per_measure = reinforcements.get(code, {})
        check(text not in per_measure.values(), f"{code} publica el mismo bloque dos veces")


def test_parses_the_measure_block_of_a_minimal_asset() -> None:
    texts = _measure_texts(MINIMAL_ENS_NORM_JS)

    # El asset de fixture cubre las cuatro medidas que aparecen en filas de
    # fixture: el repositorio exige que toda fila de la tabla tenga su redacción
    # aquí, así que una tabla que nombre medidas que su propio asset no define
    # es la incoherencia que esa guarda persigue, no una simplificación.
    check(set(texts) == {"mp.s.4", "org.1", "op.acc.5", "mp.if.3"}, f"{sorted(texts)}")
    check(
        texts["mp.s.4"] == "Se establecerán medidas preventivas frente a DoS. Para ello:\n"
        "\u2013 [mp.s.4.1] Se planificará la capacidad del sistema."
    )


def test_the_first_block_of_a_measure_wins() -> None:
    # Dos bloques reclamando la misma medida serían un bloque de referencias
    # cruzadas, no una copia mejor del artículo: se queda el primero.
    texts = _measure_texts('var x=["[org.1.1] primero","[org.1.1] segundo"]')

    check(texts == {"org.1": "[org.1.1] primero"}, f"quedó {texts}")


def test_the_first_block_of_a_reinforcement_wins_too() -> None:
    # La misma regla del test de arriba, en la mitad que la incumplía: el último
    # bloque pisaba la redacción ya leída. El bloque de un refuerzo puede citar
    # el marcador de otro antes que el suyo —``op.exp.7``'s R2 abre citando
    # "[op.exp.7.1]"—, y entonces R2 se archivaba bajo R1 y borraba su texto.
    texts = _reinforcement_texts('var x=["[org.1.r1.1] primero","[org.1.r1.1] segundo"]')

    check(texts == {"org.1": {"R1": "[org.1.r1.1] primero"}}, f"quedó {texts}")


# El RD enumera, medida a medida y nivel a nivel, qué se le exige — y lo hace en
# este mismo asset, en bloques **sin** marcadores `[medida.k]`, así que ningún
# parser de producción los lee. La aplicabilidad sale de `#tablaResumen`, que es
# la fuente que este proyecto eligió a propósito: ver scripts/capture_dom.py,
# donde `medidasControl.js` resultó contradecir a la tabla en siete medidas y
# ser la tabla la correcta.
#
# Justamente por eso sirven aquí. Son una afirmación **independiente y
# normativa** de lo mismo que `parse_levels` y `parse_reinforcements` derivan de
# la tabla, y cubren las 219 celdas: las 73 medidas por sus tres niveles. Eso
# cierra un punto ciego que ningún test cubría — el de tabla.js compara lo
# scrapeado contra otra copia *de la tabla*, así que si la tabla estuviese mal
# las dos saldrían mal igual; el RD no puede salir mal igual.
#
# Dos vocabularios, que son los dos de la propia tabla: "Categoría BÁSICA/MEDIA/
# ALTA" para las medidas que la columna de dimensiones marca "Categoría", y
# "Nivel BAJO/MEDIO/ALTO" para las que van por dimensiones. La misma distinción
# que `parse_dimension_labels` modela.
_APPLICABILITY = re.compile(
    r"""(?:Categoría|Nivel)\s+(BÁSICA|BAJO|MEDIA|MEDIO|ALTA|ALTO)\s*:\s*([^"'\n]*)"""
)
_PROSE_LEVEL_INDEX = {"BÁSICA": 0, "BAJO": 0, "MEDIA": 1, "MEDIO": 1, "ALTA": 2, "ALTO": 2}
_MEASURE_IN_PROSE = re.compile(r"\b((?:org|op|mp)\.(?:[a-z]+\.)?\d+)\b")
_R_TOKEN = re.compile(r"\bR\d+", re.IGNORECASE)
_NOT_APPLICABLE = "n.a."


def _applicability_in_the_rd(js_source: str) -> dict[tuple[str, int], str]:
    """``{(medida, índice de nivel): frase}`` para las frases que nombran su medida.

    Las de "no aplica" no la nombran —es lo que significan— así que no entran, y
    su ausencia es lo que el test compara contra la celda "n.a.". Leer cada frase
    por su cuenta, en vez de agrupar tripletas, evita reconstruir los límites de
    los bloques del asset: cada frase que exige algo se identifica sola.

    El corte en la primera comilla es lo que permite eso: ninguna de estas frases
    contiene una comilla, así que la primera **es** el final de la cadena del
    asset. Sin ese corte la frase se comía el principio de la siguiente
    (``mp.if.2.","Protección del correo electrónico [mp.s.1]``) y nombraba dos
    medidas en vez de una.

    Y el código se busca también sobre la frase sin espacios, porque el sitio
    escribe "m p.com.4" y "m p.s.2" —el espacio metido *dentro* del código—. Es
    la misma errata que ``_SEPARATOR`` ya tolera en los marcadores
    ("[mp.s. 4.r1.2]", "[mp.s 4.1]"), aquí en la prosa. Sin tolerarla, esas dos
    frases parecerían no nombrar medida alguna y se leerían como "no aplica",
    que es justo lo contrario de lo que dicen.
    """
    found: dict[tuple[str, int], str] = {}
    for level, rest in _APPLICABILITY.findall(js_source.replace("\\r\\n", "\n")):
        statement = rest.strip().rstrip(".").strip()
        codes = set(_MEASURE_IN_PROSE.findall(statement)) or set(
            _MEASURE_IN_PROSE.findall(statement.replace(" ", ""))
        )
        if len(codes) == 1:
            found[(codes.pop(), _PROSE_LEVEL_INDEX[level])] = statement
    return found


def test_the_rd_prose_and_the_summary_table_agree_on_all_219_cells() -> None:
    prose = _applicability_in_the_rd(_ENS_NORM_JS)
    measures = {m.code: m for m in SnapshotRepository.from_package_data().measures}
    covered = sorted({code for code, _ in prose})

    # Los recuentos primero: sin ellos una extracción que dejase de encontrar
    # frases compararía cero pares y pasaría en verde diciendo que todo cuadra.
    check(len(covered) == 73, f"el RD enumera las 73 medidas, se hallaron {len(covered)}")
    check(len(prose) == 193, f"esperaba 193 frases que exigen algo, hay {len(prose)}")
    unpublished = sorted(set(covered) - set(measures))
    check(not unpublished, f"el RD nombra medidas que la tabla no publica: {unpublished}")

    problems: list[str] = []
    for code in covered:
        measure = measures[code]
        for index in range(len(measure.raw_levels)):
            cell = measure.raw_levels[index]
            applies = cell.strip().casefold() != _NOT_APPLICABLE
            statement = prose.get((code, index))
            # Que el RD nombre la medida en ese nivel es que se le exige ahí; que
            # no la nombre es que dijo "no aplica". La celda tiene que decir lo
            # mismo y con los mismos refuerzos.
            if (statement is not None) != applies:
                problems.append(
                    f"{code}[{index}]: el RD "
                    f"{'la exige: ' + repr(statement) if statement else 'dice no aplica'}, "
                    f"la celda dice {cell!r}"
                )
            elif statement is not None:
                # Los refuerzos, sobre la frase **con** sus espacios: "R2 o R3"
                # sin ellos sería "R2oR3", donde el segundo token ya no abre en
                # frontera de palabra y se perdería.
                in_prose = {r.group().upper() for r in _R_TOKEN.finditer(statement)}
                in_cell = {r.group().upper() for r in _R_TOKEN.finditer(cell)}
                if in_prose != in_cell:
                    problems.append(
                        f"{code}[{index}]: refuerzos del RD {sorted(in_prose)} "
                        f"vs celda {sorted(in_cell)} ({statement!r} / {cell!r})"
                    )
    check(not problems, "el RD y #tablaResumen no coinciden:\n  " + "\n  ".join(problems))


def test_a_reinforcement_block_never_becomes_its_measures_wording() -> None:
    """La guarda que salta los bloques de refuerzo, afirmada por fin.

    Un bloque de refuerzo cita marcadores de su medida —el R2 de op.exp.7 abre
    citando "[op.exp.7.1]", el requisito que extiende— así que sin saltarlo
    acabaría archivado como la redacción de la medida.

    Sobre el asset real la guarda no cambia nada, y eso es exactamente por lo que
    hacía falta este test: ``setdefault`` deja ganar al primer bloque, y en la
    captura actual el de cada medida precede a los refuerzos que la citan. O sea
    que quitar la guarda —el ``continue``, o anclar la búsqueda con ``match``—
    pasaba la suite entera sin una queja. Lo que la guarda defiende es el orden
    contrario, que la tabla de cadenas del asset no garantiza: está barajada (un
    R1 de mp.s.4, luego la aplicabilidad de op.exp.2, luego un R7 de op.mon.3).

    Así que el orden se pone del revés a mano: el refuerzo primero. Con guarda,
    la medida se queda con su artículo; sin ella, se queda con el refuerzo.
    """
    reinforcement_first = (
        'var x=["R2-Cadena de custodia.\\r\\n'
        '\u2013 [op.exp.7.r2.1] Se ampliará lo exigido en [op.exp.7.1].\\r\\n",'
        '"\u2013 [op.exp.7.1] Se dispondrá de un proceso integral de incidentes.\\r\\n"]'
    )

    parsed = parse_norm_texts(reinforcement_first)
    texts = parsed.measure_texts

    check(set(texts) == {"op.exp.7"}, f"se archivaron {sorted(texts)}")
    check(
        texts["op.exp.7"].startswith("\u2013 [op.exp.7.1] Se dispondrá"),
        f"op.exp.7 se quedó con el refuerzo: {texts['op.exp.7'][:60]!r}",
    )
    check("R2-" not in texts["op.exp.7"], "el encabezado del refuerzo se coló en la medida")
    # Y el refuerzo sigue leyéndose como refuerzo, no se pierde por el camino.
    check(set(parsed.reinforcement_texts["op.exp.7"]) == {"R2"})


def test_decodes_the_escapes_that_only_javascript_has() -> None:
    """El asset es JavaScript, y ``json.loads`` no lee JavaScript.

    Cada uno de estos escapes es legal en JS y **ninguno** lo es en JSON, así
    que con el decodificador anterior cada uno tumbaba el asset entero: un solo
    bloque malo abortaba el parseo de los 461 literales y las 73 medidas se
    quedaban sin redacción, con un error que sólo sabía decir en qué columna de
    qué JSON estaba.

    ``\\'`` es el que estaba esperando su turno: un literal entrecomillado con
    simples que lleve un apóstrofo **tiene** que escaparlo, y este asset ya
    cambia a comillas simples para los bloques cuyo texto lleva comillas dobles.
    """
    cases = {
        r"\'": "'",
        r"\x41": "A",
        r"\u2013": "\u2013",
        r"\v": "\v",
        r"\0": "\0",
        "\t": "\t",
        "\\\\": "\\",
    }
    for escape, expected in cases.items():
        source = f'var x=["[org.1.1] a{escape}b"]'

        texts = _measure_texts(source)

        check(set(texts) == {"org.1"}, f"{escape!r} se llevó por delante el asset: {texts}")
        check(
            texts["org.1"] == f"[org.1.1] a{expected}b",
            f"{escape!r} se decodificó como {texts['org.1']!r}",
        )


def test_decodes_a_single_quoted_block_that_escapes_its_double_quotes() -> None:
    # ``_STRING_LITERAL`` admite explícitamente un ``\"`` dentro de un literal
    # de comillas simples —su alternativa es el par "sin comilla, o cualquier
    # carácter escapado"—, pero el decodificador escapaba **todas** las comillas
    # dobles para armar su JSON, incluidas las que ya venían escapadas. Así que
    # esta forma, que el propio matcher acepta, salía como JSON inválido.
    source = "var x=['[mp.s.2.1] los \\\"proxies\\\" y las cachés']"

    texts = _measure_texts(source)

    check(set(texts) == {"mp.s.2"}, f"se archivaron {sorted(texts)}")
    check(
        texts["mp.s.2"] == '[mp.s.2.1] los "proxies" y las cachés',
        f"salió {texts['mp.s.2']!r}",
    )


def test_reads_a_surrogate_pair_as_the_character_it_is() -> None:
    # Decodificar los dos suplentes por separado deja una cadena que ni siquiera
    # se puede codificar a UTF-8, así que el snapshot reventaría al volcarse —
    # peor que no leer el bloque. ``json.loads`` los juntaba y esto tiene que
    # seguir haciéndolo.
    # Escapado, que es como un minificador que emite ASCII escribe cualquier
    # carácter fuera del BMP.
    texts = _measure_texts('var x=["[org.1.1] \\uD83D\\uDE00"]')

    check(texts["org.1"] == "[org.1.1] \U0001f600", f"salió {texts['org.1']!r}")
    # Lo que rompía de verdad: dos suplentes sueltos ni se pueden codificar.
    texts["org.1"].encode("utf-8")


def test_an_escaped_backslash_does_not_start_the_escape_that_follows_it() -> None:
    # La propiedad más fácil de romper del decodificador, y la que un
    # ``.replace()`` encadenado —la "simplificación" evidente— rompe en
    # silencio: la barra escapada se consume entera, así que la "n" que viene
    # detrás es una letra y no un salto de línea. Lo mismo con "\\\\u0041", que
    # no es una A.
    source = 'var x=["[org.1.1] a\\\\nb c\\\\u0041d"]'

    texts = _measure_texts(source)

    check(texts["org.1"] == "[org.1.1] a\\nb c\\u0041d", f"salió {texts['org.1']!r}")
    check("\n" not in texts["org.1"], "la barra escapada se comió como salto de línea")


def test_a_run_of_carriage_returns_leaves_no_crlf_behind() -> None:
    # Un solo `replace("\r\n", "\n")` no puede con una racha: en "\r\r\n" se
    # lleva el par de la derecha, y el `\r` que quedaba a la izquierda se junta
    # con el `\n` recién puesto — el reemplazo ya ha pasado por ahí, así que
    # sale un CRLF intacto de una función cuyo docstring promete que no queda
    # ninguno. El asset de hoy empareja cada `\r` con su `\n`, así que esto sólo
    # quita la trampa.
    texts = _measure_texts('var x=["[org.1.1] uno\\r\\r\\ndos\\rtres"]')

    body = texts["org.1"]
    check("\r" not in body, f"quedó un retorno de carro: {body!r}")
    check(body == "[org.1.1] uno\n\ndos\ntres", f"salió {body!r}")
