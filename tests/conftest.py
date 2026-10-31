"""
Test fixtures for aparts-web.
Uses a MockBackofficeClient — no real HTTP calls to backoffice.
StubSession / real_client (spec 002) — the REAL BackofficeClient (its caches, the
ceiling, 429/503 handling) with only the HTTP session replaced; no network either.
"""
import copy
import json as _json
import threading
import time
from collections import Counter
from urllib.parse import urlsplit

import pytest
import requests
from requests.structures import CaseInsensitiveDict

import api_client
from app import app as flask_app
from api_client import set_client


class MockBackofficeClient:
    """In-memory mock of BackofficeClient for tests."""

    def __init__(self):
        self.apartments = [
            {
                "id": "apt-uuid-a",
                "code": "A",
                "name": "Апартаменти A",
                "price": 1490,
                "amenities": ["WiFi", "Кухня"],
                "description": "Тест опис",
                "photos": [],
            },
            {
                "id": "apt-uuid-b",
                "code": "B",
                "name": "Апартаменти B",
                "price": 1850,
                "amenities": ["WiFi", "Джакузі"],
                "description": "",
                "photos": [],
            },
        ]
        self.blocked: list = []
        self.last_booking: dict = {}
        self.booking_result: dict = {
            "booking_id": "test1234",
            "payment_url": "https://pay.monobank.ua/test",
            "total": 5220,
            "nights": 3,
            "base_amount": 4470,
            "tourist_tax": 750,
        }
        self.booking_status: dict = {
            "id": "test1234",
            "status": "paid",
            "apartment": "A",
            "apartment_id": "apt-uuid-a",
            "check_in": "2026-05-01",
            "check_out": "2026-05-04",
            "nights": 3,
            "total": 5220,
            "booking_number": "W0501A",
        }
        self.raise_on_book: str = ""  # set to BackofficeError message to simulate error
        self.quote_result = {
            "nights": 2, "base_amount": 4300, "tourist_tax": 192, "total": 4492,
            "tax_per_night": 48, "uniform": False, "min_night": 2000, "max_night": 2300,
            "only_one_left": False,
            "nightly": [{"date": "2030-06-01", "price": 2000},
                        {"date": "2030-06-02", "price": 2300}],
        }  # set to "invalid" for 400, or an Exception instance for 503
        self.rates_result = {
            "rates": {"2030-06-01": 2000, "2030-06-02": 2300},
        }  # set to None for 404, or an Exception instance for 503
        self.coupon_result = {"eligible": False}  # or Exception instance for 503
        self.last_coupon_phone = None
        self.last_coupon_client_ip = None
        self.last_quote: dict = {}

    def list_apartments(self, lang="ua"):
        return self.apartments

    def get_apartment(self, apt_id, lang="ua"):
        for a in self.apartments:
            if a["id"] == apt_id:
                return a
        return None

    def get_availability(self, apt_id):
        return self.blocked

    def get_quote(self, apt_id, check_in, check_out, guests=1, coupon_phone="",
                  client_ip=""):
        self.last_quote = dict(apt_id=apt_id, check_in=check_in,
                               check_out=check_out, guests=guests,
                               coupon_phone=coupon_phone, client_ip=client_ip)
        if self.quote_result == "invalid":
            return None
        if isinstance(self.quote_result, Exception):
            raise self.quote_result
        return self.quote_result

    def get_rates(self, apt_id):
        if self.rates_result is None:
            return None
        if isinstance(self.rates_result, Exception):
            raise self.rates_result
        return self.rates_result

    def check_coupon(self, phone, client_ip=""):
        self.last_coupon_phone = phone
        self.last_coupon_client_ip = client_ip
        if isinstance(self.coupon_result, Exception):
            raise self.coupon_result
        return self.coupon_result

    def create_booking(self, apartment_id, check_in, check_out, guests, name,
                       phone, coupon_phone="", coupon_expected="", idempotency_key=""):
        from api_client import BackofficeError
        if self.raise_on_book:
            raise BackofficeError(self.raise_on_book)
        self.last_booking = dict(
            apartment_id=apartment_id, check_in=check_in, check_out=check_out,
            guests=guests, name=name, phone=phone, coupon_phone=coupon_phone,
            coupon_expected=coupon_expected, idempotency_key=idempotency_key,
        )
        return self.booking_result

    def get_booking(self, booking_id):
        if booking_id == self.booking_status.get("id"):
            return self.booking_status
        return None


@pytest.fixture
def mock_client():
    c = MockBackofficeClient()
    set_client(c)
    yield c
    set_client(None)


@pytest.fixture(autouse=True)
def _no_inline_critical_css(request, monkeypatch):
    """Інлайн-CSS першого екрана (static/critical/*.css) несе імена майже всіх класів —
    і `"has-collage" in html` став би істинним завжди. Тести розмітки бачать сторінку без
    нього; саму вставку стереже tests/test_critical_css.py (маркер real_critical_css)."""
    if request.node.get_closest_marker("real_critical_css"):
        return
    import app as app_module
    monkeypatch.setattr(app_module, "critical_css", lambda group="other": "")


@pytest.fixture
def app(mock_client):
    flask_app.config["TESTING"] = True
    flask_app.config["WTF_CSRF_ENABLED"] = False
    yield flask_app


@pytest.fixture
def client(app):
    return app.test_client()


# ---------------------------------------------------------------------------
# StubSession — replaces requests.Session inside a real BackofficeClient
# ---------------------------------------------------------------------------

class StubResponse:
    """What BackofficeClient reads from requests.Response. A non-JSON body (str)
    makes .json() raise ValueError, as requests does on an HTML page."""

    request = None  # requests.HTTPError(response=...) reads response.request

    def __init__(self, status_code, body=None, headers=None,
                 content_type="application/json"):
        self.status_code = status_code
        self._body = body
        self.headers = CaseInsensitiveDict(headers or {})
        self.headers.setdefault("Content-Type", content_type)
        self.json_calls = 0

    def json(self):
        self.json_calls += 1
        if isinstance(self._body, (str, bytes)):
            raise ValueError("stub body is not JSON")
        return copy.deepcopy(self._body)   # a fresh object per parse, like the wire

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} stub backoffice", response=self)


STUB_APARTMENTS = [
    {"id": "apt-uuid-a", "code": "A", "name": "Апартаменти A", "price": 1490,
     "amenities": ["WiFi", "Кухня"], "description": "Тест опис", "photos": []},
    {"id": "apt-uuid-b", "code": "B", "name": "Апартаменти B", "price": 1850,
     "amenities": ["WiFi", "Джакузі"], "description": "", "photos": []},
]
STUB_QUOTE = {
    "nights": 2, "base_amount": 4300, "tourist_tax": 192, "total": 4492,
    "tax_per_night": 48, "uniform": False, "min_night": 2000, "max_night": 2300,
    "only_one_left": False,
    "nightly": [{"date": "2030-06-01", "price": 2000},
                {"date": "2030-06-02", "price": 2300}],
}
STUB_BOOKING = {
    "id": "test1234", "status": "paid", "apartment": "A", "apartment_id": "apt-uuid-a",
    "check_in": "2030-06-01", "check_out": "2030-06-03", "nights": 2, "total": 4492,
    "booking_number": "W0601A",
}


def default_route(method, path, params, headers, body):
    """Answers like MockBackofficeClient. path — the part after /api/public/."""
    parts = path.split("/")
    if method == "GET":
        if parts == ["apartments"]:
            return StubResponse(200, STUB_APARTMENTS)
        if len(parts) == 2:
            kind, apt_id = parts
            if kind == "apartments":
                apt = next((a for a in STUB_APARTMENTS if a["id"] == apt_id), None)
                return StubResponse(200, apt) if apt else StubResponse(404, {"error": "not_found"})
            if kind == "availability":
                return StubResponse(200, [])
            if kind == "rates":
                return StubResponse(200, {"rates": {"2030-06-01": 2000, "2030-06-02": 2300}})
            if kind == "quote":
                q = copy.deepcopy(STUB_QUOTE)
                if params.get("coupon_phone"):
                    q.update(coupon_discount=-500, coupon_applied=True, total=3992)
                return StubResponse(200, q)
            if kind == "booking":
                if apt_id == STUB_BOOKING["id"]:
                    return StubResponse(200, STUB_BOOKING)
                return StubResponse(404, {"error": "not_found"})
    if method == "POST":
        if parts == ["coupon", "check"]:
            return StubResponse(200, {"eligible": False})
        if parts == ["book"]:
            return StubResponse(200, {"booking_id": "test1234",
                                      "payment_url": "https://pay.monobank.ua/test",
                                      "total": 4492})
    return StubResponse(404, {"error": "not_found"})


class StubSession:
    """requests.Session stand-in. route(method, path, params, headers, body) ->
    StubResponse (or raises, e.g. requests.ConnectionError); path is relative to
    /api/public/. Counts calls per path and the peak of simultaneous calls;
    delay_s sleeps inside the call; gate (threading.Event) holds a call until set —
    that is how a test keeps a ceiling slot occupied. `entered` is set on every
    call start, so a test can wait until a holder is really inside."""

    def __init__(self, route=None, delay_s: float = 0.0, gate=None):
        self.route = route or default_route
        self.delay_s = delay_s
        self.gate = gate
        self.headers: dict = {}
        self.calls = Counter()          # path -> count
        self.requests: list = []        # (method, path, params, headers, body)
        self.responses: list = []       # StubResponse given out, in order
        self.raw_bodies: list = []      # bytes of every post(data=...), in order
        self.peak = 0
        self.entered = threading.Event()
        self._inflight = 0
        self._lock = threading.Lock()

    def get(self, url, params=None, headers=None, timeout=None):
        return self._call("GET", url, params, headers, None)

    def post(self, url, json=None, data=None, headers=None, timeout=None):
        """data= (bytes JSON, як шле create_booking) — у route йде розібраним, а
        сирі байти лишаються в raw_bodies для перевірки «повтор побайтно той самий»."""
        if data is not None:
            self.raw_bodies.append(data)
            json = _json.loads(data)
        return self._call("POST", url, None, headers, json)

    def total(self) -> int:
        return sum(self.calls.values())

    def _call(self, method, url, params, headers, body):
        path = urlsplit(url).path.split("/api/public/", 1)[-1].strip("/")
        params, headers = dict(params or {}), dict(headers or {})
        with self._lock:
            self.calls[path] += 1
            self.requests.append((method, path, params, headers, body))
            self._inflight += 1
            self.peak = max(self.peak, self._inflight)
            gate = self.gate      # read BEFORE `entered`: a test may clear it right after
        self.entered.set()
        try:
            if gate is not None:
                gate.wait()
            if self.delay_s:
                time.sleep(self.delay_s)
            resp = self.route(method, path, params, headers, body)
            with self._lock:
                self.responses.append(resp)
            return resp
        finally:
            with self._lock:
                self._inflight -= 1


def _reset_limits():
    api_client._ttl_cache.clear()
    if hasattr(api_client, "set_slots"):
        api_client.set_slots(0, 2.0)
    if hasattr(api_client, "set_quote_cache"):
        api_client.set_quote_cache(0)


class RealClient:
    """Handle returned by the real_client fixture: .client, .session, rebuild()."""

    def __init__(self, session: StubSession):
        self.session = session
        self.client = None
        self.rebuild()

    def rebuild(self):
        """New BackofficeClient over the same session (e.g. after changing
        Config.BACKOFFICE_URL), injected via set_client."""
        c = api_client.BackofficeClient()
        c._session = self.session
        self.client = c
        set_client(c)
        return c


@pytest.fixture
def real_client(app, monkeypatch):
    """Real BackofficeClient over a StubSession, injected over mock_client
    (the Flask `client` fixture keeps working). Caches, ceiling and quote cache
    are reset before and after."""
    _reset_limits()
    rc = RealClient(StubSession())
    yield rc
    _reset_limits()
