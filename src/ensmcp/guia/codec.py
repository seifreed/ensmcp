"""Pure JSON codec for the extracted CCN-STIC 808 checklist.

An audit verifies the RD's articles as well as the Anexo II measures, and asks
for documents as evidence. The site serves neither: its ``requisitos.js`` has
73 measure blocks, no articles, and its questions carry no evidence list. Both
come from the guide, extracted once by ``scripts/build_guia_808.py``.

Kept apart from the snapshot on purpose. ``anexo_ii.json`` is checked byte for
byte against the live site by a network test; folding in data the site does not
publish would break that for no real reason. Different sources, different
files, different lifecycles.

This module is pure, like ``snapshot.codec``: JSON text in, domain objects out.
No filesystem — ``guia.loader`` is what knows where the file lives.

**Check the edition before adding another guide.** The CCN-STIC 800 series
still circulates in editions written for RD 3/2010, which RD 311/2022 repealed,
and they look current until you diff them against the Anexo II. These were
evaluated and deliberately **not** integrated:

- ``CCN-STIC 804 ENS. Guía de implantación`` (junio 2017) — the obvious next
  step, since 808 §4.6 points at it for "how do I implement this". But it is
  the RD 3/2010 edition: it documents ten measures that no longer exist
  (``op.acc.7``, ``op.exp.11``, the ``.9`` "otros" ones) and has nothing for
  eight that do, ``op.nub.1`` (servicios en la nube) among them. Shipping it
  would hand a consultant instructions for a repealed catalogue.
- ``CCN-STIC 819 Medidas compensatorias`` (octubre 2018), ``CCN-STIC 883
  Entidades Locales`` (mayo 2020) and ``CCN-STIC 884 Perfil Azure``
  (diciembre 2019) — same problem, all pre-2022. The 883/884 profiles would
  additionally change the domain model, since a perfil de cumplimiento
  overrides which measures apply.

The way to tell in one command: a guide for the current ENS cites "Real Decreto
311/2022"; these cite "Real Decreto 3/2010" and never the new one.
"""

from __future__ import annotations

from ensmcp.domain.models import ArticleCheck, ArticleQuestion, Guia808, MeasureEvidence
from ensmcp.json_codec import load_object, require_string, require_unique

SCHEMA_VERSION = 2


def _evidence(value: object, where: str) -> tuple[str, ...]:
    """An evidence list, refusing anything else written in its place.

    A bare string is the shape the ``TypeError`` guard in ``load`` cannot see,
    because it is not an error in Python: ``tuple("un papel")`` is eight
    one-character entries. A hand-edited file loaded without a word and
    ``requisitos_articulos`` served the auditor eight evidence bullets one
    letter long. ``TypeError`` so it lands in that guard and comes out naming
    the file.
    """
    if not isinstance(value, list):
        raise TypeError(
            f"evidence for {where} is a {type(value).__name__}, expected a list of strings"
        )
    return tuple(
        require_string(item, f"evidence for {where}[{index}]") for index, item in enumerate(value)
    )


def load(text: str) -> Guia808:
    """Parse the guide's JSON, refusing a file from another schema version.

    Same contract as the snapshot codec, including for a file that declares the
    right version without having its shape: refused with a message naming the
    problem, rather than a bare ``KeyError: 'title'`` that does not even say the
    file is at fault.
    """
    document = load_object(text, "guia_808", SCHEMA_VERSION)
    try:
        guide = Guia808(
            source=require_string(document["source"], "source"),
            articles=tuple(
                ArticleCheck(
                    reference=require_string(article["reference"], "article.reference"),
                    title=require_string(article["title"], "article.title"),
                    evidence=_evidence(
                        article["evidence"],
                        require_string(article["reference"], "article.reference"),
                    ),
                    questions=tuple(
                        ArticleQuestion(
                            reference=require_string(
                                item["reference"], "article_question.reference"
                            ),
                            question=require_string(item["question"], "article_question.question"),
                        )
                        for item in article["questions"]
                    ),
                )
                for article in document["articles"]
            ),
            # El orden lo pone el fichero y aquí no se toca. Es el del apartado
            # 6.2 de la guía, que es el del Anexo II — org, op, mp — o sea el
            # mismo que recorren ``alcance_auditoria`` y
            # ``declaracion_aplicabilidad``, que es la unión que el README manda
            # hacer. Un test lo comprueba fila a fila contra el snapshot.
            #
            # Ordenar aquí por código es lo que se hacía antes, y no puede dar
            # eso: alfabéticamente ``mp`` va antes que ``op`` y ``op`` antes que
            # ``org``, y dentro de ``op`` el RD va pl, acc, exp, ext, nub, cont,
            # mon. Salían descolocadas las 73 filas, no una — y el test que lo
            # vigilaba ordenaba también la lista de la tabla antes de comparar,
            # con lo que comprobaba que un orden coincide consigo mismo.
            #
            # Por eso viaja como lista y no como objeto JSON: el orden es dato,
            # igual que en ``SecurityMeasure.audit_requirements``, y una lista lo
            # dice explícitamente en vez de depender de que quien lea el fichero
            # conserve el orden de las claves.
            measure_evidence=tuple(
                MeasureEvidence(
                    measure_code=require_string(
                        item["measure_code"], "measure_evidence.measure_code"
                    ),
                    evidence=_evidence(
                        item["evidence"],
                        require_string(item["measure_code"], "measure_evidence.measure_code"),
                    ),
                )
                for item in document["measure_evidence"]
            ),
        )
        require_unique([article.reference for article in guide.articles], "article reference")
        require_unique(
            [item.measure_code for item in guide.measure_evidence], "measure evidence code"
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(
            f"guia_808 declares schema version {SCHEMA_VERSION} but does not have its shape "
            f"({type(exc).__name__}: {exc})"
        ) from exc
    return guide
