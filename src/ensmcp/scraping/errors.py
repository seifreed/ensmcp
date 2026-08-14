"""Errors raised by the scraping infrastructure."""

from __future__ import annotations


class MeasurePageStructureError(Exception):
    """Raised when a page never renders the DOM structure we expect.

    The message carries the URL, selector and timeout that were used, so a
    traceback is actionable without re-running the scraper with extra logging.

    ``selector`` is kept as an attribute on top of that because callers
    discriminate on *which* structural step failed — the outer iframe, the
    content frame, or the summary table — and matching a substring of the
    message would be brittle. ``url`` and ``timeout_ms`` used to be stored
    the same way, but nothing ever read them: they are already in the message.
    """

    def __init__(self, url: str, selector: str, timeout_ms: int) -> None:
        super().__init__(
            f"Expected selector {selector!r} did not appear within {timeout_ms}ms at {url}"
        )
        self.selector = selector
