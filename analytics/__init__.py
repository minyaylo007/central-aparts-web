"""Своя статистика поведінки гостей (21.10.2026): маяк POST /t і панель /stats.

Вмикається лише змінною ANALYTICS_DB (шлях до SQLite поза репо). Не задано —
на Lambda й у тестах за замовчуванням — маяк відповідає 204 і нічого не пише,
скрипт у сторінку не підключається, /stats — 404.
Ключ панелі — STATS_KEY; не задано — панель закрита (404) навіть при ввімкненому зборі.
Докладно — docs/PATTERNS.md, «Своя статистика».
"""
import atexit
import hmac
import json
import logging
import threading
import time
import zlib
from collections import deque
from urllib.parse import urlsplit

from flask import (Blueprint, abort, jsonify, make_response, redirect, render_template, request,
                   session, url_for)

from analytics import queries, store
from analytics.clean import PATH_RE, SID_RE, clean_batch, is_bot_ua
from config import Config

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

bp = Blueprint("analytics", __name__, template_folder="templates")

MAX_BODY = 64 * 1024        # звичайна пачка подій
MAX_REC_BODY = 512 * 1024   # шматок запису rrweb (стиснений gzip — до розпаковки)
MAX_UNZIPPED = 8 * 1024 * 1024   # межа після розпаковки ?z=1 — від gzip-бомби
RATE_PER_MIN = 120          # маяків на адресу за хвилину, на процес
LOGIN_PER_15MIN = 10

_hits: dict = {}
_hits_lock = threading.Lock()

# Лічильник маяка (30.10): 204 мовчить для гостя, але не для нас. Раз на годину на процес —
# рядок INFO analytics_beacon_stats (прийнято пачок і відкинуто — за причинами); перша прийнята
# пачка після тихого вікна — одразу, щоб сторож (scripts/log_watch.py, «0 пачок за 24 год»)
# не чекав години. Лише числа: ні тіла, ні адрес.
STATS_EVERY_S = 3600
DROP_REASONS = ("origin", "rate", "body", "json", "schema", "size", "bot_ua", "error")
_tally = {"accepted": 0, **{r: 0 for r in DROP_REASONS}}
_tally_t0 = time.monotonic()
_tally_quiet = True         # останній рядок був з accepted=0 (або рядків ще не було)
_tally_lock = threading.Lock()


def _count(outcome: str) -> None:
    """outcome — "accepted" або причина з DROP_REASONS."""
    global _tally_t0
    now = time.monotonic()
    with _tally_lock:
        first_ok = outcome == "accepted" and _tally_quiet and _tally["accepted"] == 0
        _tally[outcome] += 1
        if not (first_ok or now - _tally_t0 >= STATS_EVERY_S):
            return
        line = _stats_line_locked()
        _tally_t0 = now
    logger.info(line)


def _stats_line_locked() -> str:
    global _tally_quiet
    _tally_quiet = _tally["accepted"] == 0
    line = "analytics_beacon_stats accepted=%d dropped=%d %s" % (
        _tally["accepted"], sum(_tally[r] for r in DROP_REASONS),
        " ".join(f"{r}={_tally[r]}" for r in DROP_REASONS))
    for k in _tally:
        _tally[k] = 0
    return line


@atexit.register
def _flush_stats() -> None:
    """Воркер виходить (викат, рестарт) — не губимо годину лічильників."""
    with _tally_lock:
        if not any(_tally.values()):
            return
        line = _stats_line_locked()
    logger.info(line)


def _client_addr() -> str:
    """Адреса лише для лічильника частоти в пам'яті процесу — у базу не йде ніколи.
    Правило те саме, що в app._visitor_ip: N довірених проксі → N-й справа."""
    n = Config.TRUSTED_PROXY_COUNT
    xff = [p.strip() for p in (request.headers.get("X-Forwarded-For") or "").split(",") if p.strip()]
    if n and len(xff) >= n:
        return xff[-n]
    return request.remote_addr or "?"


def _over_limit(bucket: str, limit: int, window_s: int) -> bool:
    now = time.monotonic()
    key = (bucket, _client_addr())
    with _hits_lock:
        if len(_hits) > 20000:
            _hits.clear()
        q = _hits.setdefault(key, deque())
        while q and now - q[0] > window_s:
            q.popleft()
        if len(q) >= limit:
            return True
        q.append(now)
        return False


def _silent():
    return "", 204


def _same_origin() -> bool:
    if request.headers.get("Sec-Fetch-Site") in ("cross-site",):
        return False
    origin = request.headers.get("Origin")
    return not origin or urlsplit(origin).netloc == request.host


def _read_body() -> bytes | None:
    cl = request.content_length
    if cl is not None and cl > MAX_REC_BODY:
        return None
    data = request.stream.read(MAX_REC_BODY + 1)
    if len(data) > MAX_REC_BODY:
        return None
    if request.args.get("z") == "1":   # запис rrweb: браузер стискає CompressionStream('gzip')
        d = zlib.decompressobj(16 + zlib.MAX_WBITS)
        data = d.decompress(data, MAX_UNZIPPED + 1)
        if len(data) > MAX_UNZIPPED or d.unconsumed_tail:
            return None
    return data


@bp.app_context_processor
def _inject():
    return {"analytics_on": store.enabled()}


@bp.route("/t", methods=["POST"])
def beacon():
    """Приймач маяка. Будь-що не так — 204 і тиша для гостя; причину рахує _count."""
    if not store.enabled():
        return _silent()
    _count(_accept())
    return _silent()


def _accept() -> str:
    """Розбір і запис пачки → "accepted" або причина відмови (DROP_REASONS)."""
    try:
        if not _same_origin():
            return "origin"
        if _over_limit("t", RATE_PER_MIN, 60):
            return "rate"
        raw = _read_body()
        if not raw:
            return "body"
        try:
            body = json.loads(raw)
        except ValueError:      # сміття від сканера — не наша поломка, лише лічильник
            return "json"
        parsed = clean_batch(body)
        if parsed is None:
            return "schema"
        sid, pv, events, rec = parsed
        if rec is None and len(raw) > MAX_BODY:   # великою буває лише пачка запису
            return "size"
        if is_bot_ua(request.headers.get("User-Agent")):
            return "bot_ua"   # боти за user-agent — не зберігаємо зовсім
    except Exception as e:   # noqa: BLE001 — маяк не має права впасти
        logger.warning("analytics_beacon_error err=%s", type(e).__name__)
        return "error"
    try:
        store.save_batch(sid, pv, events, rec, bot=False)
    except Exception as e:   # noqa: BLE001 — запис не вдався: гостю 204, нам WARNING
        logger.warning("analytics_store_error err=%s", type(e).__name__)
        return "error"
    return "accepted"


@bp.after_app_request
def _payment_redirect(response):
    """Перехід на оплату бачить лише сервер: POST /book… → 302 на чужий домен.
    Пишемо лічильник без сесії (sessionStorage сервер не бачить) і без адреси."""
    try:
        if (store.enabled() and request.method == "POST" and response.status_code in (302, 303)
                and (request.path.startswith("/book/") or request.path == "/book-group")):
            target = urlsplit(response.headers.get("Location") or "")
            if target.netloc and target.netloc != request.host:
                path = "/book-group" if request.path == "/book-group" else "/book"
                store.save_server_event("srv", path, "pay")
    except Exception as e:   # noqa: BLE001 — бронь важливіша за статистику
        logger.warning("analytics_pay_event_error err=%s", type(e).__name__)
    return response


# ---------------------------------------------------------------------------
# Панель /stats
# ---------------------------------------------------------------------------

def _panel_open() -> bool:
    return store.enabled() and bool(Config.STATS_KEY)


def _authed() -> bool:
    return session.get("stats_ok") == _key_tag()


def _key_tag() -> str:
    """Відбиток ключа в сесії: зміна STATS_KEY розлогінює всіх."""
    return hmac.new(Config.STATS_KEY.encode(), b"stats-panel", "sha256").hexdigest()[:16]


def _days() -> int:
    try:
        return max(1, min(365, int(request.args.get("days", 30))))
    except ValueError:
        return 30


def _when(ts_ms) -> str:
    return time.strftime("%d.%m %H:%M", time.localtime((ts_ms or 0) / 1000))


def _private(resp):
    resp = make_response(resp)
    resp.headers["Cache-Control"] = "no-store"
    resp.headers["X-Robots-Tag"] = "noindex, nofollow"
    resp.headers["Referrer-Policy"] = "no-referrer"
    return resp


@bp.before_request
def _guard_panel():
    if request.path.startswith("/stats"):
        if not _panel_open():
            abort(404)
        if request.endpoint not in ("analytics.stats_login",) and not _authed():
            if request.path.startswith("/stats/api/"):
                abort(403)
            return _private(render_template("stats_login.html", error=False)), 401


@bp.route("/stats/login", methods=["POST"])
def stats_login():
    if _over_limit("login", LOGIN_PER_15MIN, 900):
        return _private(render_template("stats_login.html", error=True)), 429
    key = request.form.get("key", "")
    if key and hmac.compare_digest(key.encode(), Config.STATS_KEY.encode()):
        session["stats_ok"] = _key_tag()
        return redirect(url_for("analytics.stats_home"))
    logger.info("stats_login_failed")
    return _private(render_template("stats_login.html", error=True)), 401


@bp.route("/stats/logout", methods=["POST"])
def stats_logout():
    session.pop("stats_ok", None)
    return redirect(url_for("analytics.stats_home"))


@bp.route("/stats")
def stats_home():
    days = _days()
    con = store.connect()
    try:
        ctx = {
            "days": days,
            "overview": queries.overview(con, days),
            "by_day": queries.visitors_by_day(con, min(days, 90)),
            "sources": queries.sources(con, days),
            "pages": queries.top_pages(con, days),
            "funnel": queries.funnel(con, days),
            "rage": queries.click_problems(con, days, "rg"),
            "dead": queries.click_problems(con, days, "dd"),
            "decor": queries.click_problems(con, days, "dx"),
            "fields": queries.fields(con, days),
            "errors": queries.errors(con, days),
            "vitals": queries.vitals(con, days),
            "scroll": queries.scroll_summary(con, days),
            "heat_pages": queries.heat_pages(con, days),
            "recs": queries.recordings(con, days),
            "when": _when,
        }
    finally:
        con.close()
    return _private(render_template("stats_panel.html", **ctx))


@bp.route("/stats/heat")
def stats_heat():
    days = _days()
    path = request.args.get("path", "/")
    if path.startswith("//") or not PATH_RE.fullmatch(path):   # іде в src iframe — лише свій шлях
        path = "/"
    device = "m" if request.args.get("dev") == "m" else "d"
    con = store.connect()
    try:
        pages = queries.heat_pages(con, days)
        pts = queries.clicks(con, days, path, device)
        scroll = queries.scroll_map(con, days, path, device)
    finally:
        con.close()
    return _private(render_template("stats_heat.html", days=days, path=path, device=device,
                                    pages=pages, points=pts, scroll=scroll))


@bp.route("/stats/replay/<sid>")
def stats_replay(sid):
    if not SID_RE.fullmatch(sid):
        abort(404)
    return _private(render_template("stats_replay.html", sid=sid))


@bp.route("/stats/api/rec/<sid>")
def stats_rec(sid):
    con = store.connect()
    try:
        events = store.load_recording(con, sid)
    finally:
        con.close()
    if not events:
        abort(404)
    return _private(jsonify(events))
