"""Tests for the MeasureRepository port.

Importing the module is enough to exercise its statements: Protocol method
bodies are pure signatures (no executable logic), covered by definition,
not by invocation. What's worth asserting is the shape of the contract
itself.
"""

from __future__ import annotations

from ensmcp.domain.repository import MeasureRepository
from tests.support import check


def test_measure_repository_declares_the_expected_async_methods() -> None:
    check(hasattr(MeasureRepository, "fetch_corpus"))
