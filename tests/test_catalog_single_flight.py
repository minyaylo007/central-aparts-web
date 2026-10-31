"""23.10.2026: 429 на каталозі. Стеля бек-офісу — 2 одночасних публічних читання на його
процес; кілька наших потоків разом промахувались повз прострочений запис і йшли по той
самий каталог. Тепер: один запит на ключ і процес; 429 стелі при молодому старому значенні —
INFO (гість бачить каталог хвилинної давнини), старому — WARNING, 503 — WARNING завжди."""
import logging
import threading

import pytest

import api_client
from tests.conftest import StubResponse

pytestmark = pytest.mark.critical   # доступність каталогу гостю

KEY = ("apartments", "ua")


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    monkeypatch.setattr(api_client, "_stale_log", api_client._RateLimitedWarning(
        "backoffice_stale_served", api_client._STALE_LOG_INTERVAL))
    monkeypatch.setattr(api_client, "_unavailable_log",
                        api_client._RateLimitedWarning("backoffice_unavailable"))
    api_client._ttl_cache.clear()
    yield
    api_client._ttl_cache.clear()


def _gated_fetch(calls, release, value=("cat",)):
    def fetch():
        calls.append(1)
        release.wait(5)
        return list(value)
    return fetch


def _run(n, target):
    out = [None] * n
    threads = [threading.Thread(target=lambda i=i: out.__setitem__(i, target())) for i in range(n)]
    for t in threads:
        t.start()
    return threads, out


def test_concurrent_miss_without_stale_one_request(monkeypatch):
    calls, release = [], threading.Event()
    fetch = _gated_fetch(calls, release)
    threads, out = _run(6, lambda: api_client._cached(KEY, 60, fetch))
    while not calls:
        pass
    release.set()
    for t in threads:
        t.join(5)
    assert len(calls) == 1
    assert out == [["cat"]] * 6


def test_concurrent_miss_with_stale_serves_stale_without_second_request():
    api_client._ttl_cache[KEY] = (0, ["old"])          # прострочене
    calls, release = [], threading.Event()
    leader, out = _run(1, lambda: api_client._cached(KEY, 60, _gated_fetch(calls, release)))
    while not calls:
        pass
    # ведучий у мережі — решта віддає старе одразу, нового запиту нема
    for _ in range(5):
        assert api_client._cached(KEY, 60, lambda: pytest.fail("другий запит")) == ["old"]
    release.set()
    leader[0].join(5)
    assert out == [["cat"]]
    assert api_client._cached(KEY, 60, lambda: pytest.fail("кеш свіжий")) == ["cat"]


def _answer(status):
    def fetch():
        api_client._raise_if_unavailable(StubResponse(status, {}, {"Retry-After": "1"}),
                                         "apartments")
        return ["new"]
    return fetch


def _levels(caplog):
    return [(r.levelno, r.getMessage().split()[0]) for r in caplog.records
            if r.name == "api_client"]


@pytest.mark.parametrize("status, age_s, level", [
    (429, 61, logging.INFO),         # стеля, каталог хвилинної давнини — задумана поведінка
    (429, 601, logging.WARNING),     # стеля відмовляє > 10 хв — сторож має побачити
    (503, 61, logging.WARNING),      # техроботи — завжди WARNING
], ids=["429_young", "429_old", "503"])
def test_stale_level(caplog, monkeypatch, status, age_s, level):
    now = api_client.time.time()
    api_client._ttl_cache[KEY] = (now - age_s + 60, ["old"])   # отримано age_s тому, TTL 60
    caplog.set_level(logging.INFO, logger="api_client")
    assert api_client._cached(KEY, 60, _answer(status)) == ["old"]
    assert _levels(caplog) == [(level, "backoffice_unavailable"),
                               (level, "backoffice_stale_served")]


def test_429_without_stale_stays_warning(caplog):
    caplog.set_level(logging.INFO, logger="api_client")
    with pytest.raises(api_client.BackofficeUnavailable):
        api_client._cached(KEY, 60, _answer(429))
    assert _levels(caplog) == [(logging.WARNING, "backoffice_unavailable")]


def test_quiet_flag_does_not_leak_to_next_call(caplog):
    """Після тихого 429 каталогу 429 котирування (без кешу) — WARNING."""
    now = api_client.time.time()
    api_client._ttl_cache[KEY] = (now - 1, ["old"])
    caplog.set_level(logging.INFO, logger="api_client")
    api_client._cached(KEY, 60, _answer(429))
    caplog.clear()
    with pytest.raises(api_client.BackofficeUnavailable):
        api_client._raise_if_unavailable(StubResponse(429, {}, {"Retry-After": "1"}), "quote")
    assert _levels(caplog) == [(logging.WARNING, "backoffice_unavailable")]
