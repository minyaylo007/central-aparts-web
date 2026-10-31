"""
Public Booking Site — Flask application.
Serves www.central-aparts.store: home, apartment detail, booking flow.
All data comes from the backoffice API (cv.central-aparts.store/api/public).
"""
import ipaddress
import json
import logging
import math
import os
import re
import subprocess
import time
import traceback
import uuid
from datetime import datetime
from urllib.parse import quote as _url_quote

from flask import (Flask, render_template, request, redirect, session, jsonify, abort,
                   url_for, flash, send_from_directory, g)
from markupsafe import Markup

from config import Config
from translations import get_t, SUPPORTED_LANGS, DEFAULT_LANG
from api_client import (get_client, BackofficeAuthFailed, BackofficeBusy, BackofficeError,
                        BackofficeUnavailable, quote_cache_status, reset_quote_cache_status)
from analytics import bp as analytics_bp

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)   # Lambda root logger stays WARNING — INFO from this module was being dropped


class _SiteFlask(Flask):
    def log_exception(self, exc_info):
        """Необроблений виняток → ОДИН рядок ERROR замість трейсбеку Flask: тип, метод,
        шлях без query і місце в нашому коді. Трейсбек несе локальні значення й текст
        винятку requests — повний URL із query (купонний телефон); причина та сама, що
        в _log_backoffice_error. Рядок http_5xx для цього запиту вже не пишеться."""
        _, exc, tb = exc_info
        ours = [f for f in traceback.extract_tb(tb) if os.path.dirname(f.filename) == _SITE_DIR]
        at = f"{os.path.basename(ours[-1].filename)}:{ours[-1].lineno}" if ours else "none"
        logger.error("unhandled_exception err=%s method=%s path=%s at=%s",
                     type(exc).__name__, request.method, _log_path(), at)
        g.exception_logged = True


app = _SiteFlask(__name__)
app.secret_key = Config.SECRET_KEY
app.register_blueprint(analytics_bp)   # своя статистика: POST /t і панель /stats


# ---------------------------------------------------------------------------
# Context processor — inject t(), lang, config into every template
# ---------------------------------------------------------------------------

# Стеля гостей на один апартамент: бек-офіс сам обрізає до 4 (public_api, min(4, guests)), а
# власної місткості квартира в публічних даних не віддає. Лічильники в шаблонах і main.js беруть
# число звідси (data-max), щоб не розходились із сервером.
GUESTS_MAX = 4


@app.context_processor
def inject_globals():
    lang = session.get("lang", DEFAULT_LANG)
    return dict(t=get_t(lang), lang=lang, config=Config,
                current_year=datetime.now().year, static_url=static_url,
                critical_css=critical_css, guests_max=GUESTS_MAX)


_SITE_DIR = os.path.dirname(os.path.abspath(__file__))


def _log_backoffice_error(event: str, exc: Exception, level: int = logging.ERROR) -> None:
    """Збій звернення до бек-офісу — у журнал подія, тип винятку, код відповіді й
    місце (файл:рядок нашого коду), без exc_info. Текст винятку requests — повний
    URL із query (у котируванні там coupon_phone), ланцюжок urllib3 несе той самий
    URL; тож ні повідомлення, ні трейсбек у журнал не йдуть (analyze 002, L2)."""
    status = getattr(getattr(exc, "response", None), "status_code", None)
    ours = [f for f in traceback.extract_tb(exc.__traceback__)
            if os.path.dirname(f.filename) == _SITE_DIR]
    at = f"{os.path.basename(ours[-1].filename)}:{ours[-1].lineno}" if ours else "none"
    logger.log(level, "%s err=%s status=%s at=%s", event, type(exc).__name__,
               "none" if status is None else status, at)


def _busy_json(retry_after: int):
    """Spec 002: the ceiling refused (or the backoffice said «temporarily
    unavailable») — 503 busy. main.js only checks r.ok, so for the widget this is
    the same as today's 503 unavailable; Retry-After is for caches and robots.
    The client already logged the event — no line here."""
    return jsonify({"error": "busy"}), 503, {"Retry-After": str(retry_after)}


def _minutes_phrase(n: int, lang: str) -> str:
    """Translation key for the word «minute» after the number n."""
    if lang == "en":
        return "minute_one" if n == 1 else "minute_many"
    if n % 10 == 1 and n % 100 != 11:
        return "minute_one"
    if n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14):
        return "minute_few"
    return "minute_many"


def _booking_unavailable_text(t, lang: str, retry_after: int | None) -> str:
    """Flash for /book when the backoffice said «temporarily unavailable»:
    «…спробуйте за N хвилин» with N = ceil(Retry-After / 60), else without a number."""
    if not retry_after:
        return t("booking_temporarily_unavailable")
    n = math.ceil(retry_after / 60)
    return t("booking_temporarily_unavailable_in").format(n=n, unit=t(_minutes_phrase(n, lang)))


def _booking_auth_failed(t, e: BackofficeAuthFailed) -> None:
    """/book і /book-group: бек-офіс не прийняв ключ сайту (401/403). Поломка наша,
    не гостя — ОДНА строка ERROR backoffice_auth_failed call=<book|book_group> status=N
    (сторож журналу будить дирижера; call= — частина підпису, маршрути не злипаються),
    без ключа, даних гостя й URL. Гостю — «тимчасово недоступне, напишіть нам»."""
    logger.error("backoffice_auth_failed call=%s status=%d", e.call, e.status)
    flash(t("booking_unavailable_write_us"), "error")


# 200 — звичайна віддача файлу; 304 — успішна ревалідація (заголовок оновлює вже збережену
# копію, no-store тут наказав би її викинути); 206 — частина файлу за Range. Решта кодів на
# /static/* — це помилки (404 на одруківку в шляху, 403, 405, 500), і кешувати їх не можна.
_STATIC_CACHEABLE_STATUSES = frozenset({200, 206, 304})


@app.after_request
def static_cache_headers(response):
    """Caches /static/* for 5 minutes in the browser (max-age) and a day in a shared
    cache (s-maxage) — the s-maxage half has no executor since the move off CloudFront
    on 2026-09-13 and applies again only if a cache is ever put in front of the site.
    Non-cacheable statuses get no-store: a 404 must not outlive the typo that caused it.

    22.09: адреса з відбитком версії (`?v=<git_short>`, ставить `static_url()` у шаблоні)
    і збігом із поточною версією дістає рік і `immutable` — такий адрес змінюється разом
    із файлом, тож скидати його нема чого. Будь-який інший випадок — параметра нема, він
    від старого викату, версії нема взагалі — лишається на 300 секундах: на голий
    /static/styles.css приходять закладки й чужі посилання, і вічний кеш там означав би
    гостя, що застряг на старому файлі назавжди."""
    if request.path.startswith("/static/"):
        if response.status_code not in _STATIC_CACHEABLE_STATUSES:
            response.headers["Cache-Control"] = "no-store"
        elif _asset_version_matches(request.args.get("v")):
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        else:
            response.headers["Cache-Control"] = "public, max-age=300, s-maxage=86400"
    return response


# Наші /api/-маршрути (app.py) основою сегмента: [\w-]* після неї ловить опечатки
# (/api/quot, /api/quotee, /api/rate) — вони WARNING. Новий /api/-маршрут — сюди, інакше
# впадуть test_every_get_route_is_own_404_prefix і test_no_route_falls_under_scan_rule.
_OWN_API = r"(avail|quot|rate|coupon|ux)[\w-]*"

# 404 від сканерів: шляхи, яких на сайті не було й не буде (WordPress, PHP, .env, .git,
# адмінки). Їх — INFO з scan=1, щоб сторож журналу (scripts/log_watch.py) не тонув.
# Будь-який сегмент із крапкою на початку (/.env.aws, /.boto, /.cursor/, /.claude/) —
# сканер: у сайту таких адрес нема, крім /.well-known/ (ACME, security.txt), — він
# лишається поза scan=1 (з 30.10 — INFO, бо не наша адреса). Плюс проби секретних конфігів (23.10 21:16, 114 шляхів за секунду).
_SCAN_404_RE = re.compile(
    r"(?i)(\.(php\d?|asp|aspx|jsp|cgi|env|git|svn|ini|bak|sql|ya?ml|log|old|swp)(/|$)"
    r"|/\.(?!well-known(/|$))"
    r"|/(wp-[\w-]*|wordpress|xmlrpc|phpmyadmin|pma|cgi-bin|vendor|admin|administrator"
    r"|boaform|actuator|owa|ecp|autodiscover|hnap1|console|solr|_ignition|server-status"
    r"|appsettings[\w.-]*\.json|credentials[\w.-]*|secrets?\.(json|ya?ml|txt)"
    r"|(app\.)?config\.(json|js)|env(\.(js|json|txt))?|env-config\.js|settings\.json"
    r"|aws-exports\.js|runtime-config\.js|sentry\.properties|phpinfo[\w.-]*"
    r"|composer\.(json|lock)|auth\.json|dockerfile)(/|$))"
    # 24.10: загальні правила замість списку імен. *.json у корені — у нас таких нема,
    # json віддається лише з /static/; сегмент із підкреслення (/_next/, /_environment);
    # суфікси резервних копій (/phpinfo.php~, .orig, .save).
    r"|^/[^/]+\.json$"
    r"|/_"
    r"|(~|\.(bak|old|orig|swp|save))(/|$)"
    # 27.10: останній сегмент *.js / *.mjs поза /static/ — скрейпер видирає рядки з
    # інлайн-скриптів (gtm.js, fbevents.js) і розв'язує їх як відносні адреси. Своїх .js
    # поза /static/ у сайту нема.
    r"|\.m?js$"
    # 31.10: останній сегмент — файл із розширенням (/terraform.tfstate, /private_key.pem,
    # /config/aws.json, /settings.py) — сканер. Список імен цю гонку програє, а файлів поза
    # /static/ сайт віддає лише три — favicon.ico, robots.txt, sitemap.xml у корені. Звідси ж
    # *.json на будь-якій глибині. /.well-known/ (security.txt) — не наш, але й не сканер.
    # Маршрут із розширенням — у виняток, інакше впаде test_no_route_falls_under_scan_rule.
    r"|^(?!/\.well-known/|/(favicon\.ico|robots\.txt|sitemap\.xml)$).*\.[A-Za-z0-9]+$"
    # 31.10: /api/ повз наші API (/api/config, /api/v1/version) — сканер. Наші /api/ кличе
    # лише наш JS, гість їх не набирає і зовнішніх посилань на них нема, тож повтор чужого
    # /api/-шляху — це словник сканера, а не битий лінк. Основи наших — _OWN_API вище.
    r"|^(/en)?/api/(?!" + _OWN_API + r"(/|$))")

# Наші POST-маршрути (app.py і analytics): не-GET 404 під ними — зламана форма чи маяк на
# нашому боці, це WARNING. Решта не-GET 404 — сканер: посилання гість відкриває через GET
# (24.10 02:43 — POST /, /en/, /app/, /api/, /_next/ вісім кругів за 2 с). Голі /api і
# /api/ — сканер. Новий POST-маршрут — сюди, інакше впаде тест у test_http_error_log.
_OWN_POST_RE = re.compile(r"^/(book[\w-]*|t|stats[\w-]*|api/(coupon|ux)[\w-]*)(/|$)")


# Наші адреси (усі маршрути app.py і analytics, плюс /en/ перед ними): 404 під ними —
# мертве посилання чи зламаний виклик на нашому боці, це WARNING. Префікс сегмента з
# [\w-]* ловить і опечатки (/bookk, /apartments, /healthz). Новий маршрут — сюди, інакше
# впаде test_every_get_route_is_own_404_prefix у test_http_error_log.
_OWN_404_RE = re.compile(
    r"^(/en)?/(apartment[\w-]*|book[\w-]*|confirmed[\w-]*|api/" + _OWN_API
    + r"|t|stats[\w-]*|lang"
    r"|health[\w-]*|favicon\.ico|robots\.txt|sitemap\.xml)(/|$)")


def _is_scan_404(path: str, method: str) -> bool:
    if path.startswith("/static/") or _SCAN_404_RE.search(path):
        return True
    return method not in ("GET", "HEAD") and not _OWN_POST_RE.match(path)


def _log_path() -> str:
    """Шлях для журналу: без query (там дати, телефон купона), percent-encoded — щоб
    перенос рядка чи керуючий символ у шляху не підробив рядок журналу; до 200 знаків."""
    return _url_quote(request.path, safe="/-._~:@!$&'()*+,;=")[:200]


# Рівень 404 — білим списком наших адрес, а не чорним списком сканерів: словниковий
# сканер за хвилину перебирає сотню нових імен (30.10 11:00 — 125 рядків, /2019/, /cpanel/,
# /amember/…), і список імен цю гонку програє; а битий зовнішній лінк гостя сторож і так
# побачить по повтору точного шляху в INFO.
@app.after_request
def log_http_errors(response):
    """404 під нашими адресами (_OWN_404_RE) → WARNING `http_404 method= path=`; /static/,
    шляхи сканерів і не-GET повз наші POST-маршрути → INFO scan=1; будь-який інший 404 →
    INFO `http_404 method= path=` без scan=1 (сторож рахує повтори точного шляху й тут).
    5xx → ERROR `http_5xx`. Ні IP, ні реферера, ні query — лише метод і шлях. До 23.10 404
    і 5xx не писались нікуди: access-логу gunicorn нема, журнал Caddy чужий."""
    status = response.status_code
    if status == 404:
        path = _log_path()
        if _is_scan_404(request.path, request.method):
            logger.info("http_404 scan=1 method=%s path=%s", request.method, path)
        elif _OWN_404_RE.match(request.path):
            logger.warning("http_404 method=%s path=%s", request.method, path)
        else:
            logger.info("http_404 method=%s path=%s", request.method, path)
    elif status >= 500 and not g.get("exception_logged"):
        logger.error("http_5xx status=%d method=%s path=%s", status, request.method,
                     _log_path())
    return response


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

def _public_apartments(apartments):
    """Hide test apartments (1-UAH payment-test entries) from public listing."""
    return [a for a in apartments if (a.get("price") or 0) > 1]


@app.route("/")
def home():
    lang = session.get("lang", DEFAULT_LANG)
    client = get_client()
    try:
        apartments = _public_apartments(client.list_apartments(lang=lang))
    except Exception as e:
        _log_backoffice_error("home_list_apartments_error", e)
        apartments = []
    # /?test=1 — owner-only: append the 1-UAH TEST apartment(s) to the list and
    # the hero selector for end-to-end payment tests. The backoffice hides them
    # from its public list, so they are fetched by direct id (unlinked; the
    # public never sees this without the query param).
    if request.args.get("test") == "1":
        for tid in Config.TEST_APARTMENT_IDS:
            try:
                t_apt = client.get_apartment(tid, lang=lang)
                if t_apt and not any(str(a.get("id")) == str(t_apt.get("id")) for a in apartments):
                    apartments.append(t_apt)
            except Exception as e:
                _log_backoffice_error("home_test_apartment_error", e)
    return render_template("public_home.html", apartments=apartments)


@app.route("/apartment/<apt_id>")
def apartment_detail(apt_id):
    lang = session.get("lang", DEFAULT_LANG)
    client = get_client()
    apt = client.get_apartment(apt_id, lang=lang)
    if not apt:
        abort(404)
    try:
        availability = client.get_availability(apt_id)
    except Exception:
        availability = []
    # спека 108: інші публічні квартири — для блоку «Додати ще апартаменти на ці дати».
    # Тестова (1 грн) сама групу не пропонує й у чужий список не потрапляє.
    other_apts = []
    if (apt.get("price") or 0) > 1:
        try:
            other_apts = [{"id": str(a.get("id")), "name": a.get("name") or a.get("code") or ""}
                          for a in _public_apartments(client.list_apartments(lang=lang))
                          if str(a.get("id")) != str(apt.get("id"))]
        except Exception as e:
            _log_backoffice_error("apartment_list_for_group_error", e, logging.WARNING)
    return render_template("public_apartment.html", apt=apt, availability=availability,
                           other_apts=other_apts)


@app.route("/api/availability/<apt_id>")
def availability_api(apt_id):
    """Read-only proxy used by the home-page date search to badge cards."""
    try:
        return jsonify(get_client().get_availability(apt_id))
    except BackofficeBusy:
        return _busy_json(5)
    except BackofficeUnavailable as e:
        return _busy_json(e.retry_after or 5)
    except Exception as e:
        _log_backoffice_error("availability_api_error", e)
        return jsonify({"error": "unavailable"}), 503


def _visitor_ip() -> str:
    """Visitor IP forwarded to the backoffice on coupon-bearing calls, so its
    rate limiter buckets per guest, not per our egress. Mode is
    Config.TRUSTED_PROXY_COUNT (N), read per request (001-trusted-client-ip):
      - unset → first X-Forwarded-For element, else the peer address — the
        pre-001 behaviour; the client can write that element itself;
      - 0 → header ignored, peer address;
      - N ≥ 1 → N-th non-empty element from the right (set by our proxies);
        no header / fewer than N / not an IP → peer address plus one WARNING
        (no header from loopback — our own check past Caddy — INFO).
    No address ever goes to the log — only the reason code and element count."""
    n = Config.TRUSTED_PROXY_COUNT
    if n is None:
        return (request.headers.get("X-Forwarded-For", "").split(",")[0].strip()
                or request.remote_addr or "")
    if n == 0:
        return request.remote_addr or ""
    elements = [e.strip() for e in request.headers.get("X-Forwarded-For", "").split(",")
                if e.strip()]
    if not elements:
        reason = "no_header"
    elif len(elements) < n:
        reason = "fewer_than_n"
    else:
        try:
            ipaddress.ip_address(elements[-n])
            return elements[-n]
        except ValueError:
            reason = "not_ip"
    # Без заголовка з петлі — наша власна перевірка (curl на 127.0.0.1:8004 повз Caddy):
    # Caddy ставить X-Forwarded-For кожному запиту гостя. Це INFO, інакше кожен смок-curl
    # будить сторожа журналу. Без заголовка НЕ з петлі (хтось дістався gunicorn повз
    # проксі) і решта причин — WARNING, як і було.
    level = logging.WARNING
    if reason == "no_header" and _is_loopback(request.remote_addr):
        level = logging.INFO
    logger.log(level, "client_ip_fallback reason=%s elements=%d", reason, len(elements))
    return request.remote_addr or ""


def _is_loopback(addr: str | None) -> bool:
    try:
        return ipaddress.ip_address(addr or "").is_loopback
    except ValueError:
        return False


def _log_client_ip_mode() -> None:
    """One INFO line per process about the _visitor_ip() mode (plus ERROR when
    TRUSTED_PROXY_COUNT is invalid). Never the raw setting, never an address.
    Logging handlers are not configured here: under gunicorn wsgi.py attaches
    this logger to gunicorn's handlers before importing app, so INFO reaches
    the journal; on Lambda the runtime's root handler takes it."""
    if Config.TRUSTED_PROXY_COUNT_INVALID:
        logger.error("client_ip_config_invalid env=TRUSTED_PROXY_COUNT fallback=default")
    n = Config.TRUSTED_PROXY_COUNT
    if n is None:
        logger.info("client_ip_mode mode=default")
    elif n == 0:
        logger.info("client_ip_mode mode=ignore_header")
    else:
        logger.info("client_ip_mode mode=trusted_proxies n=%d", n)


_log_client_ip_mode()


def _log_backoffice_limits_mode() -> None:
    """Spec 002: one INFO line per process for the quote cache and one for the
    backoffice ceiling, preceded by an ERROR per invalid setting — the name and
    the fallback only, never the raw value. Handlers are not configured here
    (same as _log_client_ip_mode)."""
    for env, invalid, fallback in (
            ("QUOTE_CACHE_TTL", Config.QUOTE_CACHE_TTL_INVALID, "off"),
            ("BACKOFFICE_MAX_CONCURRENT", Config.BACKOFFICE_MAX_CONCURRENT_INVALID, "off"),
            ("BACKOFFICE_SLOT_WAIT", Config.BACKOFFICE_SLOT_WAIT_INVALID, "2")):
        if invalid:
            logger.error("backoffice_limits_config_invalid env=%s fallback=%s", env, fallback)
    if Config.QUOTE_CACHE_TTL > 0:
        logger.info("quote_cache_mode mode=on ttl=%d max_entries=%d",
                    Config.QUOTE_CACHE_TTL, Config.QUOTE_CACHE_MAX_ENTRIES)
    else:
        logger.info("quote_cache_mode mode=off")
    if Config.BACKOFFICE_MAX_CONCURRENT > 0:
        logger.info("backoffice_slots_mode mode=on n=%d wait_s=%s",
                    Config.BACKOFFICE_MAX_CONCURRENT, Config.BACKOFFICE_SLOT_WAIT)
    else:
        logger.info("backoffice_slots_mode mode=off")


_log_backoffice_limits_mode()


@app.route("/api/quote/<apt_id>")
def quote_api(apt_id):
    """Read-only proxy: server-truth stay price for the booking widget and the
    home-page search badges. The backoffice computes it with the same code as
    /book, so what the visitor sees here is exactly what Monobank charges.
    Optional coupon_phone: the backoffice adds the returning-guest coupon line
    when the phone has a valid coupon and the stay clears the min-total."""
    check_in = request.args.get("check_in", "")
    check_out = request.args.get("check_out", "")
    guests = request.args.get("guests", "1")
    coupon_phone = request.args.get("coupon_phone", "").strip()
    t0 = time.perf_counter()
    try:
        reset_quote_cache_status()
        q = get_client().get_quote(apt_id, check_in, check_out, guests,
                                   coupon_phone=coupon_phone,
                                   client_ip=_visitor_ip())
        if q is None:
            return jsonify({"error": "invalid"}), 400
        # 31.08: власник просив логи — один рядок на кошторис, без телефону
        logger.info("quote apt=%s ci=%s co=%s g=%s coupon=%s applied=%s discount=%s booking_total=%s ms=%d cache=%s",
                    apt_id, check_in, check_out, guests, bool(coupon_phone), q.get("coupon_applied"),
                    q.get("coupon_discount"), q.get("booking_total"), (time.perf_counter() - t0) * 1000,
                    quote_cache_status())
        return jsonify(q)
    except BackofficeBusy:
        return _busy_json(5)
    except BackofficeUnavailable as e:
        return _busy_json(e.retry_after or 5)
    except Exception as e:
        _log_backoffice_error("quote_api_error", e)
        return jsonify({"error": "unavailable"}), 503


@app.route("/api/quote-group")
def quote_group_api():
    """Спека 108: смета кількох кімнат на спільні дати — проксі до бек-офісу без
    жодної арифметики. Віджет малює строки rooms[], сбор і total рівно з цієї
    відповіді. Помилка бек-офісу → той самий код з {"error", "apartment_id"?}."""
    ids = [i for i in request.args.get("apartment_ids", "").split(",") if i.strip()]
    guests = [g for g in request.args.get("guests", "").split(",") if g.strip()]
    lang = session.get("lang", DEFAULT_LANG)
    t0 = time.perf_counter()
    try:
        q = get_client().get_group_quote([i.strip() for i in ids],
                                         request.args.get("check_in", ""),
                                         request.args.get("check_out", ""),
                                         [_int_or_zero(g) for g in guests], lang=lang)
    except BackofficeBusy:
        return _busy_json(5)
    except BackofficeUnavailable as e:
        return _busy_json(e.retry_after or 5)
    except BackofficeError as e:
        body = {"error": str(e)}
        if e.data.get("apartment_id"):
            body["apartment_id"] = str(e.data["apartment_id"])
        logger.info("quote_group rooms=%d error=%s", len(ids), e)
        status = {"apartment_not_found": 404, "apartment_config_missing": 503}.get(str(e), 400)
        return jsonify(body), status
    except Exception as e:
        _log_backoffice_error("quote_group_api_error", e)
        return jsonify({"error": "unavailable"}), 503
    # без дат і id — лише форма відповіді
    logger.info("quote_group rooms=%d nights=%s available=%s ms=%d", len(ids), q.get("nights"),
                q.get("available"), (time.perf_counter() - t0) * 1000)
    return jsonify(q)


def _int_or_zero(value) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


@app.route("/api/coupon/check", methods=["POST"])
def coupon_check_api():
    """Proxy: returning-guest coupon lookup for the booking widget. The
    backoffice answers a generic {"eligible": false} for anything but a valid
    coupon, and rate-limits per phone+IP — this proxy adds nothing on top."""
    data = request.get_json(silent=True) or {}
    phone = str(data.get("phone", "") or "").strip()
    if not phone:
        return jsonify({"eligible": False})
    try:
        res = get_client().check_coupon(phone, client_ip=_visitor_ip())
        logger.info("coupon_check eligible=%s tail=%s", res.get("eligible"), phone[-4:])
        return jsonify(res)
    except BackofficeBusy:
        return _busy_json(5)
    except BackofficeUnavailable as e:
        return _busy_json(e.retry_after or 5)
    except Exception as e:
        _log_backoffice_error("coupon_check_api_error", e)
        return jsonify({"error": "unavailable"}), 503


@app.route("/api/ux", methods=["POST"])
def ux_event():
    """Client UX beacon (sendBeacon) → one CloudWatch line per event; no storage, no PII.
    Це відповідь на «UI скачет»: measureShift шле, на скільки px посунулась кнопка."""
    data = request.get_json(silent=True, force=True) or {}
    ev = str(data.get("event", ""))[:40]
    if not ev.startswith("cs_"):
        return ("", 204)
    payload = {k: v for k, v in data.items() if k not in ("event", "apt")}
    logger.info("ux_event ev=%s apt=%s data=%s", ev, str(data.get("apt", ""))[:16],
                json.dumps(payload, ensure_ascii=False)[:400])
    return ("", 204)


@app.route("/api/rates/<apt_id>")
def rates_api(apt_id):
    """Read-only proxy: nightly site prices for the booking-calendar day
    hints (same pattern as /api/quote). Purely decorative data — on any
    failure the calendar silently renders without prices."""
    try:
        rates = get_client().get_rates(apt_id)
        if rates is None:
            return jsonify({"error": "not_found"}), 404
        resp = jsonify(rates)
        resp.headers["Cache-Control"] = "public, max-age=900"
        return resp
    except BackofficeBusy:
        return _busy_json(5)
    except BackofficeUnavailable as e:
        return _busy_json(e.retry_after or 5)
    except Exception as e:
        _log_backoffice_error("rates_api_error", e)
        return jsonify({"error": "unavailable"}), 503


_UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


def _booking_idempotency_key(raw: str | None) -> str:
    """Ключ спроби бронювання (спека 107 бек-офісу): main.js кладе у приховане поле
    НОВИЙ UUID на кожен submit — тож повторна відправка того самого POST браузером
    після обриву везе той самий ключ. Беремо лише канонічний UUID малими літерами,
    без пробілів і хвостів; інакше (без JS, стара сторінка з кешу, сміття) — свій
    uuid4, у журнал причина без значення поля."""
    if raw and _UUID_RE.fullmatch(raw):
        return raw
    logger.info("book_idempotency_key_generated reason=%s", "invalid" if raw else "missing")
    return str(uuid.uuid4())


@app.route("/book/<apt_id>", methods=["POST"])
def book(apt_id):
    lang = session.get("lang", DEFAULT_LANG)
    t = get_t(lang)
    client = get_client()

    check_in  = request.form.get("check_in", "").strip()
    check_out = request.form.get("check_out", "").strip()
    name      = request.form.get("name", "").strip()
    phone     = request.form.get("phone", "").strip()
    # returning-guest coupon phone (optional) — re-validated by the backoffice;
    # coupon_expected="1" = the widget showed the -500 line in its last quote,
    # so the backoffice fails closed (409) if the coupon is gone by book time
    coupon_phone = request.form.get("coupon_phone", "").strip()
    coupon_expected = request.form.get("coupon_expected", "").strip()
    try:
        guests = max(1, min(4, int(request.form.get("guests", 1))))
    except (ValueError, TypeError):
        guests = 1

    if not all([check_in, check_out, name, phone]):
        flash(t("fill_all_fields"), "error")
        return redirect(url_for("apartment_detail", apt_id=apt_id))

    idempotency_key = _booking_idempotency_key(request.form.get("idempotency_key"))

    try:
        result = client.create_booking(
            apartment_id=apt_id,
            check_in=check_in,
            check_out=check_out,
            guests=guests,
            name=name,
            phone=phone,
            coupon_phone=coupon_phone,
            coupon_expected=coupon_expected,
            idempotency_key=idempotency_key,
        )
        return redirect(result["payment_url"])
    except BackofficeAuthFailed as e:
        _booking_auth_failed(t, e)
        return redirect(url_for("apartment_detail", apt_id=apt_id, check_in=check_in,
                                check_out=check_out, guests=guests, book=1))
    except BackofficeUnavailable as e:
        # спека 002: техроботи / ліміт бек-офісу — не «Не вдалося створити платіж»,
        # а «спробуйте за N хвилин»; дати й гості — назад, бронь не повторюємо
        logger.warning("book_backoffice_unavailable status=%d retry_after=%s", e.status,
                       "none" if e.retry_after is None else e.retry_after)
        flash(_booking_unavailable_text(t, lang, e.retry_after), "error")
        return redirect(url_for("apartment_detail", apt_id=apt_id, guests=guests, book=1,
                                check_in=check_in, check_out=check_out))
    except BackofficeError as e:
        # B3 (01.09): назад — З датами і прапором book=1, щоб віджет одразу перерахував той
        # самий стей (для coupon_unavailable — за повною ціною) замість порожньої форми
        params = dict(apt_id=apt_id, guests=guests, book=1)
        if str(e) == "dates_unavailable":
            logger.warning("book_dates_unavailable apt=%s ci=%s co=%s", apt_id, check_in, check_out)
            flash(t("dates_unavailable"), "error")            # blocked dates are NOT carried back
        elif str(e) == "too_many_holds":
            # Ліміт неоплачених холдів на телефон (бекофіс). Дати НЕСЕМО назад:
            # гість не зробив нічого поганого — він просто вже тримає стільки ж
            # броней, тож нехай бачить той самий стей і завершить оплату.
            logger.warning("book_too_many_holds apt=%s tail=%s", apt_id, phone[-4:])
            flash(t("too_many_holds"), "error")
            params.update(check_in=check_in, check_out=check_out)
        elif str(e) == "coupon_unavailable":
            logger.warning("book_coupon_unavailable apt=%s tail=%s", apt_id, coupon_phone[-4:])
            flash(t("coupon_no_longer_valid"), "error")
            params.update(check_in=check_in, check_out=check_out, coupon_reset=1)
        else:
            logger.error("book_backoffice_error apt=%s err=%s", apt_id, e)
            flash(t("error_booking"), "error")
            params.update(check_in=check_in, check_out=check_out)
        return redirect(url_for("apartment_detail", **params))
    except Exception as e:
        _log_backoffice_error("book_unexpected_error", e)
        flash(t("error_booking"), "error")
        return redirect(url_for("apartment_detail", apt_id=apt_id, check_in=check_in,
                                check_out=check_out, guests=guests, book=1))


def _catalog(lang: str) -> list:
    """Каталог квартир для назв кімнат групи (кеш 60 с у клієнті); збій → порожньо."""
    try:
        return get_client().list_apartments(lang=lang)
    except Exception as e:
        _log_backoffice_error("catalog_for_group_error", e, logging.WARNING)
        return []


def _group_back(rooms: list, check_in: str, check_out: str, drop: str = ""):
    """Назад на сторінку першої кімнати групи з датами, book=1 і рештою кімнат у
    group=<id>:<гостей>,… — віджет одразу перераховує ту саму групу. drop — id
    зайнятої кімнати: її з вибору прибираємо (якщо це головна — головною стає
    наступна), дати лишаються, бо інші кімнати на них вільні."""
    left = [r for r in rooms if r["apartment_id"] != drop] or rooms
    first, rest = left[0], left[1:]
    params = dict(apt_id=first["apartment_id"], guests=first["guests"], book=1,
                  check_in=check_in, check_out=check_out)
    if rest:
        params["group"] = ",".join(f'{r["apartment_id"]}:{r["guests"]}' for r in rest)
    return redirect(url_for("apartment_detail", **params))


# 400-коди /book-group → ключ перевода. Помилки сайту (ключ, форма) — загальний
# «не вдалося створити платіж»: гість тут нічого виправити не може.
_GROUP_ERROR_TEXT = {
    "need_two_apartments": "group_need_two",
    "too_many_apartments": "group_too_many",
    "duplicate_apartment": "group_duplicate",
    "invalid_guests": "group_invalid_guests",
    "invalid_dates": "group_invalid_dates",
    "too_many_holds": "too_many_holds",
    "payment_unavailable": "group_payment_unavailable",
    "apartment_config_missing": "booking_temporarily_unavailable",
}


@app.route("/book-group", methods=["POST"])
def book_group():
    """Спека 108: одна оплата Monobank за 2–6 кімнат на спільні дати. Форма — та
    сама, що в /book, плюс повторювані room / room_guests (перша — головна заявка).
    Ключ спроби, повтори і журнал — спільний цикл api_client._post_idempotent."""
    lang = session.get("lang", DEFAULT_LANG)
    t = get_t(lang)

    check_in  = request.form.get("check_in", "").strip()
    check_out = request.form.get("check_out", "").strip()
    name      = request.form.get("name", "").strip()
    phone     = request.form.get("phone", "").strip()
    ids = [i.strip() for i in request.form.getlist("room") if i.strip()]
    raw_guests = request.form.getlist("room_guests")
    rooms = [{"apartment_id": apt_id,
              "guests": max(1, min(4, _int_or_zero(raw_guests[n] if n < len(raw_guests) else 1) or 1))}
             for n, apt_id in enumerate(ids)]
    if not rooms:
        return redirect(url_for("home"))
    if not all([check_in, check_out, name, phone]):
        flash(t("fill_all_fields"), "error")
        return _group_back(rooms, check_in, check_out)

    idempotency_key = _booking_idempotency_key(request.form.get("idempotency_key"))

    try:
        result = get_client().create_group_booking(rooms, check_in, check_out, name, phone,
                                                   idempotency_key=idempotency_key)
        return redirect(result["payment_url"])
    except BackofficeAuthFailed as e:
        _booking_auth_failed(t, e)
        return _group_back(rooms, check_in, check_out)
    except BackofficeUnavailable as e:
        logger.warning("book_group_backoffice_unavailable status=%d retry_after=%s rooms=%d",
                       e.status, "none" if e.retry_after is None else e.retry_after, len(rooms))
        flash(_booking_unavailable_text(t, lang, e.retry_after), "error")
        return _group_back(rooms, check_in, check_out)
    except BackofficeError as e:
        code = str(e)
        busy_id = str(e.data.get("apartment_id") or "")
        if code in ("dates_unavailable", "apartment_not_found"):
            # у відповіді id і КОД квартири ("A"), не назва — назву гостю беремо з
            # каталогу: за id, інакше за кодом; не знайшли — текст без назви, не id
            catalog = _catalog(lang)
            code_ = e.data.get("apartment")
            room = (next((a for a in catalog if busy_id and str(a.get("id")) == busy_id), None)
                    or next((a for a in catalog if code_ and a.get("code") == code_), None))
            room_name = (room or {}).get("name") or ""
            logger.warning("book_group_%s rooms=%d", code, len(rooms))
            key = "group_room_unavailable" if code == "dates_unavailable" else "group_room_not_found"
            flash(t(key).format(name=room_name) if room_name else t(key + "_noname"), "error")
            return _group_back(rooms, check_in, check_out, drop=busy_id)
        if code == "idempotency_key_mismatch":
            flash(t("error_booking"), "error")          # клієнт уже записав WARNING
        else:
            logger.warning("book_group_backoffice_error err=%s rooms=%d", code, len(rooms))
            flash(t(_GROUP_ERROR_TEXT.get(code, "error_booking")), "error")
        return _group_back(rooms, check_in, check_out)
    except Exception as e:
        _log_backoffice_error("book_group_unexpected_error", e)
        flash(t("error_booking"), "error")
        return _group_back(rooms, check_in, check_out)


@app.route("/booking-confirmed")
def booking_confirmed():
    lang = session.get("lang", DEFAULT_LANG)
    booking_id = request.args.get("ref", "")
    booking = None
    other_apt = None
    group_rooms = []
    if booking_id:
        try:
            booking = get_client().get_booking(booking_id)
        except Exception:
            pass
    group = (booking or {}).get("group") or {}
    taken = {str((booking or {}).get("apartment_id") or "")}
    if group.get("items"):
        # спека 108: перелік усіх кімнат однієї оплати. Назва — з каталогу за
        # apartment_id; нема в каталозі (тестова, збій) — код квартири, не id
        names = {str(a.get("id")): a.get("name") for a in _catalog(lang)}
        for item in group["items"]:
            apt_id = str(item.get("apartment_id") or "")
            taken.add(apt_id)
            group_rooms.append({"name": names.get(apt_id) or item.get("apartment") or "—",
                                "guests": item.get("guests")})
    if booking and booking.get("apartment_id"):
        try:
            others = [a for a in _public_apartments(get_client().list_apartments(lang=lang))
                      if str(a.get("id")) not in taken]
            other_apt = others[0] if others else None
        except Exception:
            other_apt = None
    return render_template("public_confirmed.html", booking=booking,
                           booking_id=booking_id, other_apt=other_apt,
                           group_rooms=group_rooms, group_total=group.get("total"))


@app.route("/favicon.ico")
def favicon_ico():
    """9 з 70 помилок 404 за першу годину журналу — це /favicon.ico: клієнти, які не
    читають <link> у <head>, просять іконку з кореня. Віддаємо наявний SVG самим файлом,
    а не редиректом: один обхід замість двох, і не залежимо від клієнта, що редирект не
    ходить (саме такі сюди й приходять). Розширення в адресі нічого не обіцяє про формат —
    обіцяє Content-Type, а він тут чесний: image/svg+xml. Нового .ico не заводимо.
    Заголовок кешу ставимо вручну: шлях не під /static/, тож static_cache_headers його
    не бачить; значення — те саме, що там при 200."""
    resp = send_from_directory(app.static_folder, "favicon.svg",
                               mimetype="image/svg+xml")
    resp.headers["Cache-Control"] = "public, max-age=300, s-maxage=86400"
    return resp


@app.route("/robots.txt")
def robots_txt():
    # 31.08: URL будуються від хоста, що реально обслуговує запит (url_root) — щоб
    # sitemap ніколи не розійшовся з доменом (SITE_URL у конфізі показує на www)
    root = request.url_root.rstrip("/")
    body = f"User-agent: *\nAllow: /\nSitemap: {root}/sitemap.xml\n"
    from flask import Response
    return Response(body, mimetype="text/plain")


@app.route("/sitemap.xml")
def sitemap_xml():
    root = request.url_root.rstrip("/")
    urls = [f"{root}/"]
    try:
        for a in _public_apartments(get_client().list_apartments(lang=DEFAULT_LANG)):
            urls.append(f"{root}/apartment/{a.get('id')}")
    except Exception as e:
        _log_backoffice_error("sitemap_apartments_failed", e, logging.WARNING)
    items = "".join(f"<url><loc>{u}</loc></url>" for u in urls)
    body = ('<?xml version="1.0" encoding="UTF-8"?>'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
            f"{items}</urlset>")
    from flask import Response
    return Response(body, mimetype="application/xml")


# ---------------------------------------------------------------------------
# /health — ЯКИЙ КОМІТ тут працює (образець: aparts/backend/app.py build_info)
# ---------------------------------------------------------------------------

_BUILD_INFO = None   # кеш на весь процес: файл читається один раз, не на запит


def _git_build_info() -> dict:
    """Запасний шлях: BUILD_INFO.json нема (на коробці забули проштампувати) —
    питаємо git один раз. На Lambda git-а й теки .git нема → {} і порожні поля."""
    def _git(*args) -> str:
        try:
            return subprocess.check_output(
                ["git", *args], cwd=os.path.dirname(os.path.abspath(__file__)),
                text=True, stderr=subprocess.DEVNULL, timeout=5).strip()
        except Exception:
            return ""
    sha = _git("rev-parse", "HEAD")
    if not sha:
        return {}
    return {"git_sha": sha, "git_short": sha[:7],
            "git_subject": _git("log", "-1", "--pretty=%s")[:120],
            "built_at": "", "source": "git"}


def build_info() -> dict:
    """BUILD_INFO.json у корені репозиторію — пише scripts/stamp_build.py після
    `git pull` і перед перезапуском юніта (git commit, built_at). Нема файлу →
    питаємо git; нема й git → {}. Кешується на весь час життя процесу."""
    global _BUILD_INFO
    if _BUILD_INFO is not None:
        return _BUILD_INFO
    info = {}
    try:
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "BUILD_INFO.json"),
                  encoding="utf-8") as f:
            info = json.load(f) or {}
    except Exception:
        info = {}
    if not info.get("git_sha"):
        info = _git_build_info()
    _BUILD_INFO = info
    return info


def _asset_version() -> str:
    """Відбиток версії для адрес статики — короткий sha з build_info(). build_info()
    кешує себе на весь процес (BUILD_INFO.json або git читаються один раз), тож на
    запиті тут ні файлу, ні git. Порожньо там, де версії нема (Lambda, копія без .git
    і без BUILD_INFO.json) — і тоді адреса лишається без параметра, як до 22.09."""
    return (build_info().get("git_short") or "").strip()


def static_url(filename: str) -> str:
    """Адреса статики з відбитком версії: /static/<файл>?v=<git_short>. Новий викат
    дає новий адрес, тому сам файл можна віддавати з річним кешем (static_cache_headers).
    Версії нема → адреса без параметра, тобто рівно як до 22.09."""
    v = _asset_version()
    return f"/static/{filename}?v={v}" if v else f"/static/{filename}"


_CRITICAL_CSS: dict = {}
_CRITICAL_GROUPS = ("home", "apartment", "other")


def critical_css(group: str = "other") -> Markup:
    """Правила першого екрана для інлайн-<style> у public_base.html — static/critical/<група>.css
    (генерує scripts/build_css.sh; групи — GROUPS у e2e/tools/build-css.mjs). Читається раз на
    процес. Markup — бо в CSS є `>`, і автоекранування зробило б з нього `&gt;`; вміст наш, із
    репозиторію, не від гостя. Файлу нема → порожньо: сторінка все одно стане, просто перший
    кадр — після styles.css."""
    if group not in _CRITICAL_GROUPS:
        group = "other"
    if group not in _CRITICAL_CSS:
        try:
            with open(os.path.join(_SITE_DIR, "static", "critical", f"{group}.css"),
                      encoding="utf-8") as f:
                css = f.read()
        except OSError:
            css = ""
        _CRITICAL_CSS[group] = Markup(css.replace("</", "<\\/"))
    return _CRITICAL_CSS[group]


def _asset_version_matches(v) -> bool:
    """Чи це відбиток САМЕ цього викату. Порожня версія збігом не вважається: інакше
    на коробці без версії порожній `?v=` дістав би вічний кеш."""
    current = _asset_version()
    return bool(current) and v == current


@app.route("/health")
def health():
    """Публічна перевірка живості + ЯКИЙ КОМІТ тут відповідає. Секретів не
    віддає: ні ключів, ні BACKOFFICE_URL, ні SECRET_KEY. `function_version`
    порожній під gunicorn і заповнений на Lambda — так і відрізняють коробку
    від Lambda після переключення DNS.

    Cache-Control: no-store — обов'язково. CodeUri у infra/template.yaml це '../',
    тобто CloudFront стоїть перед усім сайтом; закешована /health брехала б про
    версію. after_request static_cache_headers чіпає лише /static/*, тож цей
    заголовок він не перебиває."""
    b = build_info()
    resp = jsonify({
        "ok": True,
        "git_sha": b.get("git_sha", ""),
        "git_short": b.get("git_short", ""),
        "git_subject": b.get("git_subject", ""),
        "built_at": b.get("built_at", ""),
        "env": Config.ENV,
        "function_version": os.environ.get("AWS_LAMBDA_FUNCTION_VERSION", ""),
    })
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.route("/lang/<code>")
def set_lang(code):
    if code in SUPPORTED_LANGS:
        session["lang"] = code
    return redirect(request.referrer or url_for("home"))


# ---------------------------------------------------------------------------
# Error handlers
# ---------------------------------------------------------------------------

@app.errorhandler(404)
def not_found(e):
    return render_template("public_error.html", code=404), 404


@app.errorhandler(500)
def server_error(e):
    return render_template("public_error.html", code=500), 500
