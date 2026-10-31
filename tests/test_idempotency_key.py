"""
Ключ ідемпотентності в POST /api/public/book (спека 107 бек-офісу, 03.10.2026).

Бек-офіс тримає замок на «перевірити + створити» і з заголовком Idempotency-Key віддає
на повтор ту саму бронь і той самий рахунок. Сайт: один UUID на спробу бронювання
(main.js кладе новий у приховане поле на кожен submit; нема або кривий — свій uuid4),
повтори тієї самої спроби — з тим самим ключем і побайтно тим самим тілом. 409
idempotency_in_progress і 503 — повтор через Retry-After, ≤ 2 повторів і ≤ 10 с
очікування сумарно; mismatch не повторюється. Справжній BackofficeClient над
StubSession, sleep підмінено — тести не чекають. Лише синтетика (телефон з кодом 00).
13.10.2026: один тест на гілку — 36 → 21 (зрізано лише повтори параметрів).
"""
import logging
import re
import uuid
from urllib.parse import parse_qs, urlsplit

import pytest

import api_client
from conftest import StubResponse, default_route
from config import Config
from translations import get_t

# critical: бронь і оплата — друга бронь і другий рахунок на обрив звʼязку
pytestmark = pytest.mark.critical

PHONE = "+380000000000"
NAME = "Тест Ідемпотентний"
KEY = "6f1c2b9e-3d4a-4f5b-8c7d-0e1f2a3b4c5d"
BOOK_FORM = {"check_in": "2030-06-01", "check_out": "2030-06-03", "guests": "2",
             "name": NAME, "phone": PHONE, "idempotency_key": KEY}
UUID4 = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}")
OK = {"booking_id": "test1234", "payment_url": "https://pay.monobank.ua/test", "total": 4492}


def book_route(*answers):
    """Маршрут StubSession: POST /book віддає answers по черзі (останній — далі
    назавжди), решта шляхів — як default_route."""
    queue = list(answers)

    def route(method, path, params, headers, body):
        if method == "POST" and path == "book":
            return queue.pop(0) if len(queue) > 1 else queue[0]
        return default_route(method, path, params, headers, body)
    return route


def r409(error, retry_after=None):
    return StubResponse(409, {"error": error},
                        {"Retry-After": retry_after} if retry_after is not None else {})


def r503(retry_after=None):
    return StubResponse(503, {"error": "busy"},
                        {"Retry-After": retry_after} if retry_after is not None else {})


@pytest.fixture
def sleeps(monkeypatch):
    """Підміна api_client._sleep: список секунд, які клієнт «прочекав»."""
    waited = []
    monkeypatch.setattr(api_client, "_sleep", waited.append)
    return waited


def _book_calls(session):
    return [(h, b) for (m, p, _, h, b) in session.requests if m == "POST" and p == "book"]


def _keys(session):
    return [h.get("Idempotency-Key") for h, _ in _book_calls(session)]


def _flashes(client):
    with client.session_transaction() as s:
        return list(s.get("_flashes", []))


def _back_query(r):
    assert r.status_code == 302
    loc = urlsplit(r.headers["Location"])
    assert loc.path == "/apartment/apt-uuid-a"
    return {k: v[0] for k, v in parse_qs(loc.query).items()}


# --- ключ: звідки береться ---------------------------------------------------------

def test_form_key_goes_to_header_as_is(client, real_client, sleeps):
    r = client.post("/book/apt-uuid-a", data=BOOK_FORM)
    assert r.status_code == 302 and r.headers["Location"] == OK["payment_url"]
    assert _keys(real_client.session) == [KEY]
    assert sleeps == []


@pytest.mark.parametrize("raw,reason", [
    (None, "missing"),
    ("not-a-uuid", "invalid"),
    (KEY.upper(), "invalid"),                    # лише канонічний вигляд малими
])
def test_bad_or_missing_form_key_server_makes_own(client, real_client, sleeps, caplog, raw, reason):
    caplog.set_level(logging.INFO, logger="app")
    form = dict(BOOK_FORM)
    if raw is None:
        form.pop("idempotency_key")
    else:
        form["idempotency_key"] = raw
    client.post("/book/apt-uuid-a", data=form)
    [key] = _keys(real_client.session)
    assert UUID4.fullmatch(key), key
    assert key != KEY
    lines = [r.getMessage() for r in caplog.records if r.name == "app"]
    assert f"book_idempotency_key_generated reason={reason}" in lines
    if raw:
        assert all(raw.strip() not in line for line in lines)   # значення поля — не в журнал


def test_server_key_is_per_attempt_not_per_session(client, real_client, sleeps):
    form = {k: v for k, v in BOOK_FORM.items() if k != "idempotency_key"}
    client.post("/book/apt-uuid-a", data=form)
    client.post("/book/apt-uuid-a", data=form)
    a, b = _keys(real_client.session)
    assert a != b


def test_client_without_key_argument_still_sends_one(real_client, sleeps):
    """Виклик create_booking без ключа (Lambda-шлях і будь-хто інший) — свій uuid4."""
    real_client.client.create_booking("apt-uuid-a", "2030-06-01", "2030-06-03", 2, NAME, PHONE)
    [key] = _keys(real_client.session)
    assert UUID4.fullmatch(key)


def test_template_has_hidden_key_field_inside_booking_form(client):
    html = client.get("/apartment/apt-uuid-a").get_data(as_text=True)
    form = html[html.index('id="booking-form"'):]
    form = form[:form.index("</form>")]
    assert re.search(r'<input type="hidden" name="idempotency_key" id="f-idempotency-key" value="">', form)


# --- повтори: той самий ключ і ті самі байти -----------------------------------------

@pytest.mark.parametrize("first", [r409("idempotency_in_progress", "2"), r503("2")],
                         ids=["409-in-progress", "503-busy"])
def test_retry_same_key_same_bytes(client, real_client, sleeps, first):
    real_client.session.route = book_route(first, StubResponse(200, OK))
    form = dict(BOOK_FORM, coupon_phone=PHONE, coupon_expected="1")
    r = client.post("/book/apt-uuid-a", data=form)
    assert r.status_code == 302 and r.headers["Location"] == OK["payment_url"]
    assert _keys(real_client.session) == [KEY, KEY]
    raw = real_client.session.raw_bodies
    assert len(raw) == 2 and raw[0] == raw[1]                 # побайтно
    assert sleeps == [2]


def test_body_bytes_are_deterministic_across_calls(real_client, sleeps):
    """Дві окремі спроби з тими самими даними — ті самі байти (ні часу, ні випадку в тілі,
    порядок ключів сталий): повтор форми браузером з тим самим ключем не дасть mismatch."""
    args = ("apt-uuid-a", "2030-06-01", "2030-06-03", 2, NAME, PHONE, PHONE, "1", KEY)
    real_client.client.create_booking(*args)
    real_client.client.create_booking(*args)
    a, b = real_client.session.raw_bodies
    assert a == b
    assert a.startswith(b'{"apartment_id": "apt-uuid-a", "check_in": ')
    # купон гостя доїжджає до бек-офісу в тілі — лише там знімають −500
    # (диверсія 13.10: без цього рядка «телефон не передано» лишалось зеленим)
    assert b'"coupon_phone": "+380000000000"' in a and b'"coupon_expected": "1"' in a


def test_retries_capped_at_two(client, real_client, sleeps):
    real_client.session.route = book_route(r409("idempotency_in_progress", "1"))
    r = client.post("/book/apt-uuid-a", data=BOOK_FORM)
    assert _keys(real_client.session) == [KEY] * 3            # 1 + 2 повтори
    assert sleeps == [1, 1]
    q = _back_query(r)
    assert q == {"check_in": "2030-06-01", "check_out": "2030-06-03", "guests": "2", "book": "1"}
    assert _flashes(client) == [
        ("error", "Бронювання тимчасово недоступне — спробуйте за 1 хвилину.")]


def test_wait_budget_ten_seconds(client, real_client, sleeps):
    real_client.session.route = book_route(r503("6"))
    r = client.post("/book/apt-uuid-a", data=BOOK_FORM)
    assert sleeps == [6]                                     # 6 + 6 > 10 — другого не чекаємо
    assert len(_keys(real_client.session)) == 2
    _back_query(r)
    assert _flashes(client) == [
        ("error", "Бронювання тимчасово недоступне — спробуйте за 1 хвилину.")]


@pytest.mark.parametrize("retry_after", [None, "11", "abc"],
                         ids=["no-header", "beyond-budget", "not-a-number"])
def test_no_wait_beyond_budget_or_without_number(client, real_client, sleeps, retry_after):
    """Нема заголовка, понад бюджет, не число — без очікування й без повтору
    (409 і 503 ідуть одним циклом — див. test_retry_same_key_same_bytes)."""
    real_client.session.route = book_route(r409("idempotency_in_progress", retry_after))
    r = client.post("/book/apt-uuid-a", data=BOOK_FORM)
    assert sleeps == []
    assert len(_keys(real_client.session)) == 1
    _back_query(r)
    [(cat, text)] = _flashes(client)
    assert cat == "error" and text.startswith("Бронювання тимчасово недоступне")


def test_429_is_not_retried(client, real_client, sleeps):
    real_client.session.route = book_route(StubResponse(429, "<html></html>", {"Retry-After": "1"},
                                                        content_type="text/html"))
    client.post("/book/apt-uuid-a", data=BOOK_FORM)
    assert len(_keys(real_client.session)) == 1 and sleeps == []


# --- без повтору ----------------------------------------------------------------------

def test_mismatch_not_retried_generic_error(client, real_client, sleeps, caplog):
    caplog.set_level(logging.INFO)
    real_client.session.route = book_route(r409("idempotency_key_mismatch", "1"),
                                           StubResponse(200, OK))
    r = client.post("/book/apt-uuid-a", data=BOOK_FORM)
    assert _keys(real_client.session) == [KEY]
    assert sleeps == []
    q = _back_query(r)
    assert q["check_in"] == "2030-06-01"
    assert _flashes(client) == [("error", "Не вдалося створити платіж. Спробуйте ще раз або "
                                          "напишіть нам у Telegram: @minyaylo")]
    warn = [r.getMessage() for r in caplog.records
            if r.levelno == logging.WARNING and r.name == "api_client"]
    assert warn == [f"backoffice_book_key_mismatch key={KEY[:8]}"]


def test_dates_unavailable_as_before(client, real_client, sleeps):
    real_client.session.route = book_route(r409("dates_unavailable", "1"))
    r = client.post("/book/apt-uuid-a", data=BOOK_FORM)
    assert len(_keys(real_client.session)) == 1 and sleeps == []
    assert "check_in" not in _back_query(r)
    assert _flashes(client) == [("error", get_t("ua")("dates_unavailable"))]


def test_409_html_body_is_dates_unavailable(real_client, sleeps):
    real_client.session.route = book_route(StubResponse(409, "<html>", content_type="text/html"))
    with pytest.raises(api_client.BackofficeError, match="^dates_unavailable$"):
        real_client.client.create_booking("apt-uuid-a", "2030-06-01", "2030-06-03", 2, NAME, PHONE)
    assert sleeps == []


# --- журнал --------------------------------------------------------------------------

def test_one_info_line_per_attempt_without_guest_data(client, real_client, sleeps, caplog,
                                                     monkeypatch):
    secret = "pk-test-" + uuid.uuid4().hex
    monkeypatch.setattr(Config, "PUBLIC_API_KEY", secret)
    real_client.rebuild()
    caplog.set_level(logging.INFO)
    real_client.session.route = book_route(r409("idempotency_in_progress", "1"),
                                           StubResponse(200, OK))
    client.post("/book/apt-uuid-a", data=dict(BOOK_FORM, coupon_phone=PHONE))
    lines = [r.getMessage() for r in caplog.records if r.name == "api_client"]
    assert lines == [f"backoffice_book key={KEY[:8]} attempt=1 status=409",
                     f"backoffice_book key={KEY[:8]} attempt=2 status=200"]
    everything = "\n".join(r.getMessage() for r in caplog.records)
    for leak in (KEY, PHONE, PHONE[1:], NAME, "2030-06-01", "2030-06-03", secret, "?"):
        assert leak not in everything, leak


def test_network_error_logged_once_not_retried(client, real_client, sleeps, caplog):
    import requests
    caplog.set_level(logging.INFO, logger="api_client")

    def route(method, path, params, headers, body):
        if path == "book":
            raise requests.ConnectionError("https://bo.test/api/public/book boom")
        return default_route(method, path, params, headers, body)
    real_client.session.route = route
    r = client.post("/book/apt-uuid-a", data=BOOK_FORM)
    assert r.status_code == 302
    assert len(_keys(real_client.session)) == 1 and sleeps == []
    lines = [r.getMessage() for r in caplog.records if r.name == "api_client"]
    assert lines == [f"backoffice_book key={KEY[:8]} attempt=1 status=ConnectionError"]
