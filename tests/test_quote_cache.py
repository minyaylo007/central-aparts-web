"""
Кеш котирування (спека 002). US1 — без налаштувань усе як до фічі: кожне котирування
йде в бек-офіс. US2 — кеш котирування без купона.
Справжній BackofficeClient поверх StubSession (tests/conftest.py), без мережі.

Не critical (13.10.2026): фіча вимкнена за замовчуванням (QUOTE_CACHE_TTL=0). Один
тест на гілку — 19 → 8; купон повз кеш і «помилка не кешується» — лишились, бо це ціна.
"""
import copy
import time

import pytest

import api_client
from api_client import get_client
from conftest import STUB_QUOTE, StubResponse, default_route

QUOTE_URL = "/api/quote/apt-uuid-a?check_in=2030-06-01&check_out=2030-06-03&guests=2"
QUOTE_PATH = "quote/apt-uuid-a"
XFF = {"X-Forwarded-For": "198.51.100.23"}


def _q(client, apt="apt-uuid-a", **kw):
    params = {"check_in": "2030-06-01", "check_out": "2030-06-03", "guests": "2", **kw}
    return client.get(f"/api/quote/{apt}", query_string=params, headers=XFF)


def _quote_calls(session) -> int:
    return sum(n for path, n in session.calls.items() if path.startswith("quote/"))


class _Clock:
    """Керований time.monotonic для api_client; решта time — справжня."""

    def __init__(self):
        self.t = 1000.0

    def monotonic(self):
        return self.t

    def __getattr__(self, name):
        return getattr(time, name)


@pytest.fixture
def clock(monkeypatch):
    c = _Clock()
    monkeypatch.setattr(api_client, "time", c)
    return c


def _quote_route(answer):
    def route(method, path, params, headers, body):
        if path.startswith("quote/"):
            return answer()
        return default_route(method, path, params, headers, body)
    return route


# --- US1 ---------------------------------------------------------------------

def test_us1_every_quote_hits_backoffice(client, real_client):
    session = real_client.session
    before = session.calls[QUOTE_PATH]
    for _ in range(2):
        r = client.get(QUOTE_URL)
        assert r.status_code == 200
        assert r.get_json()["total"] == 4492
    assert session.calls[QUOTE_PATH] - before == 2
    assert api_client.quote_cache_status() == "off"


# --- US2 ---------------------------------------------------------------------

def test_us2_hundred_identical_one_call(client, real_client):
    api_client.set_quote_cache(30)
    first = _q(client)
    assert first.status_code == 200
    assert first.get_json() == STUB_QUOTE
    for _ in range(99):
        r = _q(client)
        assert r.status_code == 200
        assert r.get_json() == STUB_QUOTE
    assert real_client.session.calls[QUOTE_PATH] == 1


def test_us2_key_components(client, real_client):
    """Квартира, дати й гості — у ключі (інший — промах, той самий — влучання);
    мова — ні."""
    api_client.set_quote_cache(30)
    assert _q(client).status_code == 200
    calls = 1
    for variant in ({"apt": "apt-uuid-b"}, {"check_in": "2030-06-02"},
                    {"check_out": "2030-06-04"}, {"guests": "3"}):
        assert _q(client, **variant).status_code == 200          # інший ключ — промах
        calls += 1
        assert _quote_calls(real_client.session) == calls, variant
        assert _q(client, **variant).status_code == 200          # той самий — влучання
        assert _quote_calls(real_client.session) == calls, variant
    client.get("/lang/en")
    assert _q(client).status_code == 200
    assert _quote_calls(real_client.session) == calls


def test_us2_coupon_bypasses_cache(client, real_client):
    api_client.set_quote_cache(30)
    assert "coupon_discount" not in _q(client).get_json()     # теплий кеш, 1 звернення
    for _ in range(100):
        r = _q(client, coupon_phone="+380000000000")
        assert r.status_code == 200
        assert r.get_json()["coupon_discount"] == -500
    session = real_client.session
    assert session.calls[QUOTE_PATH] == 101
    coupon_reqs = [req for req in session.requests if req[2].get("coupon_phone")]
    assert len(coupon_reqs) == 100
    assert all(req[2]["coupon_phone"] == "+380000000000" for req in coupon_reqs)
    assert all(req[3].get("X-Forwarded-For") for req in coupon_reqs)
    r = _q(client)                                            # анонімне — досі влучання,
    assert r.get_json() == STUB_QUOTE                         # купонна відповідь у кеш не лягла
    assert session.calls[QUOTE_PATH] == 101


def test_us2_errors_not_cached(client, real_client):
    api_client.set_quote_cache(30)
    real_client.session.route = _quote_route(lambda: StubResponse(500, {"error": "boom"}))
    for i in range(3):
        r = _q(client)
        assert r.status_code == 503
        assert r.get_json() == {"error": "unavailable"}
        assert real_client.session.calls[QUOTE_PATH] == i + 1


def test_us2_expiry(client, real_client, clock):
    api_client.set_quote_cache(30)
    quote = copy.deepcopy(STUB_QUOTE)
    real_client.session.route = _quote_route(lambda: StubResponse(200, quote))
    assert _q(client).get_json()["total"] == 4492
    clock.t += 29                                             # ще свіжа — влучання
    assert _q(client).get_json()["total"] == 4492
    assert real_client.session.calls[QUOTE_PATH] == 1
    quote["total"] = 5000                                     # ціну змінили в адмінці
    clock.t += 1                                              # рівно ttl — прострочена
    assert _q(client).get_json()["total"] == 5000
    assert real_client.session.calls[QUOTE_PATH] == 2
    assert _q(client).get_json()["total"] == 5000             # новий запис замінив старий
    assert real_client.session.calls[QUOTE_PATH] == 2


def test_us2_bounded(client, real_client):
    api_client.set_quote_cache(30, max_entries=3)
    for day in ("01", "02", "03", "04"):
        assert _q(client, check_in=f"2030-06-{day}", check_out="2030-06-10").status_code == 200
    assert len(api_client._quote_cache) == 3
    assert _quote_calls(real_client.session) == 4
    _q(client, check_in="2030-06-01", check_out="2030-06-10")   # перший витіснено
    assert _quote_calls(real_client.session) == 5
    _q(client, check_in="2030-06-04", check_out="2030-06-10")   # останній — влучання
    assert _quote_calls(real_client.session) == 5


def test_us2_returns_copy(real_client):
    """Гість, що змінив свій словник, не міняє ціну наступному."""
    api_client.set_quote_cache(30)
    c = get_client()
    q1 = c.get_quote("apt-uuid-a", "2030-06-01", "2030-06-03", "2")
    q1["total"] = 1
    q1["nightly"].append({"date": "2030-06-03", "price": 1})
    q2 = c.get_quote("apt-uuid-a", "2030-06-01", "2030-06-03", "2")
    assert q2 == STUB_QUOTE
    q2["nightly"].clear()
    assert c.get_quote("apt-uuid-a", "2030-06-01", "2030-06-03", "2") == STUB_QUOTE
    assert real_client.session.calls[QUOTE_PATH] == 1
