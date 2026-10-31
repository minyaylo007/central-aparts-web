"""27.10.2026: 429 стелі бек-офісу на холодному кеші (13.10, 22.10, 23.10, 27.10 — щоразу
одна відмова). Стеля — 2 одночасних публічних читання на його процес, сверх — одразу 429
Retry-After: 1; ключі різні (каталог, квартира A/B, доступність A/B), single-flight не рятує.
Тепер публічне GET-читання під стелею (apartments, apartment, availability, quote, rates,
quote-group) на 429 з Retry-After ≤ 2 с повторюється ОДИН раз після паузи; удалий повтор —
INFO backoffice_retry ok=1, без WARNING. 503 (техроботи), Retry-After > 2, брони — як раніше."""
import logging

import pytest

import api_client
from conftest import STUB_APARTMENTS, StubResponse, default_route

pytestmark = pytest.mark.critical   # доступність сторінок гостю і поведінка броні на 429

BOOK_FORM = {"check_in": "2030-06-01", "check_out": "2030-06-03", "guests": "2",
             "name": "Тест", "phone": "+380000000000"}


@pytest.fixture(autouse=True)
def _fresh_logs(monkeypatch):
    for name, event in (("_stale_log", "backoffice_stale_served"),
                        ("_unavailable_log", "backoffice_unavailable")):
        monkeypatch.setattr(api_client, name, api_client._RateLimitedWarning(
            event, api_client._STALE_LOG_INTERVAL if name == "_stale_log" else 1.0))
    if hasattr(api_client, "_retry_log"):
        monkeypatch.setattr(api_client, "_retry_log",
                            api_client._RateLimitedWarning("backoffice_retry"))


@pytest.fixture
def waited(monkeypatch):
    """Підміна api_client._sleep: список секунд, які клієнт «прочекав»."""
    out = []
    monkeypatch.setattr(api_client, "_sleep", out.append)
    return out


def busy_then(statuses, retry_after="1"):
    """Перші len(statuses) звернень по кожному шляху — ці коди з Retry-After, далі —
    звичайна відповідь default_route (свіже значення)."""
    seen = {}

    def route(method, path, params, hdrs, body):
        n = seen[path] = seen.get(path, 0) + 1
        if n <= len(statuses):
            return StubResponse(statuses[n - 1], {"error": "busy", "retry_after": 1},
                                {"Retry-After": retry_after})
        return default_route(method, path, params, hdrs, body)
    return route


def _stale_apartment():
    """Старе значення квартири A в кеші, старше _STALE_QUIET_S (як 27.10 17:02)."""
    old = dict(STUB_APARTMENTS[0])
    old["name"] = "Старе ім'я"
    key = ("apartment", "apt-uuid-a", "ua")
    api_client._ttl_cache[key] = (api_client.time.time() - 3600, old)
    return key


def _warnings(caplog):
    return [r for r in caplog.records if r.levelno >= logging.WARNING]


# --- 429 Retry-After 1, далі 200 -------------------------------------------------------

READS = [
    ("apartments", lambda c: c.list_apartments()),
    ("apartments/apt-uuid-a", lambda c: c.get_apartment("apt-uuid-a")),
    ("availability/apt-uuid-a", lambda c: c.get_availability("apt-uuid-a")),
    ("rates/apt-uuid-a", lambda c: c.get_rates("apt-uuid-a")),
    ("quote/apt-uuid-a", lambda c: c.get_quote("apt-uuid-a", "2030-06-01", "2030-06-03", 2)),
]


@pytest.mark.parametrize("path,call", READS, ids=["apartments", "apartment", "availability", "rates", "quote"])
def test_read_429_then_200_retried_once(real_client, waited, caplog, path, call):
    real_client.session.route = busy_then([429])
    with caplog.at_level(logging.INFO, logger="api_client"):
        value = call(real_client.client)
    assert value == real_client.session.responses[-1].json()   # свіже значення, не виняток
    assert real_client.session.responses[-1].status_code == 200
    assert real_client.session.calls[path] == 2
    assert waited == [1]
    assert _warnings(caplog) == []
    assert any("backoffice_retry" in r.getMessage() and "ok=1" in r.getMessage()
               and r.levelno == logging.INFO for r in caplog.records)


def test_apartment_page_gets_fresh_value_without_warning(client, real_client, waited, caplog):
    """Сценарій 27.10: старе значення квартири в кеші, стеля відмовила раз — сторінка
    отримує свіже, у журналі ні WARNING, ні backoffice_stale_served."""
    _stale_apartment()
    real_client.session.route = busy_then([429])
    with caplog.at_level(logging.INFO, logger="api_client"):
        r = client.get("/apartment/apt-uuid-a")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    assert "Апартаменти A" in html and "Старе ім'я" not in html
    assert _warnings(caplog) == []
    assert not any("backoffice_stale_served" in rec.getMessage() for rec in caplog.records)


def test_group_quote_429_then_200(real_client, waited):
    def route(method, path, params, hdrs, body):
        if path == "quote-group" and real_client.session.calls[path] == 1:
            return StubResponse(429, {"error": "busy"}, {"Retry-After": "1"})
        if path == "quote-group":
            return StubResponse(200, {"rooms": [], "total": 1})
        return default_route(method, path, params, hdrs, body)
    real_client.session.route = route
    out = real_client.client.get_group_quote(["apt-uuid-a", "apt-uuid-b"],
                                             "2030-06-01", "2030-06-03", [2, 2])
    assert out == {"rooms": [], "total": 1}
    assert real_client.session.calls["quote-group"] == 2
    assert waited == [1]


# --- 429, потім знову 429 ----------------------------------------------------------------

def test_read_429_twice_serves_stale_with_warning(real_client, waited, caplog):
    key = _stale_apartment()
    real_client.session.route = busy_then([429, 429])
    with caplog.at_level(logging.INFO, logger="api_client"):
        value = real_client.client.get_apartment("apt-uuid-a")
    assert value["name"] == "Старе ім'я"
    assert real_client.session.calls["apartments/apt-uuid-a"] == 2   # рівно один повтор
    assert waited == [1]
    msgs = [r.getMessage() for r in _warnings(caplog)]
    assert any(m.startswith("backoffice_unavailable status=429") for m in msgs)
    assert any(m.startswith("backoffice_stale_served") for m in msgs)
    assert api_client._ttl_cache[key][1]["name"] == "Старе ім'я"


# --- без повтору ---------------------------------------------------------------------------

def test_read_503_not_retried(real_client, waited, caplog):
    _stale_apartment()
    real_client.session.route = busy_then([503])
    with caplog.at_level(logging.INFO, logger="api_client"):
        value = real_client.client.get_apartment("apt-uuid-a")
    assert value["name"] == "Старе ім'я"
    assert real_client.session.calls["apartments/apt-uuid-a"] == 1
    assert waited == []
    assert any(r.getMessage().startswith("backoffice_unavailable status=503")
               for r in _warnings(caplog))


@pytest.mark.parametrize("retry_after", ["5", "3", None])
def test_read_429_long_or_missing_retry_after_not_retried(real_client, waited, caplog,
                                                          retry_after):
    real_client.session.route = busy_then([429], retry_after=retry_after)
    with caplog.at_level(logging.INFO, logger="api_client"):
        with pytest.raises(api_client.BackofficeUnavailable):
            real_client.client.list_apartments()
    assert real_client.session.calls["apartments"] == 1
    assert waited == []
    assert any(r.getMessage().startswith("backoffice_unavailable status=429")
               for r in _warnings(caplog))


def test_coupon_check_429_not_retried(real_client, waited):
    real_client.session.route = busy_then([429])
    with pytest.raises(api_client.BackofficeUnavailable):
        real_client.client.check_coupon("+380000000000")
    assert real_client.session.calls["coupon/check"] == 1
    assert waited == []


def test_book_429_unchanged(client, real_client, waited):
    """POST /book на 429 — як раніше: одна спроба, без паузи, гостю flash."""
    real_client.session.route = busy_then([429])
    r = client.post("/book/apt-uuid-a", data=BOOK_FORM)
    assert r.status_code == 302
    assert real_client.session.calls["book"] == 1
    assert waited == []


# --- пауза поза слотом стелі ---------------------------------------------------------------

def test_retry_pause_outside_ceiling_slot(real_client, monkeypatch):
    """Під час паузи слот стелі вільний: інший потік не чекає сплячого."""
    api_client.set_slots(1, 0.01)
    free_during_sleep = []

    def sleep(s):
        with api_client._slots.slot("probe"):         # зайнятий слот → BackofficeBusy
            free_during_sleep.append(s)
    monkeypatch.setattr(api_client, "_sleep", sleep)
    real_client.session.route = busy_then([429])
    assert real_client.client.list_apartments()
    assert free_during_sleep == [1]
