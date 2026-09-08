"""Ports supplied by the composition root to MCP capability registrars."""

from collections.abc import Awaitable, Callable
from datetime import datetime

from ensmcp.domain.dda import DDARecord, ExportedDocument, ExportFormat

RefreshHandler = Callable[[], Awaitable[None]]
StatusHandler = Callable[[], dict[str, object]]
ExportHandler = Callable[[DDARecord, ExportFormat], ExportedDocument]
Clock = Callable[[], datetime]
