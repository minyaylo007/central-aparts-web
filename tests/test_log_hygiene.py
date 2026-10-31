"""
Журнал без даних гостя на збоях бек-офісу (analyze 002, L2). Текст винятку requests
несе повний URL: у котируванні це coupon_phone у query — `logger.error(..., exc_info=True)`
друкував телефон у трейсбек. Тут справжній BackofficeClient, у якого мережу замінено
сесією, що падає так, як падає справжній requests: HTTPError з URL на 5xx і
ConnectionError з ланцюжком urllib3 (теж з URL). Лише синтетика.
"""
import logging

import pytest
import requests
from urllib3.exceptions import MaxRetryError, NewConnectionError

import api_client
from api_client import BackofficeClient, set_client
from config import Config

# critical: телефон гостя не має потрапити в журнал
pytestmark = pytest.mark.critical

PHONE = "+380000000000"
PHONE_DIGITS = "380000000000"
GUEST_IP = "198.51.100.23"
PROXY_IP = "203.0.113.7"
BASE = "https://bo.test"


class _FailingSession:
    """Замість requests.Session: кожне звернення до бек-офісу — збій."""

    def __init__(self, mode: str):
        self.mode = mode
        self.headers: dict = {}

    def get(self, url, params=None, headers=None, timeout=None):
        return self._fail("GET", url, params)

    def post(self, url, json=None, data=None, headers=None, timeout=None):
        return self._fail("POST", url, None)

    def _fail(self, method, url, params):
        full = requests.Request(method, url, params=params).prepare().url
        if self.mode == "connection":
            # так requests.adapters загортає MaxRetryError: обидва тексти з URL і query
            path = full[len(BASE):]
            try:
                raise MaxRetryError(None, path, reason=NewConnectionError(
                    None, "Failed to establish a new connection"))
            except MaxRetryError as e:
                raise requests.ConnectionError(e)
        r = requests.Response()
        r.status_code, r.reason, r.url = 500, "Internal Server Error", full
        return r


@pytest.fixture(params=["http500", "connection"])
def failing(request, client, monkeypatch):
    monkeypatch.setattr(Config, "TEST_APARTMENT_IDS", ["apt-uuid-test"])
    real = BackofficeClient()
    real.base = BASE
    real._session = _FailingSession(request.param)
    set_client(real)                       # поверх mock_client; розбирає conftest
    api_client._ttl_cache.clear()
    yield request.param
    api_client._ttl_cache.clear()


XFF = {"X-Forwarded-For": f"{GUEST_IP}, {PROXY_IP}"}
QUOTE = ("/api/quote/apt-uuid-a?check_in=2030-06-01&check_out=2030-06-03&guests=2"
         "&coupon_phone=%2B380000000000")
BOOK_FORM = {"check_in": "2030-06-01", "check_out": "2030-06-03", "guests": "2",
             "name": "Тест", "phone": PHONE, "coupon_phone": PHONE, "coupon_expected": "1"}

# (маршрут, запит, події в журналі, код відповіді гостю — як і до правки)
ROUTES = [
    ("quote", lambda c: c.get(QUOTE, headers=XFF), ["quote_api_error"], 503),
    ("coupon", lambda c: c.post("/api/coupon/check", json={"phone": PHONE}, headers=XFF),
     ["coupon_check_api_error"], 503),
    ("availability", lambda c: c.get("/api/availability/apt-uuid-a", headers=XFF),
     ["availability_api_error"], 503),
    ("rates", lambda c: c.get("/api/rates/apt-uuid-a", headers=XFF), ["rates_api_error"], 503),
    ("home", lambda c: c.get("/?test=1", headers=XFF),
     ["home_list_apartments_error", "home_test_apartment_error"], 200),
    ("book", lambda c: c.post("/book/apt-uuid-a", data=BOOK_FORM, headers=XFF),
     ["book_unexpected_error"], 302),
    ("sitemap", lambda c: c.get("/sitemap.xml", headers=XFF), ["sitemap_apartments_failed"], 200),
]


def _assert_no_guest_data(text: str) -> None:
    # хвіст телефону довший за 4 цифри — уже витік (у проєкті пишуть лише tail=<4>)
    for i in range(len(PHONE_DIGITS) - 4):
        assert PHONE_DIGITS[i:i + 5] not in text
    for needle in (GUEST_IP, PROXY_IP, "%2B", "Тест", "bo.test", "url:", "?", "Traceback"):
        assert needle not in text


@pytest.mark.parametrize("name,call,events,status", ROUTES, ids=[r[0] for r in ROUTES])
def test_backoffice_failure_logs_no_guest_data(client, failing, caplog, name, call, events, status):
    caplog.set_level(logging.DEBUG)
    r = call(client)
    assert r.status_code == status
    _assert_no_guest_data(caplog.text)
    err, code = ("HTTPError", "500") if failing == "http500" else ("ConnectionError", "none")
    for event in events:
        [rec] = [x for x in caplog.records if x.getMessage().split(" ", 1)[0] == event]
        assert f" err={err} status={code} " in rec.getMessage()
        assert rec.exc_info is None
        # місце збою лишається для відладки — ім'я файлу й рядок, без тексту винятку
        assert " at=api_client.py:" in rec.getMessage()


def test_log_line_keeps_our_frame_for_non_requests_errors(client, mock_client, caplog):
    """Не requests (тут — мок бек-офісу з tests/): у рядку тип і найглибший кадр коду
    сайту (app.py, api_client.py…, не tests/ і не site-packages), текст винятку — ні."""
    caplog.set_level(logging.DEBUG)
    mock_client.quote_result = RuntimeError(f"boom {PHONE}")
    r = client.get(QUOTE, headers=XFF)
    assert r.status_code == 503
    [rec] = [x for x in caplog.records if x.getMessage().startswith("quote_api_error ")]
    assert rec.getMessage().startswith("quote_api_error err=RuntimeError status=none at=app.py:")
    _assert_no_guest_data(caplog.text)
