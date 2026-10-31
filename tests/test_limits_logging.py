"""
Журнал фічі 002 (US4): backoffice_busy і backoffice_unavailable — не частіше рядка на
секунду на подію в процесі, з числом пропущених; відмітка cache= у рядку quote; рядки
фічі розрізнені за першим словом, без exc_info; жодних даних гостя (адреси, телефони
й хвости, дати, номер броні, тіло відповіді бек-офісу). Лише синтетика.
"""
import logging
import time

import pytest
import requests

import api_client
from api_client import get_client
from conftest import StubResponse, default_route

GUEST_IP, PROXY_IP = "198.51.100.23", "203.0.113.7"
XFF = {"X-Forwarded-For": f"{GUEST_IP}, {PROXY_IP}"}
PHONE_A, PHONE_B = "+380000000000", "+380000000001"
QUOTE_URL = "/api/quote/apt-uuid-a?check_in=2030-06-01&check_out=2030-06-03&guests=2"
BOOK_FORM = {"check_in": "2030-06-01", "check_out": "2030-06-03", "guests": "2",
             "name": "Тест", "phone": PHONE_A}
FEATURE_EVENTS = ("quote_cache_mode", "backoffice_slots_mode", "backoffice_limits_config_invalid",
                  "backoffice_busy", "backoffice_unavailable", "book_backoffice_unavailable")
HTML = "<html><body>Технічні роботи</body></html>"


class _Clock:
    """Керований time.monotonic для обмежувача в api_client; решта time — справжня."""

    def __init__(self):
        self.t = 1000.0

    def monotonic(self):
        return self.t

    def __getattr__(self, name):
        return getattr(time, name)


@pytest.fixture
def fresh_logs(monkeypatch):
    """Нові обмежувачі (без стану попередніх тестів) і керований годинник."""
    clock = _Clock()
    monkeypatch.setattr(api_client, "time", clock)
    monkeypatch.setattr(api_client, "_busy_log", api_client._RateLimitedWarning("backoffice_busy"))
    monkeypatch.setattr(api_client, "_unavailable_log",
                        api_client._RateLimitedWarning("backoffice_unavailable"))
    return clock


@pytest.fixture
def full_ceiling(real_client):
    """Стеля з одним місцем без очікування, місце вже зайняте — кожне звернення
    під стелею відмовляє одразу."""
    api_client.set_slots(1, 0.0)
    sem = api_client._slots._sem
    sem.acquire()
    yield
    sem.release()


def maintenance(method, path, params, headers, body):
    return StubResponse(503, HTML, {"Retry-After": "300", "Cache-Control": "no-store"},
                        content_type="text/html")


def _lines(caplog, event):
    return [r.getMessage() for r in caplog.records if r.getMessage().split(" ", 1)[0] == event]


# --- частота --------------------------------------------------------------------

def test_us4_busy_rate_limited(real_client, full_ceiling, fresh_logs, caplog):
    caplog.set_level(logging.DEBUG, logger="api_client")
    c = get_client()
    for i in range(50):
        with pytest.raises(api_client.BackofficeBusy):
            c.get_quote("apt-uuid-a", "2030-06-01", "2030-06-03", 2)
    [line] = _lines(caplog, "backoffice_busy")
    assert line.startswith("backoffice_busy call=quote waited_ms=")
    assert line.endswith(" suppressed=0")
    fresh_logs.t += 1.0
    with pytest.raises(api_client.BackofficeBusy):
        c.check_coupon(PHONE_A)
    assert _lines(caplog, "backoffice_busy")[-1].startswith("backoffice_busy call=coupon_check ")
    assert _lines(caplog, "backoffice_busy")[-1].endswith(" suppressed=49")
    assert all(r.levelno == logging.WARNING and r.exc_info is None for r in caplog.records)


def test_us4_unavailable_rate_limited_separately(real_client, full_ceiling, fresh_logs, caplog):
    caplog.set_level(logging.DEBUG, logger="api_client")
    c = get_client()
    for _ in range(3):                                   # отмова стелі в ту саму секунду
        with pytest.raises(api_client.BackofficeBusy):
            c.get_quote("apt-uuid-a", "2030-06-01", "2030-06-03", 2)
    real_client.session.route = maintenance
    for _ in range(50):                                  # бронь — повз стелю, бек-офіс 503
        with pytest.raises(api_client.BackofficeUnavailable):
            c.get_booking("test1234")
    assert _lines(caplog, "backoffice_unavailable") == [
        "backoffice_unavailable status=503 retry_after=300 call=booking suppressed=0"]
    assert len(_lines(caplog, "backoffice_busy")) == 1   # свій кошик, не спільний
    fresh_logs.t += 1.0
    with pytest.raises(api_client.BackofficeUnavailable):
        c.get_booking("test1234")
    assert _lines(caplog, "backoffice_unavailable")[-1] == (
        "backoffice_unavailable status=503 retry_after=300 call=booking suppressed=49")


# --- відмітка кешу й розрізнення рядків -------------------------------------------

def test_us4_quote_cache_mark(client, real_client, caplog):
    caplog.set_level(logging.DEBUG, logger="app")
    api_client.set_quote_cache(30)
    client.get(QUOTE_URL)
    client.get(QUOTE_URL)
    client.get(QUOTE_URL + "&coupon_phone=%2B380000000000")
    api_client.set_quote_cache(0)
    client.get(QUOTE_URL)
    marks = [line.rsplit(" ", 1)[-1] for line in _lines(caplog, "quote")]
    assert marks == ["cache=miss", "cache=hit", "cache=off", "cache=off"]


def test_us4_lines_distinct(client, real_client, fresh_logs, caplog):
    caplog.set_level(logging.DEBUG)
    session = real_client.session
    api_client.set_slots(1, 0.0)
    sem = api_client._slots._sem
    sem.acquire()
    try:
        assert client.get(QUOTE_URL).status_code == 503              # backoffice_busy
    finally:
        sem.release()
    api_client.set_slots(0, 2.0)
    session.route = maintenance
    assert client.get("/api/availability/apt-uuid-a").status_code == 503
    assert client.post("/book/apt-uuid-a", data=BOOK_FORM).status_code == 302

    def down(method, path, params, headers, body):
        raise requests.ConnectionError("stub backoffice down")

    session.route = down
    assert client.get(QUOTE_URL).status_code == 503                 # quote_api_error
    assert client.post("/book/apt-uuid-a", data=BOOK_FORM).status_code == 302
    events = {r.getMessage().split(" ", 1)[0]: r for r in caplog.records}
    for event in ("backoffice_busy", "backoffice_unavailable", "book_backoffice_unavailable",
                  "quote_api_error", "book_unexpected_error"):
        assert event in events, event
    assert events["book_backoffice_unavailable"].getMessage() == (
        "book_backoffice_unavailable status=503 retry_after=300")
    assert all(r.exc_info is None for r in caplog.records
               if r.getMessage().split(" ", 1)[0] in FEATURE_EVENTS)


# --- жодних даних гостя ---------------------------------------------------------------

FEATURE_NEEDLES = (GUEST_IP, PROXY_IP, PHONE_A, "380000000000", "0000", "0001", "2030-06",
                   "test1234", "Технічні роботи", "maintenance", "Traceback")


@pytest.mark.critical
def test_us4_no_personal_data(client, real_client, fresh_logs, caplog, monkeypatch):
    from app import app as flask_app
    monkeypatch.setitem(flask_app.config, "PROPAGATE_EXCEPTIONS", False)
    caplog.set_level(logging.DEBUG)
    session = real_client.session
    import app as app_module
    app_module._log_backoffice_limits_mode()

    # T010: кеш і купон
    api_client.set_quote_cache(30)
    for _ in range(2):
        client.get(QUOTE_URL, headers=XFF)
        client.get(QUOTE_URL + "&coupon_phone=%2B380000000000", headers=XFF)
    client.get("/", headers=XFF)
    client.get("/apartment/apt-uuid-a", headers=XFF)

    # T016: місце зайняте
    api_client._ttl_cache.clear()
    api_client.set_quote_cache(0)
    api_client.set_slots(1, 0.0)
    sem = api_client._slots._sem
    sem.acquire()
    try:
        client.get(QUOTE_URL, headers=XFF)
        client.get("/api/availability/apt-uuid-a", headers=XFF)
        client.get("/api/rates/apt-uuid-a", headers=XFF)
        client.post("/api/coupon/check", json={"phone": PHONE_B}, headers=XFF)
        client.get("/", headers=XFF)
        client.get("/apartment/apt-uuid-a", headers=XFF)
    finally:
        sem.release()
    api_client.set_slots(0, 2.0)

    # T020, T022, T023: техроботи бек-офісу
    session.route = maintenance
    for _ in range(2):
        fresh_logs.t += 1.0                              # щоб кожен рядок справді писався
        client.get(QUOTE_URL + "&coupon_phone=%2B380000000000", headers=XFF)
        client.get("/api/availability/apt-uuid-a", headers=XFF)
        client.post("/api/coupon/check", json={"phone": PHONE_A}, headers=XFF)
        client.post("/api/coupon/check", json={"phone": PHONE_B}, headers=XFF)
        client.post("/book/apt-uuid-a", data=dict(BOOK_FORM, coupon_phone=PHONE_B), headers=XFF)
        client.get("/", headers=XFF)
        client.get("/apartment/apt-uuid-a", headers=XFF)
        client.get("/booking-confirmed?ref=test1234", headers=XFF)
    session.route = default_route

    feature = [r for r in caplog.records if r.getMessage().split(" ", 1)[0] in FEATURE_EVENTS]
    seen = {r.getMessage().split(" ", 1)[0] for r in feature}
    assert {"backoffice_busy", "backoffice_unavailable", "book_backoffice_unavailable",
            "quote_cache_mode", "backoffice_slots_mode"} <= seen
    for r in feature:
        text = r.getMessage()
        for needle in FEATURE_NEEDLES:
            assert needle not in text, (needle, text)
        assert r.exc_info is None
    for needle in (GUEST_IP, PROXY_IP, PHONE_A, PHONE_B, "380000000000", "380000000001",
                   "Технічні роботи"):
        assert needle not in caplog.text, needle
