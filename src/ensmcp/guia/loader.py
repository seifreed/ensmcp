"""Where the CCN-STIC 808 data file is read from.

The impure half of the split ``snapshot`` already makes: ``guia.codec`` turns
text into domain objects and knows nothing about files, and this knows which
file without knowing its shape.
"""

from __future__ import annotations

from ensmcp import package_data
from ensmcp.domain.models import Guia808
from ensmcp.guia.codec import load

GUIDE_FILENAME = "guia_808.json"


def _require_content(guide: Guia808) -> Guia808:
    incomplete = (
        not guide.source.strip()
        or not guide.articles
        or not guide.measure_evidence
        or any(
            not article.reference.strip()
            or not article.title.strip()
            or not article.questions
            or not article.evidence
            or any(
                not question.reference.strip() or not question.question.strip()
                for question in article.questions
            )
            or any(not item.strip() for item in article.evidence)
            for article in guide.articles
        )
        or any(
            not item.measure_code.strip()
            or not item.evidence
            or any(not evidence.strip() for evidence in item.evidence)
            for item in guide.measure_evidence
        )
    )
    if incomplete:
        raise ValueError("guia_808 is incomplete")
    return guide


def load_packaged_guide() -> Guia808:
    """Load the copy shipped inside the installed package."""
    return _require_content(load(package_data.read(GUIDE_FILENAME)))
