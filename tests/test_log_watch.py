"""Сторож журналу scripts/log_watch.py: у повідомлення не тече ПД, повтор не дублюється."""
import importlib.util
import json
import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.critical

_SPEC = importlib.util.spec_from_file_location(
    "log_watch", Path(__file__).resolve().parent.parent / "scripts" / "log_watch.py")
lw = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(lw)

PHONE = "+380001234567"
# фальшивий ключ у формі Stripe; збирається з частин, щоб push protection GitHub не прийняв
# літерал за справжній ключ — у рядку журналу (і в повідомленні сторожа) він стоїть цілим
TOKEN = "sk_" + "live_" + "9fQ2xLmT7vPa1"
IP = "203.0.113.77"


def _leaks(msg: str):
    """Що з чутливого потрапило в рядок. Порожньо — чисто."""
    found = [s for s in (PHONE, PHONE[1:], TOKEN, IP, "9fQ2xLmT7vPa1", "Ivan", "ivan", "Petrenko",
                         "petrenko") if s in msg]
    found += re.findall(r"\d{3,}", msg)          # будь-яке довге число: телефон, IP, id
    return found


def _line(level, msg):
    return f"[2026-10-23 10:00:00 +0300] [12345] [{level}] {msg}"


def _fake_sources(monkeypatch, journal, active="active", restarts=0):
    entries = [(1_792_740_000 + i, m, f"c{i}") for i, m in enumerate(journal)]
    monkeypatch.setattr(lw, "read_journal", lambda cursor, since=None: entries)
    monkeypatch.setattr(lw, "read_unit", lambda: (active, restarts))
    monkeypatch.setattr(lw, "read_analytics", lambda db, last: ([], last or 0))


class _Sent:
    def __init__(self):
        self.lines = []

    def __call__(self, cmd, **kw):
        if cmd and cmd[0] == lw.LEAD_SAY:
            self.lines.append(cmd[2])

        class R:
            returncode = 0
            stdout = ""
        return R()


DIRTY = [
    _line("ERROR", f"unhandled_exception err=ValueError method=POST "
                   f"path=/book/{PHONE}/{TOKEN}?phone={PHONE}&key={TOKEN} at=app.py:10"),
    _line("WARNING", f"backoffice_error call=quote phone={PHONE} token={TOKEN} ip={IP}"),
    _line("ERROR", f"Ivan Petrenko {PHONE} {TOKEN} {IP}"),
    _line("WARNING", f"client_ip_fallback reason=bad_header remote={IP}"),
]


def test_message_has_no_phone_token_ip(monkeypatch, tmp_path):
    _fake_sources(monkeypatch, DIRTY)
    sent = _Sent()
    monkeypatch.setattr(lw.subprocess, "run", sent)
    assert lw.run(str(tmp_path / "st.json"), None, dry_run=False, now=1_792_740_100) == 0
    assert len(sent.lines) == 1
    msg = sent.lines[0]
    assert "\n" not in msg
    assert _leaks(msg) == [], msg
    assert "unhandled_exception" in msg and "backoffice_error" in msg and "ERROR free_text" in msg
    assert lw.WHERE in msg
    # і стан на диску теж без ПД: підписи там ті самі (числа там — лише мітки часу)
    state = json.loads((tmp_path / "st.json").read_text(encoding="utf-8"))
    assert _leaks(" ".join(state["sigs"])) == []


def test_leak_check_catches_spoiled_sanitizer(monkeypatch, tmp_path):
    """Тест уміє сказати «так»: зіпсований очисник шляху — витік знайдено."""
    monkeypatch.setattr(lw, "clean_path", lambda raw: raw)
    _fake_sources(monkeypatch, DIRTY[:1])
    sent = _Sent()
    monkeypatch.setattr(lw.subprocess, "run", sent)
    lw.run(str(tmp_path / "st.json"), None, dry_run=False, now=1_792_740_100)
    assert PHONE in sent.lines[0]
    assert _leaks(sent.lines[0]) != []


@pytest.mark.parametrize("raw, want", [
    ("/apartment/90398196", "/apartment/N"),
    ("/apartment/d6e3a1b2-0c4d-4e5f-8a9b-0123456789ab", "/apartment/:x"),
    (f"/x/{TOKEN}", "/x/:x"),
    ("/api/quote/123?checkin=2026-11-20", "/api/quote/N"),
    ("/static/img/7.jpg", "/static/img/N.jpg"),
    ("/wp-login.php", "/wp-login.php"),
])
def test_clean_path(raw, want):
    assert lw.clean_path(raw) == want


def test_two_same_errors_one_message(monkeypatch, tmp_path):
    st = str(tmp_path / "st.json")
    sent = _Sent()
    monkeypatch.setattr(lw.subprocess, "run", sent)
    err = _line("ERROR", "http_5xx status=503 method=GET path=/apartment/1")
    # дві підряд в одному прогоні
    _fake_sources(monkeypatch, [err, err])
    lw.run(st, None, dry_run=False, now=1_792_740_100)
    # і ще одна такаж у наступному прогоні за 10 хв
    _fake_sources(monkeypatch, [err])
    lw.run(st, None, dry_run=False, now=1_792_740_700)
    assert len(sent.lines) == 1
    assert "ERROR http_5xx path=/apartment/N ×2" in sent.lines[0]


def test_backoffice_auth_failed_wakes_on_first_line_per_route(monkeypatch, tmp_path):
    """401/403 ключа сайту (app._booking_auth_failed): перша ж строка — сигнал;
    call= у підписі, тож /book і /book-group не злипаються в один лічильник."""
    st = str(tmp_path / "st.json")
    sent = _Sent()
    monkeypatch.setattr(lw.subprocess, "run", sent)
    _fake_sources(monkeypatch, [_line("ERROR", "backoffice_auth_failed call=book status=401"),
                                _line("ERROR", "backoffice_auth_failed call=book_group status=401")])
    lw.run(st, None, dry_run=False, now=1_792_740_100)
    assert len(sent.lines) == 1
    assert "ERROR backoffice_auth_failed call=book ×1" in sent.lines[0]
    assert "ERROR backoffice_auth_failed call=book_group ×1" in sent.lines[0]


def test_resend_after_doubling_and_after_hour(monkeypatch, tmp_path):
    st = str(tmp_path / "st.json")
    sent = _Sent()
    monkeypatch.setattr(lw.subprocess, "run", sent)
    w = _line("WARNING", "backoffice_unavailable status=503 call=apartments")
    _fake_sources(monkeypatch, [w, w])
    lw.run(st, None, dry_run=False, now=1_792_740_100)
    _fake_sources(monkeypatch, [w, w])          # 4 за годину = удвічі від 2
    lw.run(st, None, dry_run=False, now=1_792_740_200)
    _fake_sources(monkeypatch, [])
    lw.run(st, None, dry_run=False, now=1_792_740_300)
    assert len(sent.lines) == 2 and "×4" in sent.lines[1]


def test_404_only_from_five_per_hour(monkeypatch, tmp_path):
    st = str(tmp_path / "st.json")
    sent = _Sent()
    monkeypatch.setattr(lw.subprocess, "run", sent)
    one = _line("WARNING", "http_404 method=GET path=/old-page")
    warn = _line("WARNING", "http_404 method=GET path=/wp-login.php")
    _fake_sources(monkeypatch, [one] * 4 + [warn] * 4)
    lw.run(st, None, dry_run=False, now=1_792_740_100)
    assert sent.lines == []
    _fake_sources(monkeypatch, [warn])
    lw.run(st, None, dry_run=False, now=1_792_740_200)
    assert len(sent.lines) == 1
    assert "WARNING http_404 path=/wp-login.php ×5" in sent.lines[0]
    assert "old-page" not in sent.lines[0]


def test_404_scan_repeats_never_alert(monkeypatch, tmp_path):
    """24.10: той самий шлях ×5 із scan=1 — нуль повідомлень; повтор сканера не наша
    поломка (02:43 — POST /en/ /app/ /api/ /_next/ ×8 будили дирижера)."""
    sent = _Sent()
    monkeypatch.setattr(lw.subprocess, "run", sent)
    scan = _line("INFO", "http_404 scan=1 method=POST path=/wp-login.php")
    _fake_sources(monkeypatch, [scan] * 20)
    lw.run(str(tmp_path / "st.json"), None, dry_run=False, now=1_792_740_100)
    assert sent.lines == []


def test_info_and_quiet_lines_ignored(monkeypatch, tmp_path):
    sent = _Sent()
    monkeypatch.setattr(lw.subprocess, "run", sent)
    _fake_sources(monkeypatch, [_line("INFO", "quote apartment=1 ms=120"),
                                "Booting worker with pid: 5"])
    lw.run(str(tmp_path / "st.json"), None, dry_run=False, now=1_792_740_100)
    assert sent.lines == []


def test_unit_down_and_restarts(monkeypatch, tmp_path):
    st = str(tmp_path / "st.json")
    sent = _Sent()
    monkeypatch.setattr(lw.subprocess, "run", sent)
    _fake_sources(monkeypatch, [], restarts=0)
    lw.run(st, None, dry_run=False, now=1_792_740_100)
    _fake_sources(monkeypatch, ["Traceback (most recent call last):"], active="failed", restarts=2)
    lw.run(st, None, dry_run=False, now=1_792_740_700)
    assert len(sent.lines) == 1
    msg = sent.lines[0]
    assert "UNIT state=failed" in msg and "UNIT restart ×2" in msg and "TRACEBACK" in msg


def test_dry_run_sends_and_saves_nothing(monkeypatch, tmp_path, capsys):
    st = tmp_path / "st.json"
    sent = _Sent()
    monkeypatch.setattr(lw.subprocess, "run", sent)
    _fake_sources(monkeypatch, [_line("ERROR", "http_5xx status=500 method=GET path=/")])
    lw.run(str(st), None, dry_run=True, now=1_792_740_100)
    assert sent.lines == [] and not st.exists()
    assert "ERROR http_5xx path=/ ×1" in capsys.readouterr().out


def test_lead_say_failure_keeps_alert_for_next_run(monkeypatch, tmp_path):
    st = str(tmp_path / "st.json")

    def failing(cmd, **kw):
        class R:
            returncode = 1
            stdout = ""
        return R()
    monkeypatch.setattr(lw.subprocess, "run", failing)
    _fake_sources(monkeypatch, [_line("ERROR", "http_5xx status=500 method=GET path=/")])
    assert lw.run(st, None, dry_run=False, now=1_792_740_100) == 1
    sent = _Sent()
    monkeypatch.setattr(lw.subprocess, "run", sent)
    _fake_sources(monkeypatch, [])
    lw.run(st, None, dry_run=False, now=1_792_740_700)
    assert len(sent.lines) == 1


def test_analytics_rows(tmp_path):
    import sqlite3
    db = tmp_path / "a.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE sessions (sid TEXT PRIMARY KEY, bot INTEGER DEFAULT 0)")
    con.execute("CREATE TABLE events (id INTEGER PRIMARY KEY, ts INTEGER, sid TEXT, kind TEXT,"
                " path TEXT, sel TEXT, n INTEGER, num1 REAL, num2 REAL, str1 TEXT)")
    con.executemany("INSERT INTO sessions VALUES (?,?)", [("g", 0), ("b", 1)])
    ms = 1_792_740_000_000
    rows = [
        (1, ms, "g", "ap", "/", None, 200, 50, None, "/api/quote/1"),
        (2, ms, "g", "je", f"/apartment/{PHONE}", "main.js", 10, None, None, None),
        (3, ms, "g", "ap", "/", None, 503, 80, None, "/api/quote/2"),
        (4, ms, "g", "ap", "/", None, 200, 12000, None, "/api/availability/3"),
        (5, ms, "g", "ap", "/", None, 200, None, 1, "/api/quote/4"),   # фонова вкладка
        (6, ms, "b", "je", "/", "main.js", 1, None, None, None),        # бот
    ]
    con.executemany("INSERT INTO events VALUES (?,?,?,?,?,?,?,?,?,?)", rows)
    con.commit()
    con.close()
    found, last = lw.read_analytics(str(db), 0)
    sigs = [s for _, s in found]
    assert {ts for ts, _ in found} == {1_792_740_000}      # мс бази → секунди сторожа
    assert sigs == ["JS je src=main.js page=/apartment/:x", "API ap_5xx api=/api/quote/N",
                    "API ap_slow api=/api/availability/N"]
    assert last == 6
    assert lw.read_analytics(str(db), 6)[0] == []
    assert json.dumps(sigs).find(PHONE[1:]) == -1


SCANNER_DOT_PATHS = [
    "/.env.example", "/.env.aws", "/.boto", "/.amplifyrc", "/.cursor/mcp.json",
    "/.claude/settings.local.json", "/.docker/config.json", "/.aws/credentials", "/.npmrc",
    "/.git-credentials", "/.netrc", "/.pgpass", "/.htpasswd", "/.s3cfg", "/.terraformrc",
    "/.vercel/project.json", "/.firebaserc", "/.env.local", "/.env.prod", "/.env.dev",
    "/.env.backup", "/.env.save", "/.env.staging", "/.env.test", "/.env.old",
    "/.circleci/config.yml", "/.travis.yml", "/.gitlab-ci.yml", "/.dockercfg", "/.htaccess",
    "/.Trash",
]


@pytest.mark.parametrize("level, scan", [("WARNING", ""), ("INFO", "scan=1 ")])
def test_404_distinct_paths_never_merge(monkeypatch, tmp_path, level, scan):
    """23.10 21:16: 31 різний дот-шлях очищувався в /:x і давав «/:x ×31». Різні
    шляхи — різні лічильники, навіть якщо виглядають однаково."""
    assert len(set(SCANNER_DOT_PATHS)) == 31
    assert {lw.clean_path(p) for p in SCANNER_DOT_PATHS[:4]} == {"/:x"}   # вигляд той самий
    sent = _Sent()
    monkeypatch.setattr(lw.subprocess, "run", sent)
    _fake_sources(monkeypatch, [_line(level, f"http_404 {scan}method=GET path={p}")
                                for p in SCANNER_DOT_PATHS])
    lw.run(str(tmp_path / "st.json"), None, dry_run=False, now=1_792_740_100)
    assert sent.lines == []


def test_404_same_path_five_times_one_message(monkeypatch, tmp_path):
    st = str(tmp_path / "st.json")
    sent = _Sent()
    monkeypatch.setattr(lw.subprocess, "run", sent)
    same = _line("WARNING", "http_404 method=GET path=/.env.aws")
    _fake_sources(monkeypatch, [same] * 5)
    lw.run(st, None, dry_run=False, now=1_792_740_100)
    _fake_sources(monkeypatch, [same])          # шостий у тій самій годині — не нове
    lw.run(st, None, dry_run=False, now=1_792_740_200)
    assert len(sent.lines) == 1
    assert "WARNING http_404 path=/:x ×5" in sent.lines[0]
    assert "#" not in sent.lines[0] and "env" not in sent.lines[0]
    # у стані — хеш, а не сирий шлях
    assert "env" not in (tmp_path / "st.json").read_text(encoding="utf-8")


def test_404_key_exact_path_query_cut():
    """Ключ — точний шлях без query, цифри не згортаються (30.10: /2018/ … /2022/ за одну
    секунду склеїлись у «/N/ ×5»). /apartment/1 і /apartment/2 — теж різні ключі."""
    a = lw.parse_journal_line(_line("WARNING", "http_404 method=GET path=/apartment/1"))
    b = lw.parse_journal_line(_line("WARNING", "http_404 method=GET path=/apartment/1?x=2"))
    c = lw.parse_journal_line(_line("WARNING", "http_404 method=GET path=/apartment/2"))
    assert a == b and a != c


YEAR_PATHS = ["/2018/", "/2019/", "/2020/", "/2021/", "/2022/"]


@pytest.mark.parametrize("level", ["WARNING", "INFO"])
def test_404_five_different_years_no_message(monkeypatch, tmp_path, level):
    """30.10 11:00:32: словниковий сканер, п'ять різних /20NN/ за секунду — нуль повідомлень."""
    sent = _Sent()
    monkeypatch.setattr(lw.subprocess, "run", sent)
    _fake_sources(monkeypatch, [_line(level, f"http_404 method=GET path={p}")
                                for p in YEAR_PATHS])
    lw.run(str(tmp_path / "st.json"), None, dry_run=False, now=1_792_740_100)
    assert sent.lines == []


@pytest.mark.parametrize("path", ["/2019/", "/old-page/"])
def test_404_info_same_path_five_times_one_message(monkeypatch, tmp_path, path):
    """INFO http_404 без scan=1 (будь-який чужий шлях: стара адреса, реклама) рахується як
    WARNING: той самий шлях ×5 за годину — одне повідомлення, шостий — не нове."""
    st = str(tmp_path / "st.json")
    sent = _Sent()
    monkeypatch.setattr(lw.subprocess, "run", sent)
    same = _line("INFO", f"http_404 method=GET path={path}")
    _fake_sources(monkeypatch, [same] * 5)
    lw.run(st, None, dry_run=False, now=1_792_740_100)
    _fake_sources(monkeypatch, [same])
    lw.run(st, None, dry_run=False, now=1_792_740_200)
    assert len(sent.lines) == 1
    assert f"INFO http_404 path={lw.clean_path(path)} ×5" in sent.lines[0]


def test_404_scan_wp_login_five_times_no_message(monkeypatch, tmp_path):
    sent = _Sent()
    monkeypatch.setattr(lw.subprocess, "run", sent)
    scan = _line("INFO", "http_404 scan=1 method=GET path=/wp-login.php")
    _fake_sources(monkeypatch, [scan] * 5)
    lw.run(str(tmp_path / "st.json"), None, dry_run=False, now=1_792_740_100)
    assert sent.lines == []


def test_since_reads_from_time_and_requires_dry_run(monkeypatch, tmp_path, capsys):
    """--since: журнал з часу, а не з курсора; стан не читається й не пишеться."""
    st = tmp_path / "st.json"
    st.write_text(json.dumps({"cursor": "old"}), encoding="utf-8")
    seen = []
    monkeypatch.setattr(lw, "read_journal", lambda cursor, since=None: seen.append(
        (cursor, since)) or [])
    monkeypatch.setattr(lw, "read_unit", lambda: ("active", 0))
    monkeypatch.setattr(lw, "read_analytics", lambda db, last: ([], last or 0))
    assert lw.main(["--dry-run", "--since", "2026-10-30 08:00", "--state", str(st)]) == 0
    assert seen == [(None, "2026-10-30 08:00")]
    assert json.loads(st.read_text(encoding="utf-8")) == {"cursor": "old"}
    with pytest.raises(SystemExit):
        lw.main(["--since", "2026-10-30 08:00", "--state", str(st)])


# ---------- маяк статистики: 24 год без пачок ----------

def _beacon(accepted):
    return _line("INFO", f"analytics_beacon_stats accepted={accepted} dropped=0 origin=0 rate=0 "
                         "body=0 json=0 schema=0 size=0 bot_ua=0 error=0")


def _journal_at(monkeypatch, items):
    """items: [(ts, рядок)] — журнал з власними мітками часу."""
    entries = [(ts, m, f"c{ts}") for ts, m in items]
    monkeypatch.setattr(lw, "read_journal", lambda cursor, since=None: entries)
    monkeypatch.setattr(lw, "read_unit", lambda: ("active", 0))
    monkeypatch.setattr(lw, "read_analytics", lambda db, last: ([], last or 0))


T0 = 1_792_740_000


def test_beacon_24h_without_batches_one_message(monkeypatch, tmp_path):
    st = str(tmp_path / "st.json")
    sent = _Sent()
    monkeypatch.setattr(lw.subprocess, "run", sent)
    _journal_at(monkeypatch, [(T0 - 60, _beacon(0))])           # рядки є, пачок нема
    lw.run(st, None, dry_run=False, now=T0)                       # відлік
    for k in range(1, 6 * 24 + 1):                                # прогони кожні 10 хв, доба
        _journal_at(monkeypatch, [(T0 + k * 600 - 5, _beacon(0))] if k % 6 == 0 else [])
        lw.run(st, None, dry_run=False, now=T0 + k * 600)
    _journal_at(monkeypatch, [])
    lw.run(st, None, dry_run=False, now=T0 + 24 * 3600 + 1200)   # ще прогони — не повторює
    lw.run(st, None, dry_run=False, now=T0 + 30 * 3600)
    assert len(sent.lines) == 1
    assert sent.lines[0].startswith("маяк статистики: 0 прийнятих пачок за 24 год")
    assert not _leaks(sent.lines[0])


def test_beacon_batches_present_no_message(monkeypatch, tmp_path):
    st = str(tmp_path / "st.json")
    sent = _Sent()
    monkeypatch.setattr(lw.subprocess, "run", sent)
    _journal_at(monkeypatch, [])
    lw.run(st, None, dry_run=False, now=T0)
    for k in range(1, 6 * 30 + 1):                                # 30 год, пачка раз на 20 год
        _journal_at(monkeypatch, [(T0 + k * 600 - 5, _beacon(3))] if k % 120 == 0 else [])
        lw.run(st, None, dry_run=False, now=T0 + k * 600)
    assert sent.lines == []


def _load_fresh():
    spec = importlib.util.spec_from_file_location(
        "log_watch_fresh", Path(__file__).resolve().parent.parent / "scripts" / "log_watch.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_places_from_env(monkeypatch, tmp_path):
    """Юніт, тека стану й команда сповіщення — із середовища (публічний код без шляхів машини)."""
    monkeypatch.setenv("SITE_UNIT", "site-unit-x")
    monkeypatch.setenv("SITE_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SITE_NOTIFY_CMD", "/bin/true")
    mod = _load_fresh()
    assert mod.UNIT == "site-unit-x"
    assert mod.STATE_PATH == str(tmp_path / "logwatch-state.json")
    assert mod.DB_PATH == str(tmp_path / "analytics.db")
    assert mod.LEAD_SAY == "/bin/true"


def test_places_default_next_to_checkout(monkeypatch):
    """Без середовища тека даних — <чекаут>-data поруч із чекаутом, юніт — <користувач>-central-aparts-site."""
    for k in ("SITE_UNIT", "SITE_DATA_DIR", "SITE_NOTIFY_CMD"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("USER", "someone")
    monkeypatch.setenv("LOGNAME", "someone")
    mod = _load_fresh()
    checkout = Path(__file__).resolve().parent.parent
    assert mod.DATA_DIR == str(checkout) + "-data"
    assert mod.UNIT == "someone-central-aparts-site"
