"""Pure parsers for everything requisitos.js carries.

That asset is the CCN-STIC 808 **audit questionnaire**, and two different things
are read out of it in one pass (pulling the JS literal, splitting it per
measure, flattening HTML to text):

- ``ParsedRequisitos.descriptions`` — the flattened text of a measure's whole block, which
  is what ``SecurityMeasure.description`` holds and what free-text search reads.
- ``ParsedRequisitos.audit_requirements`` — the same block taken apart into the individual
  questions an auditor asks, each with its level, its printed code, whether it
  is *essential* and its clarifying note.

ENS Navegable's summary table (``#tablaResumen``) carries every measure's code,
title, dimensions and applicability, but **no description**. Those descriptions
live in a separate build asset, ``requisitos.js``, as a single JS string literal
of the form::

    var requisitos808='<div class="requeriments-wrapper">...<div class="section-title">\
    <span>Política de seguridad (org.1)</span></div><div class="modal-container">...'

Each measure is delimited by a ``<div class="section-title"><span>Título (código)\
</span></div>`` header, and the code is the **last** parenthesised group of that
span (titles themselves may contain parentheses, e.g. "Mecanismo de
autenticación (usuarios externos) (op.acc.5)").

The parser is pure: it takes the raw JS source (already fetched) and returns
both mappings keyed by measure code. No Patchright, no network. A real capture of
the live site's ``requisitos.js`` (``tests/scraping/fixtures/requisitos.js``)
confirms exactly 73 such blocks, one per measure, matching the 73 rows of
``#tablaResumen`` 1:1, and 430 audit questions across them (the asset has 431
blocks, but one of them — ``mp.info.1``'s "1.4" — carries no question at all).
"""

from __future__ import annotations

import re
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from html.parser import HTMLParser

from ensmcp.domain.models import AuditRequirement, SystemCategory

# ponytail: the real requisitos.js is a single-quoted JS string with no escaped
# single-quotes inside (verified against the fixture: exactly two "'" in the
# file). If the site ever switches to double quotes or adds escapes, update this.
_VAR_DECLARATION = re.compile(r"var\s+\w+\s*=\s*'")
_SECTION_DELIMITER = re.compile(r'<div class="section-title"><span>([^<]+)</span></div>')
# ENS measure codes: org.1, op.pl.1, op.acc.5, mp.if.3, mp.info.6, mp.s.4 — a
# dotted lowercase path ending in a leaf number.
_CODE_PATTERN = re.compile(r"^[a-z]+(?:\.[a-z]+)*\.\d+$")

# Inside a measure block, each level opens a collapsible whose header names it,
# and every requirement after it belongs to that level until the next header.
_LEVEL_HEADER = re.compile(r'<div class="tittle-measure">([^<]*)</div>')
_LEVEL_LABELS = {
    "categoría básica": SystemCategory.BASICA,
    "categoría media": SystemCategory.MEDIA,
    "categoría alta": SystemCategory.ALTA,
}

# One requirement, of which only the question part is data: the sibling
# "requirement-text" div is the auditor's own answer box and is always empty.
_REQUIREMENT_START = re.compile(r'<div class="requirement">')
_QUESTION_CLASS = re.compile(r'<div class="requirement-question audit ?([^"]*)">')
_QUESTION = re.compile(r'<div class="q"><span class="code">([^<]*)</span>(.*?)</div>', re.S)
_NOTE = re.compile(r'<div class="a">(.*?)</div>', re.S)
_ESSENTIAL_CLASS = "essential"


@dataclass(frozen=True, slots=True)
class ParsedRequisitos:
    """Both readings of one ``requisitos.js`` parse."""

    descriptions: dict[str, str]
    audit_requirements: dict[str, tuple[AuditRequirement, ...]]


class _TextExtractor(HTMLParser):
    """Collapses an HTML fragment into a single space-joined text string."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []

    def handle_data(self, data: str) -> None:
        text = data.strip()
        if text:
            self._parts.append(text)

    def text(self) -> str:
        """The collapsed text — every run of whitespace down to one space.

        Stripping each chunk is not enough, and the difference is not cosmetic:
        a run *inside* a chunk survives it. The site serves two ("NOTA:  La
        priorización…" in op.exp.4, and mp.info.3's link followed by two spaces),
        so those measures shipped a description no reader can type: searching
        "NOTA: La priorización" with one space matched nothing at all, in a
        corpus that does contain the phrase.
        """
        return " ".join(" ".join(self._parts).split())


def _extract_html_literal(js_source: str) -> str:
    """Return the HTML string literal declared by ``var <name> = '...'``.

    Robust to the variable name (``requisitos808`` today) — only the shape of
    the declaration matters.
    """
    match = _VAR_DECLARATION.match(js_source)
    if match is None:
        raise ValueError("requisitos.js does not declare a single-quoted var = '...' literal")
    end = js_source.rfind("'", match.end())
    if end == -1:
        raise ValueError("requisitos.js string literal is not closed")
    return js_source[match.end() : end]


def _extract_code(label: str) -> str:
    """Return the measure code from a section-title span label.

    The code is the last parenthesised group: titles can contain their own
    parentheses ("... (usuarios externos) (op.acc.5)"), so the first '(' from
    the right is the one that opens the code group.
    """
    label = label.strip()
    open_idx = label.rfind("(")
    close_idx = label.rfind(")")
    # A missing ')' needs no check of its own: rfind returns -1, which is
    # already below every real open_idx, so close_idx < open_idx catches it.
    # (An explicit close_idx == -1 test used to sit here and could never be
    # the sole reason this fired — verified exhaustively over both indices.)
    if open_idx == -1 or close_idx < open_idx:
        raise ValueError(f"section title has no (code) group: {label!r}")
    code = label[open_idx + 1 : close_idx].strip()
    if not _CODE_PATTERN.match(code):
        raise ValueError(f"section title code is not an ENS measure code: {code!r}")
    return code


def parse_requisitos(js_source: str) -> ParsedRequisitos:
    """Parse both readings of ``requisitos.js`` in one block traversal.

    Each description is the plain-text content of the block between one
    section-title header and the next, with HTML tags stripped and whitespace
    collapsed. The level headers ("Categoría Básica/Media/Alta") are preserved,
    so a description reads like "Categoría Básica 1.1 ¿La PSI ...?".

    Each measure's block opens one collapsible per level, and every requirement
    after a level header belongs to that level until the next header — so the
    body is split on the headers and the requirements read within each segment.

    Requirements keep the order the questionnaire asks them in, because that
    order *is* information: the printed code restarts inside a measure (five
    separate "1.1" in ``op.acc.5``), and only the sequence shows where. Hence
    ``position`` rather than the code as identity.

    A requirement that no header covers is refused rather than skipped — see
    ``_require_every_requirement_under_a_level``.
    """
    parsed = [
        (code, _plain_text(body), tuple(_requirements_in(body)))
        for code, body in _measure_blocks(js_source)
    ]
    return ParsedRequisitos(
        descriptions={code: description for code, description, _ in parsed},
        audit_requirements={code: requirements for code, _, requirements in parsed},
    )


def _require_every_requirement_under_a_level(body: str, headers: Sequence[re.Match[str]]) -> None:
    """Refuse a measure block whose requirements do not all sit under a level.

    ``_requirements_in`` reads each level header and everything after it, so a
    ``requirement`` div placed **before** the first header is read by nobody: it
    has no level to be filed under and simply never appears. Silently — zero
    extra questions is not by itself anomalous, three real measures have none at
    básico, and nothing downstream counts them. ``alcance_auditoria`` would have
    told an auditor there is less to ask than the guide asks.

    So it raises, which is the same contract ``_parse_level_label`` already holds
    for a header it does not recognise, and the one ``NavegableRepository`` holds
    for an asset that stops naming a measure. The safe failure is identical: the
    server answers from the snapshot, so a raise downgrades the live check to
    ``unavailable`` and goes on serving the file, and ``build_snapshot`` refuses
    to overwrite it.

    A block with no headers at all is the same fault whenever it has
    requirements — every one of them is stray — and no fault when it has none,
    which is what the asset's non-measure blocks look like.

    All 431 requirement divs of the real capture sit under one of the three
    headers, so this changes nothing today and only removes the trap.
    """
    first_level = headers[0].end() if headers else len(body)
    stray = sum(1 for match in _REQUIREMENT_START.finditer(body) if match.start() < first_level)
    if stray:
        raise ValueError(
            f"{stray} requirement(s) sit before the first «Categoría …» header: "
            "they belong to no level and would be dropped from the audit scope"
        )


def _requirements_in(body: str) -> Iterator[AuditRequirement]:
    """Every audit requirement of one measure block, in order."""
    position = 0
    headers = list(_LEVEL_HEADER.finditer(body))
    _require_every_requirement_under_a_level(body, headers)
    for index, header in enumerate(headers):
        level = _parse_level_label(header.group(1))
        end = headers[index + 1].start() if index + 1 < len(headers) else len(body)
        for fragment in _split_requirements(body[header.end() : end]):
            question = _QUESTION.search(fragment)
            if question is None:
                # A "requirement" div with no question part: nothing an auditor
                # asks, so it must not become an empty entry in the list.
                continue
            # The same reason, for the shape the site actually ships: a ``q``
            # div holding its printed code and nothing else. ``mp.info.1``
            # serves exactly that for its "1.4" —
            # ``<div class="q"><span class="code">1.4</span></div>`` — and
            # checking only for the *absence* of the div let it through, so the
            # corpus published one audit requirement whose question was the
            # empty string. A numbered blank is not a question an auditor asks;
            # it counted towards the audit scope and rendered as a blank line.
            text = _plain_text(question.group(2))
            if not text:
                continue
            note = _NOTE.search(fragment)
            classes = _QUESTION_CLASS.search(fragment)
            yield AuditRequirement(
                position=position,
                code=question.group(1).strip(),
                level=level,
                essential=classes is not None and _ESSENTIAL_CLASS in classes.group(1),
                question=text,
                note=_plain_text(note.group(1)) if note else "",
            )
            position += 1


def _split_requirements(segment: str) -> Iterator[str]:
    """Cut one level's segment into its individual ``requirement`` divs."""
    starts = [match.start() for match in _REQUIREMENT_START.finditer(segment)]
    for index, start in enumerate(starts):
        yield segment[start : starts[index + 1] if index + 1 < len(starts) else len(segment)]


def _parse_level_label(label: str) -> SystemCategory:
    """Map a "Categoría Básica/Media/Alta" collapsible header to its level."""
    level = _LEVEL_LABELS.get(" ".join(label.split()).casefold())
    if level is None:
        raise ValueError(f"unknown audit level header: {label!r}")
    return level


def _plain_text(fragment: str) -> str:
    """Flatten an HTML fragment to whitespace-collapsed text.

    ``close()`` is not optional decoration, it is the second half of
    ``HTMLParser``'s contract: ``feed()`` holds back a tail it cannot yet tell
    apart from an incomplete construct, and only ``close()`` says "there is no
    more, treat what you kept as text". Without it that tail is dropped without
    a word.

    And it is not just the tail. A fragment ending in a bare ``&`` — which the
    parser has to assume might be the start of ``&amp;`` — loses **everything
    since the last tag**, not the ampersand alone: ``"fin &"`` came out as the
    empty string. In a question that is worse than a truncation, because
    ``_requirements_in`` drops a requirement whose text is empty, so the
    question would have disappeared from the audit scope entirely — the same
    outcome as ``mp.info.1``'s numbered blank, which this parser already has a
    branch for.

    Today's asset carries no ``&`` at all, so this changes nothing in the
    corpus and only removes the trap.
    """
    extractor = _TextExtractor()
    extractor.feed(fragment)
    extractor.close()
    return extractor.text()


def _measure_blocks(js_source: str) -> Iterator[tuple[str, str]]:
    """Yield ``(measure_code, block_html)`` for every measure in the asset.

    The preparation shared by both readings of the asset.
    """
    html = _extract_html_literal(js_source)
    marks = list(_SECTION_DELIMITER.finditer(html))
    if not marks:
        raise ValueError("no section-title blocks found in requisitos.js")
    seen: set[str] = set()
    for index, mark in enumerate(marks):
        end = marks[index + 1].start() if index + 1 < len(marks) else len(html)
        code = _extract_code(mark.group(1))
        if code in seen:
            raise ValueError(f"duplicate measure block in requisitos.js: {code}")
        seen.add(code)
        yield code, html[mark.end() : end]
