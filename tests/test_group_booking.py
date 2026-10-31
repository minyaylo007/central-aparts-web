"""
Бронь кількох апартаментів однією оплатою (спека 108 бек-офісу, 06.10.2026).

GET /api/quote-group — проксі смети без жодної арифметики на сайті; POST /book-group —
одна оплата Monobank за 2–6 апартаментів. Ключ спроби, повтори на 409
idempotency_in_progress і 503, «mismatch не повторюється» і рядок журналу — той самий
цикл api_client._post_idempotent, що в /book (тест на /book тут — щоб одиночна
бронь не змінилась). Справжній BackofficeClient над StubSession, sleep підмінено.
Лише синтетика: телефон з кодом 00, вигадані імена.
13.10.2026: один тест на гілку — 53 → 26 (зрізано лише повтори параметрів).
"""
import json
import logging
import re
from urllib.parse import parse_qs, urlsplit

import pytest

import api_client
from conftest import StubResponse, default_route
from translations import get_t

# critical: бронь, гроші й назва зайнятої кімнати гостю
pytestmark = pytest.mark.critical

PHONE = "+380000000108"
NAME = "Тест Груповий"
KEY = "1a2b3c4d-5e6f-4a1b-8c2d-3e4f5a6b7c8d"
A, B = "apt-uuid-a", "apt-uuid-b"
FORM = {"check_in": "2030-06-01", "check_out": "2030-06-03", "name": NAME, "phone": PHONE,
        "idempotency_key": KEY, "room": [A, B], "room_guests": ["2", "3"]}
OK = {"booking_id": "grp00001", "payment_url": "https://pay.monobank.ua/group", "total": 9900,
      "nights": 2, "base_amount": 9400, "tourist_tax": 500,
      "items": [{"request_id": "r1", "apartment_id": A, "apartment": "A", "base_amount": 4300, "guests": 2},
                {"request_id": "r2", "apartment_id": B, "apartment": "B", "base_amount": 5100, "guests": 3}]}
QUOTE = {
    "nights": 2, "check_in": "2030-06-01", "check_out": "2030-06-03",
    "rooms": [{"apartment_id": A, "apartment": "A", "name": "Апартаменти A", "guests": 2, "nights": 2,
               "base_amount": 4300, "tourist_tax": 192, "total": 4492, "nightly": []},
              {"apartment_id": B, "apartment": "B", "name": "Апартаменти B", "guests": 3, "nights": 2,
               "base_amount": 5100, "tourist_tax": 288, "total": 5388, "nightly": []}],
    "unavailable": [], "available": True,
    # навмисно НЕ сума строк: сайт мусить віддати число бек-офісу, а не порахувати своє
    "base_amount": 9400, "tourist_tax": 481, "total": 9881,
}
UUID4 = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}")


def group_route(*answers, quote=None):
    """POST book-group віддає answers по черзі (останній — далі назавжди); GET
    quote-group — quote (StubResponse або dict); решта — як default_route."""
    queue = list(answers) or [StubResponse(201, OK)]

    def route(method, path, params, headers, body):
        if method == "POST" and path == "book-group":
            return queue.pop(0) if len(queue) > 1 else queue[0]
        if method == "GET" and path == "quote-group":
            q = quote if quote is not None else QUOTE
            return q if isinstance(q, StubResponse) else StubResponse(200, q)
        return default_route(method, path, params, headers, body)
    return route


def err(status, body, headers=None):
    return StubResponse(status, body, headers or {})


@pytest.fixture
def sleeps(monkeypatch):
    waited = []
    monkeypatch.setattr(api_client, "_sleep", waited.append)
    return waited


@pytest.fixture
def group(real_client):
    """real_client з маршрутом групи: group.use(*answers, quote=...)."""
    def use(*answers, quote=None):
        real_client.session.route = group_route(*answers, quote=quote)
        return real_client.session
    real_client.use = use
    use()
    return real_client


def _group_posts(session):
    return [(h, b) for (m, p, _, h, b) in session.requests if m == "POST" and p == "book-group"]


def _flashes(client):
    with client.session_transaction() as s:
        return [m for _, m in s.pop("_flashes", [])]   # забрати: редирект тут не відкривають


def _back(r):
    assert r.status_code == 302
    loc = urlsplit(r.headers["Location"])
    return loc.path, {k: v[0] for k, v in parse_qs(loc.query).items()}


# --- прохід: форма → бек-офіс → Монобанк -------------------------------------------

def test_book_group_sends_items_and_key_and_redirects_to_payment(client, group, sleeps):
    r = client.post("/book-group", data=FORM)
    assert r.status_code == 302 and r.headers["Location"] == OK["payment_url"]
    [(headers, body)] = _group_posts(group.session)
    assert headers["Idempotency-Key"] == KEY
    assert body == {"items": [{"apartment_id": A, "guests": 2}, {"apartment_id": B, "guests": 3}],
                    "check_in": "2030-06-01", "check_out": "2030-06-03", "name": NAME, "phone": PHONE}
    assert sleeps == []


@pytest.mark.parametrize("raw", [None, KEY.upper()])
def test_missing_or_bad_key_server_makes_own(client, group, sleeps, raw):
    form = dict(FORM)
    if raw is None:
        form.pop("idempotency_key")
    else:
        form["idempotency_key"] = raw
    client.post("/book-group", data=form)
    [(headers, _)] = _group_posts(group.session)
    assert UUID4.fullmatch(headers["Idempotency-Key"]) and headers["Idempotency-Key"] != KEY


def test_guests_are_clamped_and_empty_rooms_dropped(client, group, sleeps):
    form = dict(FORM, room=[A, " ", B], room_guests=["9", "2", "0"])
    client.post("/book-group", data=form)
    [(_, body)] = _group_posts(group.session)
    # порожній room відкинуто; гості беруться за позицією і зажимаються 1–4
    assert [i["apartment_id"] for i in body["items"]] == [A, B]
    assert all(1 <= i["guests"] <= 4 for i in body["items"])


def test_no_rooms_goes_home_without_backoffice(client, group):
    r = client.post("/book-group", data={k: v for k, v in FORM.items() if k not in ("room", "room_guests")})
    assert r.status_code == 302 and urlsplit(r.headers["Location"]).path == "/"
    assert _group_posts(group.session) == []


def test_missing_fields_flash_and_keep_selection(client, group):
    r = client.post("/book-group", data=dict(FORM, phone=""))
    path, q = _back(r)
    assert path == f"/apartment/{A}"
    assert q["group"] == f"{B}:3" and q["guests"] == "2" and q["book"] == "1"
    assert q["check_in"] == "2030-06-01" and q["check_out"] == "2030-06-03"
    assert _flashes(client) == [get_t("ua")("fill_all_fields")]
    assert _group_posts(group.session) == []


# --- повтори: той самий цикл, що в /book -------------------------------------------

def test_in_progress_retried_with_same_key_and_same_bytes(client, group, sleeps):
    group.use(err(409, {"error": "idempotency_in_progress"}, {"Retry-After": "2"}),
              err(503, {"error": "busy"}, {"Retry-After": "1"}),
              StubResponse(201, OK))
    r = client.post("/book-group", data=FORM)
    assert r.headers["Location"] == OK["payment_url"]
    posts = _group_posts(group.session)
    assert len(posts) == 3
    assert {h["Idempotency-Key"] for h, _ in posts} == {KEY}
    assert len(set(group.session.raw_bodies)) == 1          # тіло серіалізоване один раз
    assert sleeps == [2, 1]


def test_retry_budget_exhausted_gives_unavailable_text(client, group, sleeps):
    group.use(err(503, {"error": "busy"}, {"Retry-After": "6"}))
    r = client.post("/book-group", data=FORM)
    assert len(_group_posts(group.session)) == 2              # 6 с влізли, ще 6 — ні
    assert sleeps == [6]
    path, q = _back(r)
    assert path == f"/apartment/{A}" and q["group"] == f"{B}:3"
    [msg] = _flashes(client)
    assert NAME not in msg and PHONE not in msg


def test_mismatch_is_not_retried(client, group, sleeps, caplog):
    caplog.set_level(logging.INFO)
    group.use(err(409, {"error": "idempotency_key_mismatch"}))
    client.post("/book-group", data=FORM)
    assert len(_group_posts(group.session)) == 1
    assert sleeps == []
    assert _flashes(client) == [get_t("ua")("error_booking")]
    assert any(r.getMessage() == f"backoffice_book_group_key_mismatch key={KEY[:8]}" for r in caplog.records)


def test_log_line_per_attempt_without_personal_data(client, group, sleeps, caplog):
    caplog.set_level(logging.INFO)
    group.use(err(409, {"error": "idempotency_in_progress"}, {"Retry-After": "1"}), StubResponse(201, OK))
    client.post("/book-group", data=FORM)
    lines = [r.getMessage() for r in caplog.records if r.name == "api_client"]
    assert f"backoffice_book_group key={KEY[:8]} attempt=1 status=409 rooms=2" in lines
    assert f"backoffice_book_group key={KEY[:8]} attempt=2 status=201 rooms=2" in lines
    everything = "\n".join(r.getMessage() for r in caplog.records)
    for secret in (PHONE, PHONE[-9:], NAME, "2030-06-01", KEY, "pay.monobank.ua"):
        assert secret not in everything


def test_single_book_log_line_and_body_unchanged(client, real_client, sleeps, caplog):
    """Одиночна /book після рефакторингу: та сама строка журналу без rooms= і те
    саме тіло, що до спеки 108."""
    caplog.set_level(logging.INFO)
    form = {"check_in": "2030-06-01", "check_out": "2030-06-03", "guests": "2",
            "name": NAME, "phone": PHONE, "idempotency_key": KEY}
    client.post(f"/book/{A}", data=form)
    lines = [r.getMessage() for r in caplog.records if r.name == "api_client"]
    assert f"backoffice_book key={KEY[:8]} attempt=1 status=200" in lines
    assert not any("rooms=" in line for line in lines)
    [raw] = real_client.session.raw_bodies
    assert json.loads(raw) == {"apartment_id": A, "check_in": "2030-06-01", "check_out": "2030-06-03",
                               "guests": 2, "name": NAME, "phone": PHONE}


# --- кожна відповідь бек-офісу → свій текст гостю ----------------------------------

GROUP_ERROR_TEXTS = [
    (err(400, {"error": "need_two_apartments"}), "group_need_two"),
    (err(400, {"error": "too_many_apartments"}), "group_too_many"),
    (err(400, {"error": "duplicate_apartment"}), "group_duplicate"),
    (err(400, {"error": "invalid_guests"}), "group_invalid_guests"),
    (err(400, {"error": "invalid_dates"}), "group_invalid_dates"),
    (err(400, {"error": "invalid_items"}), "error_booking"),
    (err(400, {"error": "missing_fields"}), "error_booking"),
    (err(400, {"error": "invalid_idempotency_key"}), "error_booking"),
    (err(409, {"error": "too_many_holds"}), "too_many_holds"),
    (err(500, {"error": "apartment_config_missing", "apartment_id": B}), "booking_temporarily_unavailable"),
    (err(502, {"error": "payment_unavailable"}), "group_payment_unavailable"),
    (err(401, {"error": "unauthorized"}), "booking_unavailable_write_us"),
]


def test_each_error_has_its_own_human_text(client, group, sleeps):
    """Кожна відповідь бек-офісу → свій ключ перекладу (ua — через сторінку,
    en — наявність рядка в translations.py; до 13.10 це були 24 тести)."""
    with client.session_transaction() as s:
        s["lang"] = "ua"
    for resp, key in GROUP_ERROR_TEXTS:
        group.use(resp)
        r = client.post("/book-group", data=FORM)
        path, q = _back(r)
        assert path == f"/apartment/{A}" and q["group"] == f"{B}:3", key
        msg = get_t("ua")(key)
        assert msg != key, f"ua.{key} нема в translations.py"
        assert _flashes(client) == [msg], key
        assert get_t("en")(key) != key, f"en.{key} нема в translations.py"


def test_payment_unavailable_can_be_retried_with_new_key(client, group, sleeps):
    """502: рахунку нема — гість тисне ще раз, браузер шле НОВИЙ ключ, і бек-офіс
    його приймає (старий не «зачинено» на сайті)."""
    group.use(err(502, {"error": "payment_unavailable"}), StubResponse(201, OK))
    client.post("/book-group", data=FORM)
    other = "2b3c4d5e-6f7a-4b2c-9d3e-4f5a6b7c8d9e"
    r = client.post("/book-group", data=dict(FORM, idempotency_key=other))
    assert r.headers["Location"] == OK["payment_url"]
    assert [h["Idempotency-Key"] for h, _ in _group_posts(group.session)] == [KEY, other]


def test_dates_unavailable_names_the_room_and_drops_it(client, group, sleeps):
    lang, name = "ua", "Апартаменти B"
    group.use(err(409, {"error": "dates_unavailable", "apartment_id": B, "apartment": "B"}))
    r = client.post("/book-group", data=dict(FORM, room=[A, B, "apt-uuid-c"], room_guests=["2", "3", "1"]))
    path, q = _back(r)
    # зайнята B прибрана з вибору, A лишилась головною, C — у group
    assert path == f"/apartment/{A}" and q["group"] == "apt-uuid-c:1"
    [msg] = _flashes(client)
    assert msg == get_t(lang)("group_room_unavailable").format(name=name)
    assert B not in msg


def test_dates_unavailable_on_head_room_makes_next_one_head(client, group, sleeps):
    group.use(err(409, {"error": "dates_unavailable", "apartment_id": A, "apartment": "A"}))
    path, q = _back(client.post("/book-group", data=FORM))
    assert path == f"/apartment/{B}" and q["guests"] == "3" and "group" not in q


def test_unknown_room_by_code_then_no_name_never_id(client, group, sleeps):
    # id не з каталогу, код є → назва за кодом
    group.use(err(409, {"error": "dates_unavailable", "apartment_id": "zzz", "apartment": "B"}))
    client.post("/book-group", data=FORM)
    assert _flashes(client) == [get_t("ua")("group_room_unavailable").format(name="Апартаменти B")]
    # ні id, ні коду в каталозі → текст без назви; id гостю не показуємо
    group.use(err(404, {"error": "apartment_not_found", "apartment_id": "zzz-unknown"}))
    client.post("/book-group", data=FORM)
    [msg] = _flashes(client)
    assert msg == get_t("ua")("group_room_not_found_noname") and "zzz" not in msg


def test_unavailable_and_unexpected_errors(client, group, sleeps, monkeypatch):
    group.use(err(429, {"error": "rate_limited"}, {"Retry-After": "120"}))
    client.post("/book-group", data=FORM)
    [msg] = _flashes(client)
    assert msg and NAME not in msg

    def boom(*a, **kw):
        raise RuntimeError("socket")
    monkeypatch.setattr(group.client, "create_group_booking", boom)
    path, _ = _back(client.post("/book-group", data=FORM))
    assert path == f"/apartment/{A}"
    assert _flashes(client) == [get_t("ua")("error_booking")]


# --- 401/403 бек-офісу: зламаний ключ сайту — наша поломка, не гостя -------------

SITE_KEY = "test-site-key-must-not-leak"


@pytest.mark.parametrize("status", [401, 403])
@pytest.mark.parametrize("route", ["book", "book_group"])
def test_backoffice_auth_failure_is_one_error_line_and_honest_text(
        client, group, sleeps, caplog, monkeypatch, route, status):
    """Ключ сайту не прийнято: одна строка ERROR backoffice_auth_failed (сторож
    будить дирижера) без ключа й даних гостя; гостю — «тимчасово недоступне,
    напишіть нам», форма — назад з датами й гостями; повторів нема."""
    monkeypatch.setattr(api_client.Config, "PUBLIC_API_KEY", SITE_KEY)
    group.rebuild()
    caplog.set_level(logging.INFO)
    resp = err(status, {"error": "unauthorized"})
    if route == "book":
        def answer(method, path, params, headers, body):
            return resp if method == "POST" and path == "book" else default_route(
                method, path, params, headers, body)
        group.session.route = answer
        r = client.post(f"/book/{A}", data={"check_in": "2030-06-01", "check_out": "2030-06-03",
                                            "guests": "2", "name": NAME, "phone": PHONE,
                                            "idempotency_key": KEY})
        path, q = _back(r)
        assert path == f"/apartment/{A}" and q["guests"] == "2"
    else:
        group.use(resp)
        path, q = _back(client.post("/book-group", data=FORM))
        assert path == f"/apartment/{A}" and q["group"] == f"{B}:3"
    assert q["check_in"] == "2030-06-01" and q["check_out"] == "2030-06-03" and q["book"] == "1"
    assert len([x for x in group.session.requests if x[0] == "POST"]) == 1 and sleeps == []
    assert _flashes(client) == [get_t("ua")("booking_unavailable_write_us")]
    loud = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
    assert loud == [f"backoffice_auth_failed call={route} status={status}"]
    everything = "\n".join(r.getMessage() for r in caplog.records)
    for secret in (SITE_KEY, PHONE, PHONE[-9:], NAME, KEY, "http"):
        assert secret not in everything


# --- смета: проксі без арифметики ---------------------------------------------------

def test_quote_group_is_passed_through_as_is(client, group):
    r = client.get(f"/api/quote-group?apartment_ids={A},{B}&check_in=2030-06-01&check_out=2030-06-03&guests=2,3")
    assert r.status_code == 200
    assert r.get_json() == QUOTE          # total 9881 — бек-офісу, а не сума строк
    [(m, p, params, _, _)] = [x for x in group.session.requests if x[1] == "quote-group"]
    assert params == {"apartment_ids": f"{A},{B}", "check_in": "2030-06-01", "check_out": "2030-06-03",
                      "guests": "2,3", "lang": "ua"}


@pytest.mark.parametrize("resp,status,body", [
    (err(404, {"error": "apartment_not_found", "apartment_id": "x"}), 404,
     {"error": "apartment_not_found", "apartment_id": "x"}),
    (err(400, {"error": "invalid_dates"}), 400, {"error": "invalid_dates"}),
    (err(500, {"error": "apartment_config_missing", "apartment_id": "x"}), 503,
     {"error": "apartment_config_missing", "apartment_id": "x"}),
])
def test_quote_group_errors(client, group, resp, status, body):
    group.use(quote=resp)
    r = client.get(f"/api/quote-group?apartment_ids={A},x&check_in=2030-06-01&check_out=2030-06-03&guests=2,2")
    assert r.status_code == status and r.get_json() == body


def test_quote_group_busy_and_network(client, group, monkeypatch):
    group.use(quote=err(503, {"error": "busy"}, {"Retry-After": "7"}))
    r = client.get(f"/api/quote-group?apartment_ids={A},{B}&check_in=2030-06-01&check_out=2030-06-03&guests=2,2")
    assert r.status_code == 503

    def boom(*a, **kw):
        raise RuntimeError("socket")
    monkeypatch.setattr(group.client, "get_group_quote", boom)
    r = client.get(f"/api/quote-group?apartment_ids={A},{B}&check_in=2030-06-01&check_out=2030-06-03&guests=2,2")
    assert r.status_code == 503 and r.get_json() == {"error": "unavailable"}


# --- сторінки ------------------------------------------------------------------------

def test_apartment_page_offers_other_rooms_not_itself(client):
    html = client.get(f"/apartment/{A}").get_data(as_text=True)
    form = html[html.index('id="booking-form"'):]
    form = form[:form.index("</form>")]
    assert 'id="group-field"' in form
    assert f'data-apt-id="{B}"' in form and f'data-apt-id="{A}"' not in form
    assert get_t("ua")("group_add_toggle") in form


def test_confirmed_page_lists_every_room_and_group_total(client, mock_client):
    mock_client.booking_status = dict(mock_client.booking_status, group={
        "primary_id": "test1234", "role": "primary", "total": 9881,
        "items": [{"request_id": "r1", "apartment_id": A, "apartment": "A", "base_amount": 4300, "guests": 2},
                  {"request_id": "r2", "apartment_id": B, "apartment": "B", "base_amount": 5100, "guests": 3},
                  {"request_id": "r3", "apartment_id": "gone", "apartment": "Z", "base_amount": 1, "guests": 1}]})
    html = client.get("/booking-confirmed?ref=test1234").get_data(as_text=True)
    lst = html[html.index('id="group-confirmed-list"'):]
    lst = lst[:lst.index("</ul>")]
    items = re.findall(r"<li>(.*?)</li>", lst, re.S)
    assert [re.sub(r"\s+", " ", i).strip() for i in items] == [
        "Апартаменти A · 2 гост.", "Апартаменти B · 3 гост.", "Z · 1 гост."]
    assert "9881" in html and "value: 9881" in html
    assert "second-apt-card" not in html          # обидві квартири вже в броні — нема що пропонувати


def test_confirmed_page_single_booking_unchanged(client):
    html = client.get("/booking-confirmed?ref=test1234").get_data(as_text=True)
    assert 'id="group-confirmed-list"' not in html
    assert "5220" in html and "value: 5220" in html


# --- JS: гроші рахує лише сервер ----------------------------------------------------

def _group_js():
    import pathlib
    js = (pathlib.Path(__file__).resolve().parent.parent / "static" / "main.js").read_text(encoding="utf-8")
    start = js.index("// ---- Spec 108")
    block = js[start:js.index("Idempotency-Key", start)]
    return re.sub(r"//[^\n]*", "", block)            # коментарі не код


def test_group_js_shows_server_numbers_and_never_adds_money():
    js = _group_js()
    assert "fmt_uah(q.total)" in js and "fmt_uah(q.tourist_tax)" in js
    assert "Number(q.total)" in js                     # липка ціна — теж число сервера
    # ні накопичення, ні складання сум, ні константи 48 у блоці групи
    assert not re.search(r"\+=|\.reduce\(|\b48\b", js)
    assert not re.search(r"(base_amount|total|tourist_tax)\s*\+|\+\s*\w*\.(base_amount|total|tourist_tax)", js)
