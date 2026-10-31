"""
Обмеження частоти WARNING (analyze 002, M3): `backoffice_stale_served` у вікні техробіт
бек-офісу писався на кожен перегляд сторінки. Тепер — не частіше рядка на інтервал на
процес і вид звернення, з числом пропущених. Той самий помічник `_RateLimitedWarning`
спека 002 бере для `backoffice_busy` / `backoffice_unavailable` (T031: 1 рядок/с).
"""
import logging

import pytest

import api_client


class _Clock:
    """Керований годинник для api_client: time.time (_cached) і time.monotonic (обмежувач)."""

    def __init__(self):
        self.t = 1000.0

    def time(self):
        return self.t

    def monotonic(self):
        return self.t


@pytest.fixture
def clock(monkeypatch):
    c = _Clock()
    monkeypatch.setattr(api_client, "time", c)
    monkeypatch.setattr(api_client, "_stale_log", api_client._RateLimitedWarning(
        "backoffice_stale_served", api_client._STALE_LOG_INTERVAL))
    api_client._ttl_cache.clear()
    yield c
    api_client._ttl_cache.clear()


def _down():
    raise ConnectionError("backoffice down")


def _lines(caplog, event):
    return [r.getMessage() for r in caplog.records if r.getMessage().startswith(event + " ")]


def test_stale_served_rate_limited_per_kind(clock, caplog):
    caplog.set_level(logging.DEBUG, logger="api_client")
    apts, avail = ("apartments", "ua"), ("availability", "apt-uuid-a")
    assert api_client._cached(apts, 60, lambda: ["warm"]) == ["warm"]
    assert api_client._cached(avail, 30, lambda: ["busy"]) == ["busy"]
    clock.t += 61                                     # обидва записи прострочені
    for _ in range(50):                               # гість щоразу бачить устареле, як і раніше
        assert api_client._cached(apts, 60, _down) == ["warm"]
    assert _lines(caplog, "backoffice_stale_served") == [
        "backoffice_stale_served key=('apartments', 'ua') suppressed=0"]
    assert api_client._cached(avail, 30, _down) == ["busy"]     # інший вид — свій рядок
    assert _lines(caplog, "backoffice_stale_served")[-1] == (
        "backoffice_stale_served key=('availability', 'apt-uuid-a') suppressed=0")
    clock.t += api_client._STALE_LOG_INTERVAL - 1
    api_client._cached(apts, 60, _down)               # ще в інтервалі — пропущено (50-й)
    assert len(_lines(caplog, "backoffice_stale_served")) == 2
    clock.t += 1
    api_client._cached(apts, 60, _down)
    assert _lines(caplog, "backoffice_stale_served")[-1] == (
        "backoffice_stale_served key=('apartments', 'ua') suppressed=50")
    assert all(r.exc_info is None for r in caplog.records)


def test_helper_default_one_line_per_second(clock, caplog):
    """Як задумано в 002 (T031): одна подія — один кошик, 1 рядок/с, args форматуються."""
    caplog.set_level(logging.DEBUG, logger="api_client")
    busy = api_client._RateLimitedWarning("backoffice_busy")
    for _ in range(50):
        busy.warn("call=%s waited_ms=%d", "quote", 2001)
    clock.t += 1.0
    busy.warn("call=%s waited_ms=%d", "rates", 2002)
    assert _lines(caplog, "backoffice_busy") == [
        "backoffice_busy call=quote waited_ms=2001 suppressed=0",
        "backoffice_busy call=rates waited_ms=2002 suppressed=49",
    ]
    assert all(r.levelno == logging.WARNING for r in caplog.records)
