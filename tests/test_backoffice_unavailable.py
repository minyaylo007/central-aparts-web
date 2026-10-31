"""
Бек-офіс «тимчасово недоступний» (спека 002, US5): 429/503 на будь-якому зверненні —
BackofficeUnavailable за кодом і Retry-After, тіло не читається (HTML техробіт зараз,
JSON пізніше). JSON-ручки — 503 busy з Retry-After бек-офісу; /book — flash «за N
хвилин» з датами й гостями; сторінки — устареле з кешу. Діє без налаштувань.
"""
from urllib.parse import parse_qs, urlsplit

import pytest

import api_client
import app as app_module
from app import app as flask_app
from conftest import StubResponse, default_route
from translations import get_t

# critical: 429/503 бек-офісу: доступність сторінок і /book
pytestmark = pytest.mark.critical

HTML = "<html><body>Технічні роботи</body></html>"
QUOTE_URL = "/api/quote/apt-uuid-a?check_in=2030-06-01&check_out=2030-06-03&guests=2"
BOOK_FORM = {"check_in": "2030-06-01", "check_out": "2030-06-03", "guests": "2",
             "name": "Тест", "phone": "+380000000000"}
JSON_ENDPOINTS = [
    ("quote", lambda c: c.get(QUOTE_URL)),
    ("availability", lambda c: c.get("/api/availability/apt-uuid-a")),
    ("rates", lambda c: c.get("/api/rates/apt-uuid-a")),
    ("coupon", lambda c: c.post("/api/coupon/check", json={"phone": "+380000000000"})),
]


def maintenance(status=503, body="html", retry_after="300"):
    """Маршрут StubSession: УСІ шляхи — 429/503 + Retry-After + тіло техробіт."""
    headers = {"Cache-Control": "no-store"}
    if retry_after is not None:
        headers["Retry-After"] = retry_after

    def route(method, path, params, hdrs, body_):
        if body == "html":
            return StubResponse(status, HTML, headers, content_type="text/html")
        return StubResponse(status, {"error": "maintenance", "retry_after": 300}, headers)
    return route


VARIANTS = [(503, "html"), (503, "json"), (429, "html")]


@pytest.fixture(params=VARIANTS, ids=["503-html", "503-json", "429-html"])
def maint(request, real_client):
    status, body = request.param
    real_client.session.route = maintenance(status, body)
    return real_client.session


def _expire_ttl_cache():
    for key, (_, value) in list(api_client._ttl_cache.items()):
        api_client._ttl_cache[key] = (0, value)


def _flashes(client):
    with client.session_transaction() as s:
        return list(s.get("_flashes", []))


# --- JSON-ручки ------------------------------------------------------------------

def test_us5_json_endpoints(client, maint):
    for name, call in JSON_ENDPOINTS:
        before = maint.total()
        r = call(client)
        assert r.status_code == 503, name
        assert r.get_json() == {"error": "busy"}, name
        assert r.headers["Retry-After"] == "300", name
        assert maint.total() - before == 1, name          # без повторів


RETRY_AFTER_CASES = [(None, None), ("Wed, 21 Oct 2026 07:28:00 GMT", None), ("0", None),
                     ("-5", None), ("1.5", None), ("3601", None), ("1", 1), ("3600", 3600)]


@pytest.mark.parametrize("header,expected", RETRY_AFTER_CASES)
def test_us5_retry_after_values(client, real_client, header, expected):
    real_client.session.route = maintenance(retry_after=header)
    r = client.get(QUOTE_URL)
    assert r.status_code == 503
    assert r.headers["Retry-After"] == str(expected or 5)
    assert api_client._parse_retry_after(header) == expected


# --- /book -----------------------------------------------------------------------

def _assert_back_to_apartment(r):
    assert r.status_code == 302
    loc = urlsplit(r.headers["Location"])
    assert loc.path == "/apartment/apt-uuid-a"
    q = {k: v[0] for k, v in parse_qs(loc.query).items()}
    assert q == {"check_in": "2030-06-01", "check_out": "2030-06-03", "guests": "2", "book": "1"}


def test_us5_book_flash(client, maint):
    r = client.post("/book/apt-uuid-a", data=BOOK_FORM)
    _assert_back_to_apartment(r)
    assert _flashes(client) == [
        ("error", "Бронювання тимчасово недоступне — спробуйте за 5 хвилин.")]
    assert maint.calls["book"] == 1                       # бронь не повторюється


def test_us5_book_flash_en(client, real_client):
    real_client.session.route = maintenance()
    client.get("/lang/en")
    r = client.post("/book/apt-uuid-a", data=BOOK_FORM)
    _assert_back_to_apartment(r)
    assert _flashes(client) == [
        ("error", "Booking is temporarily unavailable — please try again in 5 minutes.")]
    assert real_client.session.calls["book"] == 1


def test_us5_book_flash_without_retry_after(client, real_client):
    real_client.session.route = maintenance(retry_after=None)
    r = client.post("/book/apt-uuid-a", data=BOOK_FORM)
    _assert_back_to_apartment(r)
    assert _flashes(client) == [
        ("error", "Бронювання тимчасово недоступне — спробуйте трохи пізніше.")]


@pytest.mark.parametrize("lang,seconds,phrase", [
    ("ua", 60, "за 1 хвилину."), ("ua", 61, "за 2 хвилини."), ("ua", 300, "за 5 хвилин."),
    ("ua", 720, "за 12 хвилин."), ("ua", 1260, "за 21 хвилину."), ("ua", 1320, "за 22 хвилини."),
    ("ua", 660, "за 11 хвилин."), ("ua", 3600, "за 60 хвилин."),
    ("en", 60, "in 1 minute."), ("en", 120, "in 2 minutes."),
])
def test_us5_book_minutes_forms(lang, seconds, phrase):
    text = app_module._booking_unavailable_text(get_t(lang), lang, seconds)
    assert text.endswith(phrase)
    assert app_module._booking_unavailable_text(get_t(lang), lang, None) == get_t(lang)(
        "booking_temporarily_unavailable")


# --- купон, сторінки, кеш, тіло ----------------------------------------------------

def test_us5_coupon_no_signal(client, maint):
    a = client.post("/api/coupon/check", json={"phone": "+380000000000"})
    b = client.post("/api/coupon/check", json={"phone": "+380000000001"})
    assert (a.status_code, a.get_data(), a.headers["Retry-After"]) == (
        b.status_code, b.get_data(), b.headers["Retry-After"])
    assert a.get_json() == {"error": "busy"}


def test_us5_pages(client, real_client, monkeypatch):
    monkeypatch.setitem(flask_app.config, "PROPAGATE_EXCEPTIONS", False)
    assert "Апартаменти A" in client.get("/").get_data(as_text=True)      # прогрів
    assert client.get("/apartment/apt-uuid-a").status_code == 200
    real_client.session.route = maintenance()
    _expire_ttl_cache()
    r = client.get("/")
    assert r.status_code == 200
    assert "Апартаменти A" in r.get_data(as_text=True)
    r = client.get("/apartment/apt-uuid-a")
    assert r.status_code == 200
    assert "Апартаменти A" in r.get_data(as_text=True)
    api_client._ttl_cache.clear()                          # процес стартував у вікні
    r = client.get("/")
    assert r.status_code == 200
    assert "Апартаменти A" not in r.get_data(as_text=True)
    assert client.get("/apartment/apt-uuid-a").status_code == 500
    assert client.get("/booking-confirmed?ref=test1234").status_code == 200
    assert real_client.session.calls["booking/test1234"] == 1


def test_us5_not_cached(client, real_client):
    api_client.set_quote_cache(30)
    real_client.session.route = maintenance()
    assert client.get(QUOTE_URL).status_code == 503
    real_client.session.route = default_route
    assert client.get(QUOTE_URL).status_code == 200
    assert real_client.session.calls["quote/apt-uuid-a"] == 2


def test_us5_html_body_never_parsed(client, real_client):
    real_client.session.route = maintenance(body="html")
    for _, call in JSON_ENDPOINTS:
        call(client)
    client.post("/book/apt-uuid-a", data=BOOK_FORM)
    client.get("/")
    client.get("/booking-confirmed?ref=test1234")
    responses = real_client.session.responses
    assert len(responses) >= 7
    assert all(resp.json_calls == 0 for resp in responses)
