"""Своя статистика (analytics/): приймач POST /t, чистота від ПД, вимкнення без env, панель /stats.

Телефони в тестах — лише з кодом оператора 00. «Брудний» запит навмисно несе ім'я,
телефон, пошту, дату народження й значення полів; перевірка _pii_in_db має знайти їх у
навмисно зіпсованій базі (test_pii_checker_catches_corrupted_sample) — інакше її «чисто»
нічого не варте.
"""
import gzip
import json
import os
import re
import sqlite3
import time
import zlib

import pytest

import analytics
from analytics import clean, queries, store
from config import Config

UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/130.0 Safari/537.36"}
SID, PV = "testsess01", "testpv0001"
KEY = "test-stats-key-0123456789"

# ПД, які не мають лягти в базу ні в якому вигляді
NAME = "Тарас Шевченко"
PHONE = "+380 00 123 45 67"
PHONE_DIGITS = "380001234567"
EMAIL = "guest.test@example.com"
BIRTH = "1990-05-17"
FIELD_VALUE = "Секретне значення поля"
SECRETS = (NAME, "Шевченко", PHONE, PHONE_DIGITS, "1234567", EMAIL, "guest.test", BIRTH, FIELD_VALUE)


@pytest.fixture
def adb(tmp_path, monkeypatch):
    db = str(tmp_path / "analytics.db")
    monkeypatch.setattr(Config, "ANALYTICS_DB", db)
    monkeypatch.setattr(Config, "STATS_KEY", KEY)
    analytics._hits.clear()
    yield db
    analytics._hits.clear()


def _post(client, body, **kw):
    data = body if isinstance(body, (bytes, str)) else json.dumps(body)
    headers = dict(UA, **kw.pop("headers", {}))
    return client.post(kw.pop("url", "/t"), data=data, headers=headers, **kw)


def _batch(events, sid=SID, pv=PV):
    return {"v": 1, "s": sid, "p": pv, "e": events}


def _pv(path="/", **extra):
    e = {"k": "pv", "u": path, "w": 1280, "h": 800, "l": "uk", "sl": "uk", "r": "", "q": {}, "wd": 0}
    e.update(extra)
    return e


def _rows(db, sql):
    con = sqlite3.connect(db)
    try:
        return con.execute(sql).fetchall()
    finally:
        con.close()


def _db_dump(db) -> str:
    """Уся база текстом: кожна клітинка таблиць + розпаковані записи rrweb."""
    con = sqlite3.connect(db)
    out = []
    try:
        for (table,) in con.execute("SELECT name FROM sqlite_master WHERE type='table'"):
            for row in con.execute(f"SELECT * FROM {table}"):
                for v in row:
                    if isinstance(v, bytes):
                        v = zlib.decompress(v).decode()
                    out.append(str(v))
    finally:
        con.close()
    return "\n".join(out)


def _pii_in_db(db) -> list:
    dump = _db_dump(db)
    found = [s for s in SECRETS if s in dump]
    # json.dumps у записах кодує кирилицю як \\uXXXX — шукаємо й так
    found += [s for s in SECRETS if json.dumps(s)[1:-1] in dump and s not in found]
    return found


DIRTY = _batch([
    _pv("/apartment/abc?name=Тарас+Шевченко&phone=380001234567",
        r="google.com", q={"utm_source": "fb", "utm_campaign": f"{EMAIL} {PHONE}",
                                 "phone": PHONE, "name": NAME}, name=NAME, value=FIELD_VALUE),
    {"k": "c", "u": "/apartment/380001234567", "x": 0.4, "y": 1.2, "s": "button#btn-book",
     "r": "button", "t": f"{PHONE} {EMAIL}", "value": FIELD_VALUE, "name": NAME},
    {"k": "fd", "u": "/apartment/abc", "f": "w-name", "fm": "booking-form", "ms": 2500, "fl": 1,
     "v": NAME, "value": FIELD_VALUE},
    {"k": "fd", "u": "/apartment/abc", "f": NAME, "fm": "booking-form", "ms": 10, "fl": 1},
    {"k": "ab", "u": "/apartment/abc", "f": "w-phone", "fm": "booking-form", "value": PHONE},
    {"k": "je", "u": "/", "m": f"Cannot read {EMAIL} born {BIRTH} tel {PHONE}",
     "src": f"https://x/static/main.js?phone={PHONE_DIGITS}", "ln": 12},
    {"k": "ap", "u": "/", "a": f"/api/coupon/check?phone={PHONE_DIGITS}", "st": 200, "ms": 80},
    {"k": "f", "u": "/apartment/abc", "st": "form", "name": NAME},
    {"k": "zz", "u": "/", "t": NAME},
])

DIRTY_REC = {"v": 1, "s": SID, "p": PV, "rec": {"q": 0, "u": "/apartment/abc", "e": [
    {"type": 4, "timestamp": 1000, "data": {"href": "https://x/apartment/abc", "width": 1280, "height": 800}},
    {"type": 2, "timestamp": 1001, "data": {"node": {"type": 0, "childNodes": [
        {"type": 2, "tagName": "input", "attributes": {"id": "w-name", "value": NAME}, "childNodes": []},
        {"type": 2, "tagName": "textarea", "attributes": {"value": FIELD_VALUE}, "childNodes": []},
        {"type": 3, "textContent": f"Пишіть: {EMAIL} або {PHONE}"}]}}},
    {"type": 3, "timestamp": 1002, "data": {"source": 5, "id": 7, "text": NAME, "isChecked": False}},
]}}


# ---------------------------------------------------------------------------
# Вимкнено без env
# ---------------------------------------------------------------------------

@pytest.mark.critical
def test_off_without_env_beacon_silent_no_script_no_panel(client, tmp_path, monkeypatch):
    monkeypatch.setattr(Config, "ANALYTICS_DB", "")
    monkeypatch.setattr(Config, "STATS_KEY", KEY)
    r = _post(client, _batch([_pv()]))
    assert r.status_code == 204
    assert list(tmp_path.iterdir()) == []
    home = client.get("/")
    assert home.status_code == 200
    assert b"analytics.js" not in home.data
    assert client.get("/stats").status_code == 404


def test_on_page_has_script_and_footer_note(client, adb):
    home = client.get("/")
    assert b"analytics.js" in home.data
    assert "знеособлену статистику".encode() in home.data


@pytest.mark.critical
def test_beacon_script_sync_before_main(client, adb):
    """30.10: async-маяк виконувався після main.js — ціна з URL квартири (prefill) йшла повз обгортку
    fetch, кроки dates/price губились. Маяк — синхронно й раніше за main.js."""
    html = client.get("/").data.decode()
    tag = re.search(r"<script[^>]*analytics\.js[^>]*>", html)
    main = re.search(r"<script[^>]*main\.js[^>]*>", html)
    assert "async" not in tag.group(0) and "defer" not in tag.group(0)
    assert tag.start() < main.start()


def test_home_dates_step_is_stored_and_in_funnel(client, adb):
    _post(client, _batch([_pv(), {"k": "f", "u": "/", "st": "home_dates"},
                          {"k": "c", "u": "/", "x": 0.5, "y": 0.2}]))
    assert _rows(adb, "SELECT str1 FROM events WHERE kind='f'") == [("home_dates",)]
    steps = queries.funnel(store.connect(), 7)["steps"]
    assert [s["key"] for s in steps][:3] == ["home", "home_dates", "apt"]
    assert steps[1]["n"] == 1 and "главная" in steps[1]["label"]


# ---------------------------------------------------------------------------
# Схема, межі, боти
# ---------------------------------------------------------------------------

@pytest.mark.critical
def test_valid_batch_is_stored(client, adb):
    r = _post(client, _batch([_pv(r="google.com", q={"utm_source": "insta"}),
                              {"k": "c", "u": "/", "x": 0.5, "y": 0.2, "s": "a.btn", "r": "a", "t": "Забронювати"},
                              {"k": "sc", "u": "/", "m": 70, "hr": 4.2, "us": 1}]))
    assert r.status_code == 204
    s = _rows(adb, "SELECT device, ref, utm_source, landing, pages, interacted, bot FROM sessions")
    assert s == [("d", "google.com", "insta", "/", 1, 1, 0)]
    kinds = sorted(k for (k,) in _rows(adb, "SELECT kind FROM events"))
    assert kinds == ["c", "pv", "sc"]


@pytest.mark.parametrize("body", [
    b"not json", b"[]", json.dumps({"v": 2, "s": SID, "p": PV, "e": [_pv()]}),
    json.dumps({"v": 1, "s": "BAD SID!", "p": PV, "e": [_pv()]}),
    json.dumps({"v": 1, "s": SID, "p": PV, "e": [{"k": "pv", "u": "/", "w": "wide"}]}),
    json.dumps({"v": 1, "s": SID, "p": PV, "e": [{"k": "c", "u": "javascript:x", "x": 0.1, "y": 0.1}]}),
])
def test_bad_schema_is_silently_dropped(client, adb, body):
    r = _post(client, body)
    assert r.status_code == 204 and r.data == b""
    assert not os.path.exists(adb) or _rows(adb, "SELECT COUNT(*) FROM events") == [(0,)]


@pytest.mark.critical
def test_oversize_body_dropped(client, adb):
    big = _batch([_pv()] + [{"k": "c", "u": "/", "x": 0.1, "y": 0.1, "s": "a" * 120, "t": "x" * 40}] * 59)
    big["pad"] = "x" * (70 * 1024)
    assert _post(client, big).status_code == 204
    assert not os.path.exists(adb) or _rows(adb, "SELECT COUNT(*) FROM events") == [(0,)]
    huge = b"x" * (analytics.MAX_REC_BODY + 10)
    assert _post(client, huge).status_code == 204


def test_gzip_bomb_dropped_and_gzip_record_accepted(client, adb):
    bomb = gzip.compress(b'{"v":1,"pad":"' + b"0" * (analytics.MAX_UNZIPPED + 100) + b'"}')
    assert len(bomb) < analytics.MAX_REC_BODY
    assert _post(client, bomb, url="/t?z=1").status_code == 204
    assert not os.path.exists(adb) or _rows(adb, "SELECT COUNT(*) FROM recordings") == [(0,)]
    rec = {"v": 1, "s": SID, "p": PV, "rec": {"q": 0, "u": "/", "e": [
        {"type": 4, "timestamp": 1, "data": {}}, {"type": 2, "timestamp": 2, "data": {}}]}}
    assert _post(client, gzip.compress(json.dumps(rec).encode()), url="/t?z=1").status_code == 204
    assert _rows(adb, "SELECT COUNT(*) FROM recordings") == [(1,)]


def test_rate_limit_per_address(client, adb):
    for _ in range(analytics.RATE_PER_MIN + 5):
        _post(client, _batch([{"k": "sc", "u": "/", "m": 10, "hr": 2}]))
    assert _rows(adb, "SELECT COUNT(*) FROM events") == [(analytics.RATE_PER_MIN,)]


@pytest.mark.critical
@pytest.mark.parametrize("ua", ["", "curl/8.5.0", "Googlebot/2.1 (+http://www.google.com/bot.html)",
                                "Mozilla/5.0 (X11; Linux x86_64) HeadlessChrome/130.0 Safari/537.36"])
def test_bot_user_agent_not_stored(client, adb, ua):
    client.post("/t", data=json.dumps(_batch([_pv()])), headers={"User-Agent": ua})
    assert not os.path.exists(adb) or _rows(adb, "SELECT COUNT(*) FROM sessions") == [(0,)]


def test_cross_site_post_dropped(client, adb):
    _post(client, _batch([_pv()]), headers={"Origin": "https://evil.example"})
    assert not os.path.exists(adb) or _rows(adb, "SELECT COUNT(*) FROM sessions") == [(0,)]


def test_webdriver_flag_marks_bot_and_open_only_is_not_people(client, adb):
    _post(client, _batch([_pv(wd=1), {"k": "c", "u": "/", "x": 0.1, "y": 0.1}], sid="botsess01"))
    _post(client, _batch([_pv(), {"k": "sc", "u": "/", "m": 60, "hr": 2, "us": 0}], sid="passive01"))
    rows = dict((s, (b, i)) for s, b, i in _rows(adb, "SELECT sid, bot, interacted FROM sessions"))
    assert rows["botsess01"][0] == 1
    assert rows["passive01"] == (0, 0)


@pytest.mark.critical
def test_beacon_survives_storage_failure(client, adb, monkeypatch):
    def boom(*a, **k):
        raise sqlite3.OperationalError("disk I/O error")
    monkeypatch.setattr(store, "save_batch", boom)
    r = _post(client, _batch([_pv()]))
    assert r.status_code == 204 and r.data == b""


# ---------------------------------------------------------------------------
# ПД: брудний запит лягає чистим
# ---------------------------------------------------------------------------

@pytest.mark.critical
def test_dirty_request_lands_clean(client, adb):
    assert _post(client, DIRTY).status_code == 204
    assert _post(client, DIRTY_REC).status_code == 204
    # корисне збереглося — інакше «чисто» могло б означати «нічого не записали»
    kinds = sorted(k for (k,) in _rows(adb, "SELECT kind FROM events"))
    assert kinds == ["ab", "ap", "c", "f", "fd", "je", "pv"]
    assert _rows(adb, "SELECT COUNT(*) FROM recordings") == [(1,)]
    assert _rows(adb, "SELECT landing, ref, utm_source FROM sessions") == [("/apartment/abc", "google.com", "fb")]
    assert _pii_in_db(adb) == []
    assert "[phone]" in _db_dump(adb) and "[email]" in _db_dump(adb) and "[date]" in _db_dump(adb)


@pytest.mark.critical
def test_pii_checker_catches_corrupted_sample(client, adb):
    """Перевірка вміє сказати «так»: зіпсований рядок, вставлений повз очистку, знаходиться."""
    _post(client, _batch([_pv()]))
    assert _pii_in_db(adb) == []
    con = sqlite3.connect(adb)
    with con:
        con.execute("INSERT INTO events(ts, day, kind, txt) VALUES (1, '2026-10-21', 'c', ?)", (NAME,))
        con.execute("INSERT INTO recordings(sid, ts, day, data) VALUES (?, 1, '2026-10-21', ?)",
                    (SID, zlib.compress(json.dumps([{"t": PHONE}]).encode())))
    con.close()
    found = _pii_in_db(adb)
    assert NAME in found and PHONE in found


def test_scrub_rec_masks_field_values():
    out = clean.scrub_rec(DIRTY_REC["rec"]["e"])
    s = json.dumps(out, ensure_ascii=False)
    for secret in (NAME, FIELD_VALUE, EMAIL, PHONE):
        assert secret not in s
    assert '"id": "w-name"' in s


def test_unknown_fields_and_kinds_dropped():
    assert clean.clean_event({"k": "zz", "u": "/"}) is None
    e = clean.clean_event({"k": "fd", "u": "/", "f": "w-name", "fm": "booking-form", "ms": 5, "fl": 1,
                           "value": FIELD_VALUE})
    assert FIELD_VALUE not in json.dumps(e, ensure_ascii=False)
    assert clean.clean_path("/apartment/380001234567?phone=1") == "/apartment/:n"
    assert clean.clean_path("/apartment/94577289-2fcb-42fc-a113-832794b65309") == \
        "/apartment/94577289-2fcb-42fc-a113-832794b65309"


def test_api_timing_from_background_tab_has_no_ms():
    """22.10: 97892 мс у /api/quote — вкладка була у фоні, сервер відповів за < 0,5 с.
    bg: 1 → часу нема (num1 NULL, AVG у панелі його пропускає), num2 = 1."""
    seen = clean.clean_event({"k": "ap", "u": "/", "a": "/api/quote/x", "st": 200, "ms": 120})
    assert (seen["num1"], seen["num2"]) == (120, None)
    bg = clean.clean_event({"k": "ap", "u": "/", "a": "/api/quote/x", "st": 200, "ms": 97892,
                            "bg": 1})
    assert (bg["n"], bg["num1"], bg["num2"]) == (200, None, 1)
    src = open(os.path.join(os.path.dirname(os.path.dirname(__file__)), "static",
                            "analytics.js"), encoding="utf-8").read()
    assert "e.bg = 1" in src and "nhid++" in src


# ---------------------------------------------------------------------------
# Сервер: перехід на оплату; зберігання
# ---------------------------------------------------------------------------

@pytest.mark.critical
def test_payment_redirect_counted_without_session(client, adb, mock_client):
    r = client.post("/book/apt-uuid-a", data={"check_in": "2026-05-20", "check_out": "2026-05-23",
                                              "guests": "2", "name": "Тест Тестів", "phone": "+380000000000"})
    assert r.status_code in (302, 303)
    assert _rows(adb, "SELECT kind, path, str1, sid FROM events") == [("srv", "/book", "pay", None)]
    assert "Тест" not in _db_dump(adb)


def test_purge_keeps_90_days_of_recordings(adb):
    con = store.connect()
    now = time.time()
    old, fresh = store.day_of(now - 91 * 86400), store.day_of(now - 10 * 86400)
    with con:
        for day in (old, fresh):
            con.execute("INSERT INTO recordings(sid, ts, day, data) VALUES (?, 1, ?, ?)",
                        (SID, day, zlib.compress(b"[]")))
    store.maybe_purge(con, now, force=True)
    assert [d for (d,) in con.execute("SELECT day FROM recordings")] == [fresh]
    con.close()


def test_script_size_and_safety():
    path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static", "analytics.js")
    src = open(path, encoding="utf-8").read()
    # 30.10: 10 → 11 КБ — крок «дати (головна)», сторож мертвого кліку й прапор «сховано до LCP»
    # 30.10 (2): 11 → 13 КБ — правило dead 2: ціль від pointerdown, dd/dx, свій сторож на тап
    assert len(src.encode()) < 13 * 1024
    assert "maskAllInputs: true" in src and "#booking-form" in src
    assert "document.cookie" not in src and "localStorage" not in src


# ---------------------------------------------------------------------------
# Панель /stats
# ---------------------------------------------------------------------------

@pytest.mark.critical
def test_panel_closed_without_key_or_login(client, adb, monkeypatch):
    r = client.get("/stats")
    assert r.status_code == 401 and r.headers["Cache-Control"] == "no-store"
    assert client.get("/stats/api/rec/" + SID).status_code == 403
    assert client.post("/stats/login", data={"key": "wrong"}).status_code == 401
    monkeypatch.setattr(Config, "STATS_KEY", "")
    assert client.get("/stats").status_code == 404


def test_panel_after_login_shows_data(client, adb):
    _post(client, DIRTY)
    _post(client, DIRTY_REC)
    assert client.post("/stats/login", data={"key": KEY}).status_code == 302
    r = client.get("/stats")
    assert r.status_code == 200
    assert "Воронка брони".encode() in r.data and b"w-name" in r.data
    assert client.get("/stats/heat?path=/apartment/abc&dev=d").status_code == 200
    assert b'src="/"' in client.get("/stats/heat?path=//evil.example").data
    assert client.get(f"/stats/replay/{SID}").status_code == 200
    rec = client.get(f"/stats/api/rec/{SID}")
    assert rec.status_code == 200 and len(rec.json) == 3


# ---------------------------------------------------------------------------
# Маяк не мовчить для нас (30.10): лічильник у журнал, збій запису — WARNING
# ---------------------------------------------------------------------------

@pytest.fixture
def tally(monkeypatch):
    for k in analytics._tally:
        analytics._tally[k] = 0
    monkeypatch.setattr(analytics, "_tally_quiet", True)
    monkeypatch.setattr(analytics, "_tally_t0", time.monotonic())
    yield analytics._tally
    for k in analytics._tally:
        analytics._tally[k] = 0


def _stats_lines(caplog):
    return [r.getMessage() for r in caplog.records if r.getMessage().startswith("analytics_beacon_stats")]


@pytest.mark.critical
def test_storage_failure_logs_warning_with_type_only(client, adb, tally, monkeypatch, caplog):
    def boom(*a, **k):
        raise sqlite3.OperationalError(f"disk I/O error {PHONE}")
    monkeypatch.setattr(store, "save_batch", boom)
    caplog.set_level("INFO", logger="analytics")
    r = _post(client, _batch([_pv(), {"k": "c", "u": "/", "x": 0.1, "y": 0.1, "t": NAME}]))
    assert r.status_code == 204
    warns = [rec.getMessage() for rec in caplog.records if rec.levelname == "WARNING"]
    assert warns == ["analytics_store_error err=OperationalError"]
    assert tally["error"] == 1 and tally["accepted"] == 0
    assert not [s for s in SECRETS if s in caplog.text]


@pytest.mark.critical
def test_stats_line_first_batch_now_then_hourly(client, adb, tally, monkeypatch, caplog):
    caplog.set_level("INFO", logger="analytics")
    _post(client, _batch([_pv()]))
    _post(client, _batch([_pv()], sid="testsess02"))
    _post(client, b"{not json")                                  # сміття — без WARNING
    _post(client, _batch([_pv()]), headers={"Origin": "https://evil.example"})
    assert _stats_lines(caplog) == [
        "analytics_beacon_stats accepted=1 dropped=0 origin=0 rate=0 body=0 json=0 schema=0 size=0 "
        "bot_ua=0 error=0"]
    assert not [r for r in caplog.records if r.levelname == "WARNING"]
    monkeypatch.setattr(analytics, "_tally_t0", time.monotonic() - analytics.STATS_EVERY_S)
    _post(client, _batch([_pv()]), headers={"User-Agent": "curl/8.5.0"})
    assert _stats_lines(caplog)[1] == (
        "analytics_beacon_stats accepted=1 dropped=3 origin=1 rate=0 body=0 json=1 schema=0 size=0 "
        "bot_ua=1 error=0")


# ---------------------------------------------------------------------------
# Чисті заміри (30.10): швидкість без фонових вкладок, оцінка пристроїв
# ---------------------------------------------------------------------------

def _click():
    return {"k": "c", "u": "/", "x": 0.5, "y": 0.2}


@pytest.mark.critical
def test_vitals_hidden_before_lcp_excluded(client, adb):
    _post(client, _batch([_pv(), {"k": "vt", "u": "/", "lcp": 1200, "inp": 80, "cls": 0.01, "hb": 0}]))
    _post(client, _batch([_pv(), {"k": "vt", "u": "/", "lcp": 95000, "inp": 0, "cls": 0, "hb": 1}],
                         sid="testsess02"))
    assert sorted(_rows(adb, "SELECT n FROM events WHERE kind='vt'")) == [(0,), (1,)]
    v = queries.vitals(store.connect(), 7)
    assert [(x["page"], x["n"], x["lcp"]) for x in v] == [("/", 1, 1200)]


def _session(client, adb, sid, start_min, end_min, w=1280, r="google.com", bot=False):
    pv = _pv(w=w, r=r, wd=1 if bot else 0)
    _post(client, _batch([pv, _click()], sid=sid))
    con = sqlite3.connect(adb)
    with con:
        con.execute("UPDATE sessions SET first_ts=?, last_ts=? WHERE sid=?",
                    (T_MS + start_min * 60_000, T_MS + end_min * 60_000, sid))
    con.close()


T_MS = int(time.time() * 1000) - 6 * 3600 * 1000


def test_devices_estimate_glues_tabs_within_30_min(client, adb):
    _session(client, adb, "devsess001", 0, 5)                  # пристрій 1
    _session(client, adb, "devsess002", 10, 20)                # нова вкладка через 5 хв — той самий
    _session(client, adb, "devsess003", 140, 150)              # через дві години — новий (2)
    _session(client, adb, "devsess004", 12, 15, w=390)         # інша ширина — інший (3)
    _session(client, adb, "devsess005", 12, 15, r="")          # інше джерело — інший (4)
    _session(client, adb, "devsess006", 12, 15, bot=True)      # бот — не рахується
    con = store.connect()
    assert queries.overview(con, 7)["people"] == 5
    assert queries.devices_estimate(con, 7) == 4


def test_stats_report_shows_devices_estimate(client, adb):
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "stats_report", os.path.join(os.path.dirname(os.path.dirname(__file__)), "scripts", "stats_report.py"))
    sr = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sr)
    _session(client, adb, "devsess001", 0, 5)
    _session(client, adb, "devsess002", 10, 20)
    md = sr.build(7)
    assert "| Людей | Устройств (оценка) |" in md
    assert "| 2 | 1 |" in md and "Это оценка" in md


# ---------------------------------------------------------------------------
# Правило мертвого кліку 2 (30.10): dd — лише по цілі, що обіцяє дію; декор — dx
# ---------------------------------------------------------------------------

def _dead(kind, sel, dv=None):
    e = {"k": kind, "u": "/", "x": 0.5, "y": 0.2, "s": sel, "r": "", "t": ""}
    if dv is not None:
        e["dv"] = dv
    return e


@pytest.mark.critical
def test_dead_rule_v2_keeps_old_rows_apart_and_decor_separate(client, adb):
    """Старий запис dd (без dv) — правило 1, новий — правило 2; тап по декору (dx) — не dead.
    Звіт і панель розуміють обидва: рядки двох правил не складаються в один."""
    _post(client, _batch([_pv(), _dead("dd", "span.flatpickr-weekday"),
                          _dead("dd", "span.flatpickr-day", dv=2),
                          _dead("dx", "span.flatpickr-weekday", dv=2)]))
    assert sorted(_rows(adb, "SELECT kind, sel, num1 FROM events WHERE kind IN ('dd','dx')"),
                  key=str) == sorted([("dd", "span.flatpickr-day", 2), ("dd", "span.flatpickr-weekday", None),
                                      ("dx", "span.flatpickr-weekday", 2)], key=str)
    con = store.connect()
    dead = {(r["sel"], r["rule"]) for r in queries.click_problems(con, 7, "dd")}
    assert dead == {("span.flatpickr-day", 2), ("span.flatpickr-weekday", 1)}
    assert [r["sel"] for r in queries.click_problems(con, 7, "dx")] == ["span.flatpickr-weekday"]

    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "stats_report", os.path.join(os.path.dirname(os.path.dirname(__file__)), "scripts", "stats_report.py"))
    sr = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sr)
    md = sr.build(7)
    assert "| `/` | компьютер | `span.flatpickr-day` |  | новое | 1 | 1 |" in md
    assert "| `/` | компьютер | `span.flatpickr-weekday` |  | старое | 1 | 1 |" in md
    decor = md.split("## Тапы по декору без реакции", 1)[1].split("##", 1)[0]
    assert "span.flatpickr-weekday" in decor and "flatpickr-day`" not in decor

    assert client.post("/stats/login", data={"key": KEY}).status_code == 302
    html = client.get("/stats").data.decode()
    assert "Тапы по декору" in html and html.count('<span class="muted">старое правило</span>') == 1


def test_beacon_dead_rule_v2_in_script():
    """Маяк: dead лише по ACT або cursor:pointer, ціль — від pointerdown (Flatpickr відкривається
    поверх поля ще до click, і click приходить BODY — розбір 30.10, правило dead 2).
    Поведінку в браузері стереже смока 07."""
    src = open(os.path.join(os.path.dirname(os.path.dirname(__file__)), "static", "analytics.js"),
               encoding="utf-8").read()
    assert ".flatpickr-day" in src and "cursor === 'pointer'" in src
    assert "'dd' : 'dx'" in src and "d.dv = 2" in src
    assert "'pointerdown'" in src and "t.contains(pd.el)" in src
