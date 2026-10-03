"""Keep unit tests independent from live delivery and PostgreSQL-only transaction locks."""
from unittest.mock import AsyncMock
import pytest


@pytest.fixture(autouse=True)
def isolate_live_delivery(monkeypatch):
    from app.api import scans, tsd
    from app.services import document_guard, scan_events
    for module in (scans, tsd, scan_events):
        for name in ("publish_event", "publish_scan", "publish_removed"):
            if hasattr(module, name):
                monkeypatch.setattr(module, name, AsyncMock())
    monkeypatch.setattr(document_guard, "lock_ms_document", AsyncMock())
    monkeypatch.setattr(tsd, "lock_ms_document", AsyncMock())
