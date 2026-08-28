"""Клиент мониторинга не должен ничего слать, пока не сконфигурирован, и не падать."""

from app.core import monitoring


async def test_emit_noop_when_disabled(monkeypatch):
    monkeypatch.setattr(monitoring.settings, "MONITORING_ENABLED", False, raising=False)
    called = False

    class _Boom:  # если клиент попробует ходить в сеть — тест упадёт
        def __init__(self, *a, **k):
            nonlocal called
            called = True

    monkeypatch.setattr(monitoring.httpx, "AsyncClient", _Boom)
    await monitoring.emit("test.event", document_id="x")
    assert called is False


async def test_emit_swallows_errors(monkeypatch):
    # Включён, но транспорт падает — emit НЕ должен пробрасывать исключение.
    monkeypatch.setattr(monitoring.settings, "MONITORING_ENABLED", True, raising=False)
    monkeypatch.setattr(monitoring.settings, "MONITORING_URL", "http://x/monitoring", raising=False)
    monkeypatch.setattr(monitoring.settings, "MONITORING_KEY", "k", raising=False)

    class _Boom:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            raise RuntimeError("network down")

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(monitoring.httpx, "AsyncClient", _Boom)
    await monitoring.emit("test.event")  # не должно бросить
