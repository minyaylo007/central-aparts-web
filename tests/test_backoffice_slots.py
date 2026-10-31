"""
Стеля одночасних звернень до бек-офісу (спека 002, US3): не більше N мереж звернень
процесу одночасно, очікування місця ≤ W, далі швидка відмова BackofficeBusy; гостю —
503 busy (JSON-ручки); бронь — повз стелю. Справжній BackofficeClient поверх
StubSession (tests/conftest.py).

Не critical (13.10.2026): фіча вимкнена за замовчуванням (BACKOFFICE_MAX_CONCURRENT=0),
гість її не бачить. Один тест на гілку — 13 → 4; устареле з кешу на сторінках і
звільнення місця за помилкою лишились у test_limits_logging / test_backoffice_unavailable.
"""
import threading
import time

import pytest

import api_client
from api_client import get_client

QUOTE_URL = "/api/quote/apt-uuid-a?check_in=2030-06-01&check_out=2030-06-03&guests=2"
BOOK_FORM = {"check_in": "2030-06-01", "check_out": "2030-06-03", "guests": "2",
             "name": "Тест", "phone": "+380000000000"}


def _quietly(fn):
    try:
        fn()
    except Exception:
        pass


@pytest.fixture
def hold(real_client):
    """hold() — фоновий потік займає місце під стелею: його звернення стоїть у
    сесії на gate, доки тест (або teardown) не відпустить. Наступні звернення
    сесія не тримає."""
    holders = []

    def _hold():
        session = real_client.session
        gate = threading.Event()
        session.entered.clear()
        session.gate = gate
        c = get_client()
        t = threading.Thread(target=_quietly, daemon=True,
                             args=(lambda: c.check_coupon("+380000000001"),))
        t.start()
        assert session.entered.wait(2)
        session.gate = None
        holders.append((gate, t))
        return gate

    yield _hold
    for gate, t in holders:
        gate.set()
        t.join(2)


def test_us3_peak_not_above_n(real_client):
    c = get_client()
    api_client.set_slots(3, 2.0)
    session = real_client.session
    session.delay_s = 0.05

    def call(i):
        kind = i % 6
        if kind == 0:
            return c.get_quote("apt-uuid-a", f"2030-06-{i:02d}", f"2030-07-{i:02d}", 2)
        if kind == 1:
            return c.check_coupon("+380000000000")
        key, fn = [(("apartments", "ua"), lambda: c.list_apartments("ua")),
                   (("apartment", "apt-uuid-a", "ua"), lambda: c.get_apartment("apt-uuid-a")),
                   (("availability", "apt-uuid-a"), lambda: c.get_availability("apt-uuid-a")),
                   (("rates", "apt-uuid-a"), lambda: c.get_rates("apt-uuid-a"))][kind - 2]
        api_client._ttl_cache.pop(key, None)      # у мережу, а не з кешу
        return fn()

    barrier = threading.Barrier(30)
    results = [None] * 30

    def worker(i):
        barrier.wait()
        results[i] = call(i)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(30)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    assert all(r is not None for r in results)
    assert session.total() >= 10
    assert session.peak <= 3


def test_us3_refusal_within_wait(real_client, hold):
    api_client.set_slots(1, 0.2)
    hold()
    t0 = time.perf_counter()
    with pytest.raises(api_client.BackofficeBusy) as e:
        get_client().get_quote("apt-uuid-a", "2030-06-01", "2030-06-03", 2)
    elapsed = time.perf_counter() - t0
    assert 0.2 <= elapsed <= 0.2 + 0.5
    assert e.value.call == "quote"
    assert e.value.waited_ms >= 200
    assert real_client.session.calls["quote/apt-uuid-a"] == 0


def test_us3_json_busy(client, real_client, hold):
    api_client.set_slots(1, 0.2)
    hold()
    r = client.get(QUOTE_URL)
    assert r.status_code == 503
    assert r.get_json() == {"error": "busy"}
    assert r.headers["Retry-After"] == "5"


def test_us3_booking_bypasses_ceiling(client, real_client, hold):
    api_client.set_slots(1, 0.2)
    hold()
    t0 = time.perf_counter()
    r = client.post("/book/apt-uuid-a", data=BOOK_FORM)
    assert time.perf_counter() - t0 < 0.2
    assert r.status_code == 302
    assert r.headers["Location"] == "https://pay.monobank.ua/test"
    r = client.get("/booking-confirmed?ref=test1234")
    assert r.status_code == 200
    assert real_client.session.calls["booking/test1234"] == 1
