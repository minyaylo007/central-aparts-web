"""wsgi.py під gunicorn: рядки застосунку рівня INFO доходять до тих самих
обробників, що й журнал gunicorn (stderr → journald), і без жодної адреси.

Обробники — справжній gunicorn.glogging.Logger, як у воркері; потік
підмінено буфером. Адреси — лише документаційні, телефон синтетичний."""
import io
import logging
import re

import pytest

import wsgi
from config import Config

PHONE = "+380000000000"
QUOTE_ARGS = {"check_in": "2030-06-01", "check_out": "2030-06-03",
              "guests": "2", "coupon_phone": PHONE}
# [2026-09-11 16:00:00 +0300] [12345] [INFO] message — формат gunicorn.error
def line_re(level, msg):
    return rf"^\[\d{{4}}-\d\d-\d\d \d\d:\d\d:\d\d [+-]\d{{4}}\] \[\d+\] \[{level}\] {msg}$"


@pytest.fixture
def gunicorn_log():
    """Налаштування журналу gunicorn-воркера; після тесту — усе як було,
    щоб caplog інших тестів не змінився."""
    from gunicorn.config import Config as GunicornConfig
    from gunicorn.glogging import Logger

    names = ("gunicorn.error", *wsgi.APP_LOGGERS)
    saved = {n: (logging.getLogger(n).handlers[:], logging.getLogger(n).level,
                 logging.getLogger(n).propagate) for n in names}
    glog = Logger(GunicornConfig())
    buf = io.StringIO()
    for handler in glog.error_log.handlers:
        handler.setStream(buf)
    yield glog.error_log, buf
    for n, (handlers, level, propagate) in saved.items():
        lg = logging.getLogger(n)
        lg.handlers[:] = handlers
        lg.setLevel(level)
        lg.propagate = propagate


@pytest.fixture
def trusted(monkeypatch):
    def _set(n):
        monkeypatch.setattr(Config, "TRUSTED_PROXY_COUNT", n)
        monkeypatch.setattr(Config, "TRUSTED_PROXY_COUNT_INVALID", False)
    return _set


def test_startup_mode_line_reaches_gunicorn_handler(gunicorn_log, trusted, caplog):
    import app as app_module
    _, buf = gunicorn_log
    trusted(1)
    caplog.set_level(logging.INFO, logger="app")
    wsgi.attach_app_logging()
    app_module._log_client_ip_mode()
    lines = buf.getvalue().splitlines()
    assert len(lines) == 1
    assert re.match(line_re("INFO", "client_ip_mode mode=trusted_proxies n=1"), lines[0]), lines[0]
    # caplog (pytest, корінь) бачить ту саму одну запис — поширення не змінилось
    assert [r.getMessage() for r in caplog.records if r.name == "app"] == [
        "client_ip_mode mode=trusted_proxies n=1"]


def test_attach_is_idempotent(gunicorn_log, trusted):
    import app as app_module
    _, buf = gunicorn_log
    trusted(None)
    wsgi.attach_app_logging()
    wsgi.attach_app_logging()
    app_module._log_client_ip_mode()
    assert buf.getvalue().count("client_ip_mode") == 1


def test_noop_without_gunicorn_handlers():
    before = {n: logging.getLogger(n).handlers[:] for n in wsgi.APP_LOGGERS}
    wsgi.attach_app_logging(logging.getLogger("test.wsgi.no_handlers"))
    assert {n: logging.getLogger(n).handlers[:] for n in wsgi.APP_LOGGERS} == before


@pytest.mark.critical
def test_request_lines_reach_handler_without_addresses(gunicorn_log, trusted, client, mock_client):
    """quote і coupon_check (INFO) та відкат (WARNING) — у журналі; адрес і повного
    телефону там нема."""
    _, buf = gunicorn_log
    wsgi.attach_app_logging()
    trusted(1)
    r = client.get("/api/quote/apt-uuid-a", query_string=QUOTE_ARGS,
                   headers={"X-Forwarded-For": "198.51.100.23, 203.0.113.7"},
                   environ_base={"REMOTE_ADDR": "127.0.0.1"})
    assert r.status_code == 200
    r = client.post("/api/coupon/check", json={"phone": PHONE},
                    headers={"X-Forwarded-For": "203.0.113.7"},
                    environ_base={"REMOTE_ADDR": "127.0.0.1"})
    assert r.status_code == 200
    trusted(2)
    client.post("/api/coupon/check", json={"phone": PHONE},
                headers={"X-Forwarded-For": "203.0.113.7"},
                environ_base={"REMOTE_ADDR": "192.0.2.50"})

    text = buf.getvalue()
    levels = [re.search(r"\] \[(\w+)\] (\w+)", ln).groups() for ln in text.splitlines()]
    assert levels == [("INFO", "quote"), ("INFO", "coupon_check"),
                      ("WARNING", "client_ip_fallback"), ("INFO", "coupon_check")]
    for leaked in ("198.51.100.23", "203.0.113.7", "192.0.2.50", "127.0.0.1", PHONE):
        assert leaked not in text


# ---------- логгер analytics і сторож журналу (31.10) ----------
# 30.10–31.10: analytics_beacon_stats (INFO) не доходили до журналу — логгера "analytics"
# не було в APP_LOGGERS. Сторож (scripts/log_watch.py) не бачив accepted>0 і щодоби
# піднімав «0 прийнятих пачок»; WARNING analytics_* ішли через last-resort без формату
# gunicorn, і _LINE_RE сторожа їх не розбирав.

import ast  # noqa: E402
import importlib.util  # noqa: E402
from pathlib import Path  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
_SPEC = importlib.util.spec_from_file_location("log_watch", ROOT / "scripts" / "log_watch.py")
lw = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(lw)

# Не код застосунку під gunicorn: окремі скрипти, тести, смока, документи.
_NOT_APP = {"tests", "scripts", "e2e", "deploy", "specs", "docs", "node_modules"}


def _app_modules():
    """(ім'я модуля, файл) для кожного .py застосунку в корені й пакетах."""
    for f in sorted(ROOT.rglob("*.py")):
        rel = f.relative_to(ROOT)
        if rel.parts[0] in _NOT_APP or rel.parts[0].startswith("."):
            continue
        if rel.name in ("wsgi.py", "wsgi_adapter.py", "conftest.py"):
            continue
        parts = list(rel.with_suffix("").parts)
        if parts[-1] == "__init__":
            parts.pop()
        yield ".".join(parts), f


def _logger_names(module, path):
    """Імена з logging.getLogger(...) у файлі: __name__ → ім'я модуля, рядок — як є."""
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if (isinstance(node, ast.Call) and getattr(node.func, "attr", None) == "getLogger"
                and node.args):
            arg = node.args[0]
            if isinstance(arg, ast.Name) and arg.id == "__name__":
                yield module
            elif isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                yield arg.value


def _covered(name):
    """Логгер дійде до обробників gunicorn: він сам у APP_LOGGERS або його предок
    (дочірні analytics.* поширюються вгору до "analytics")."""
    return any(name == a or name.startswith(a + ".") for a in wsgi.APP_LOGGERS)


def test_every_app_logger_is_attached():
    """Наступний модуль із getLogger(__name__) не загубиться мовчки, як analytics."""
    found = {n for mod, f in _app_modules() for n in _logger_names(mod, f)}
    assert {"app", "api_client", "analytics"} <= found, found   # сам обхід працює
    missing = sorted(n for n in found if not _covered(n))
    assert not missing, f"додай у wsgi.APP_LOGGERS: {missing}"


@pytest.mark.critical
def test_beacon_stats_reach_journal_and_log_watch_reads_them(gunicorn_log, monkeypatch):
    """Наскрізно: _count → INFO analytics_beacon_stats accepted=3 у форматі gunicorn →
    сторож бачить accepted=3."""
    import time as _time

    import analytics
    _, buf = gunicorn_log
    wsgi.attach_app_logging()
    tally = {k: 0 for k in analytics._tally}
    tally["accepted"] = 2
    monkeypatch.setattr(analytics, "_tally", tally)
    monkeypatch.setattr(analytics, "_tally_quiet", False)
    monkeypatch.setattr(analytics, "_tally_t0", _time.monotonic() - analytics.STATS_EVERY_S)
    analytics._count("accepted")

    lines = buf.getvalue().splitlines()
    assert len(lines) == 1, lines
    assert re.match(line_re("INFO", r"analytics_beacon_stats accepted=3 dropped=0 .*"), lines[0])
    assert lw.beacon_accepted(lines[0]) == 3


@pytest.mark.critical
def test_analytics_warning_is_parsed_by_log_watch(gunicorn_log):
    """WARNING analytics_store_error — у форматі gunicorn, сторож бачить підпис."""
    import analytics
    _, buf = gunicorn_log
    wsgi.attach_app_logging()
    analytics.logger.warning("analytics_store_error err=%s", "OperationalError")
    lines = buf.getvalue().splitlines()
    assert len(lines) == 1, lines
    assert lw.parse_journal_line(lines[0]) == ("WARNING analytics_store_error", False)
