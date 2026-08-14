"""Pure parsers for the RD 311/2022 wording shipped in norms/ens.js.

That asset is the Real Decreto itself, minified and name-mangled, and **two**
different things are read out of it in one pass over its JS string literals:

- ``ParsedNormTexts.measure_texts`` — what the RD demands of each of the 73 measures,
  which is what ``SecurityMeasure.norm_text`` holds.
- ``ParsedNormTexts.reinforcement_texts`` — the same for each refuerzo, which is what
  ``Reinforcement.text`` holds.

Neither is in ``#tablaResumen`` (which only *names* what applies) nor in
``requisitos.js`` (which is the CCN-STIC 808 questionnaire — what an auditor
*asks*, not what the norm *requires*). The string table holds one entry per
block::

    "R1-Contraseñas.\\r\\n\\u2013 [op.acc.6.r1.1] Se empleará una contraseña
    como mecanismo de autenticación cuando el acceso se realiza desde zonas
    controladas...\\r\\n\\u2013 [op.acc.6.r1.2] Se impondrán normas de..."

The mangling is irrelevant here: this reads the string *contents*, never the
generated identifiers, so a rebuild that renames every symbol changes nothing.
What identifies a block is the norm's own requirement numbering — ``[medida.k]``
for a measure, ``[medida.rN.k]`` for a refuerzo — and not the heading, which
spells its dash three different ways (bare hyphen, spaced hyphen, en dash).
Verified against a real capture: 73 measure blocks (one per row of
``#tablaResumen``, none missing, none spare) and 133 refuerzo blocks, each
carrying markers for exactly one measure, covering every one of the 92
(measure, refuerzo) pairs the live table demands.

The parser is pure: raw JS source in, both mappings keyed by measure code out.
No Patchright, no network.
"""

from __future__ import annotations

import functools
import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ParsedNormTexts:
    """Both readings of one ``norms/ens.js`` parse."""

    measure_texts: dict[str, str]
    reinforcement_texts: dict[str, dict[str, str]]


# The asset's string table, read as literals rather than evaluated. Both quote
# styles matter: the asset switches to single quotes for the blocks whose text
# contains double quotes, and mp.s.2's R3 ("proxies", "cachés") is exactly such
# a block — reading only double-quoted literals silently dropped it. Each
# alternative is the standard "no quote, or any escaped character" pair, so an
# escaped quote inside a block cannot end the match early.
_STRING_LITERAL = re.compile(r'"((?:[^"\\]|\\.)*)"' + r"|'((?:[^'\\]|\\.)*)'")

# The asset does not always spell a code cleanly. The real capture writes one
# marker "[mp.s. 4.r1.2]" (a dot *and* a space) and another "[mp.s 4.1]" (a
# space *instead of* the dot), so any run of dots and spaces is accepted as the
# separator and normalised back to a single dot by ``_normalise_code`` — or the
# code would never join against the "mp.s.4" the summary table uses. Without
# that tolerance a block whose markers *all* carried the typo would match
# nothing and be skipped entirely, which means a measure or refuerzo shipped
# with no wording at all, silently. Neither block in the current capture is in
# that position (both are preceded by a clean marker for the same code), so this
# changes nothing today and only removes the trap.
#
# The segment count is what keeps that tolerance from becoming a trap of its own.
# Every ENS code is two or three segments ("org.1", "op.acc.5", "mp.info.6") and
# never more, so the letter part repeats **at most once**. Left unbounded, a
# space-separated word next to a marker would be swallowed into the code:
# "[ver mp.s.4.1]" matched as "ver.mp.s.4", a code no measure has, and — because
# the search stops at the first match — the block would be filed under it and the
# real measure would ship with no wording. Bounded, that marker matches nothing
# and the search moves on to the next one in the block, which is the clean one.
_SEPARATOR = r"[.\s]+"
_MEASURE_CODE = rf"[a-z]+(?:{_SEPARATOR}[a-z]+)?{_SEPARATOR}\d+"

# "[org.4.2]" — measure org.4, requirement 2 of the RD's own numbering.
_MEASURE_MARKER = re.compile(rf"\[({_MEASURE_CODE}){_SEPARATOR}(\d+)\]")

# "[op.acc.6.r1.2]" — measure op.acc.6, refuerzo R1, requirement 2. Cannot be
# confused with the marker above: that one ends in digits, this one has the
# "r<n>" segment in between.
_REINFORCEMENT_MARKER = re.compile(rf"\[({_MEASURE_CODE}){_SEPARATOR}r(\d+)\.\d+\]")


def _normalise_code(raw: str) -> str:
    """Collapse a marker's separator runs back into single dots."""
    return re.sub(_SEPARATOR, ".", raw)


# Un escape de JavaScript. El par suplente va el primero a propósito: leer
# "\\uD83D\\uDE00" por separado deja dos suplentes sueltos, y una cadena con
# suplentes sueltos ni siquiera se puede codificar a UTF-8 — el snapshot
# reventaría al volcarse, que es peor que no leer el bloque.
#
# Cualquier otro carácter tras la barra se representa a sí mismo, que es
# exactamente la regla de JS, y es lo que hace que "\\'" y "\\\"" salgan bien.
# No hay rama para la continuación de línea porque no se puede llegar a ella:
# ``_STRING_LITERAL`` no casa un literal con un salto de línea dentro, así que
# uno así se salta entero — y entonces la medida se queda sin redacción, que es
# justo lo que ``NavegableRepository`` rechaza con un mensaje que la nombra.
_ESCAPE = re.compile(
    r"\\u([dD][89abAB][0-9a-fA-F]{2})\\u([dD][c-fC-F][0-9a-fA-F]{2})"
    r"|\\u([0-9a-fA-F]{4})"
    r"|\\x([0-9a-fA-F]{2})"
    r"|\\(.)"
)

# Los escapes de una sola letra que JS traduce a un carácter de control.
_LETTER_ESCAPES = {"b": "\b", "f": "\f", "n": "\n", "r": "\r", "t": "\t", "v": "\v", "0": "\0"}


def _unescape(match: re.Match[str]) -> str:
    """Un escape de JS, ya como el texto que representa."""
    high, low, quad, hexa, letter = match.groups()
    if high is not None:
        return chr(0x10000 + (int(high, 16) - 0xD800) * 0x400 + int(low, 16) - 0xDC00)
    for digits in (quad, hexa):
        if digits is not None:
            return chr(int(digits, 16))
    return _LETTER_ESCAPES.get(letter, letter)


def _decode(literal: str) -> str:
    """Decode one JS string literal's escapes into real text.

    ``json.loads`` used to do this, and JSON is not JavaScript. The asset's
    whole escape vocabulary is ``\\r`` and ``\\n`` today, which the two spell
    identically — but the moment a block uses one of the escapes only JS has,
    ``json.loads`` refuses the string and **every** measure loses its wording,
    because one bad block aborts the parse of all 461 literals. And it says so
    as a JSON column number, which names neither the asset nor the block.

    ``\\'`` is the one that was waiting to happen: a single-quoted literal that
    contains an apostrophe *has* to escape it, and this asset already switches
    to single quotes for the blocks whose text carries double quotes. ``\\xHH``,
    ``\\v``, ``\\0`` and a raw tab were the same trap.

    The other half was silent rather than loud: the old code escaped **every**
    double quote of a single-quoted literal to build its JSON, including the
    ones that were already escaped, so ``'dijo \\"hola\\"'`` — a shape
    ``_STRING_LITERAL`` explicitly admits — turned into malformed JSON.

    CRLF is normalised to LF so the requirement list reads as lines on every
    platform — y el ``\\r`` suelto también, que es la mitad que faltaba. Un solo
    ``replace("\\r\\n", "\\n")`` no puede con una racha: ``"\\r\\r\\n"`` deja un
    ``"\\r\\n"`` detrás, porque lo que quedó a la izquierda se junta con el
    ``\\n`` recién puesto y el reemplazo ya ha pasado por ahí. El asset de hoy
    empareja cada ``\\r`` con su ``\\n`` (1547 de cada uno), así que esto no
    cambia nada y sólo hace cierto lo que la línea de arriba promete.
    """
    decoded = _ESCAPE.sub(_unescape, literal)
    return decoded.replace("\r\n", "\n").replace("\r", "\n").strip()


def _blocks(js_source: str) -> Iterator[tuple[str, Callable[[], str]]]:
    """Every string literal of the asset, as ``(raw literal, decode it)``.

    Markers are matched on the raw literal and the text is only decoded once a
    parser has decided it wants the block, which is why the decoding arrives as
    a callable: most of the norm's strings are articles and annexes that neither
    parser keeps, and escapes they contain would be decoded for nothing.
    """
    for match in _STRING_LITERAL.finditer(js_source):
        # ``is not None`` y no un truthy: la cadena vacía es un literal válido.
        # El estilo de comillas ya no hace falta más allá de esto — ``_decode``
        # lee los escapes de JS, así que una comilla doble suelta dentro de un
        # literal entrecomillado con simples se lee tal cual.
        literal = match.group(1) if match.group(1) is not None else match.group(2)
        yield literal, functools.partial(_decode, literal)


def parse_norm_texts(js_source: str) -> ParsedNormTexts:
    """Parse measure and reinforcement wording in one literal traversal.

    This is the answer to "what does org.4 require?" — a *requirement* ("Se
    establecerá un proceso formal de autorizaciones que cubra...") rather than
    the audit *questions* about it that ``requisitos.js`` carries.

    A refuerzo block is not a measure block even though it quotes measure
    markers: op.exp.7's R2 opens by citing "[op.exp.7.1]", the very requirement
    it extends. Blocks carrying a ``[medida.rN.k]`` marker therefore belong to
    the reinforcement mapping and are skipped here — without that, R2's
    wording would land on op.exp.7 itself and overwrite the actual article.

    The first marker decides which measure a block is, as in
    the reinforcement mapping, and the first block wins if two ever claimed
    the same measure: the capture has exactly one per measure, so a second would
    be a cross-reference block, not a better copy of the article.
    Grouped by measure rather than returned flat because that is how the
    repository consumes it: one lookup per measure row, no rescan of the whole
    corpus per row.

    A block whose markers name more than one measure would be ambiguous, so
    the first marker decides — it is the one the heading belongs to, and the
    captured asset has no such block anyway. Blocks with no marker at all are
    the rest of the norm's text (articles, annexes) and are skipped.

    And the first *block* wins if two ever claimed the same refuerzo, exactly as
    in the measure mapping — this was the half that did not, and it failed
    the other way round: the last block overwrote the wording already read.
    The measure half above names the shape that produces such a
    pair, because this asset contains it: op.exp.7's R2 opens by citing
    "[op.exp.7.1]", so a refuerzo block quoting a *sibling* refuerzo's marker
    ahead of its own would be filed under the sibling — and silently replace the
    real R1 wording with R2's. The 133 blocks of the current capture are one per
    refuerzo, so this changes nothing today and only removes the trap.
    """
    measure_texts: dict[str, str] = {}
    reinforcement_texts: dict[str, dict[str, str]] = {}
    for literal, decode in _blocks(js_source):
        reinforcement = _REINFORCEMENT_MARKER.search(literal)
        if reinforcement is not None:
            measure_code, reinforcement_number = reinforcement.groups()
            by_code = reinforcement_texts.setdefault(_normalise_code(measure_code), {})
            by_code.setdefault(f"R{reinforcement_number}", decode())
            continue
        measure = _MEASURE_MARKER.search(literal)
        if measure is not None:
            measure_texts.setdefault(_normalise_code(measure.group(1)), decode())
    return ParsedNormTexts(measure_texts, reinforcement_texts)
