"""Port the domain depends on instead of any concrete data source."""

from __future__ import annotations

from typing import Protocol

from ensmcp.domain.models import Category, SecurityMeasure


class MeasureRepository(Protocol):
    """Source of ENS categories and measures, implemented by infrastructure."""

    async def fetch_corpus(self) -> tuple[list[Category], list[SecurityMeasure]]:
        """Return both halves, read as one — never two reads stitched together.

        Keeping separate methods for each half made every implementation repeat
        the same projections and allowed callers to stitch together two reads.
        """
