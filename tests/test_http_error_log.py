"""23.10.2026 (OWNER-RULES п.42): 404 і 5xx — у журнал одним рядком, без query, IP і
реферера; /static/ і сканери — INFO scan=1; необроблений виняток — один ERROR без
трейсбеку. З 30.10 WARNING — лише 404 під нашими адресами, решта — INFO без scan=1
(сторож рахує її повтори так само). Сторож журналу (scripts/log_watch.py) читає саме ці
рядки."""
import logging
import re

import pytest

# critical: повторні 404 публічних посилань і 5xx гостя мають бути видні
pytestmark = pytest.mark.critical

PHONE = "+380000000000"


def _app_records(caplog):
    return [r for r in caplog.records if r.name == "app"
            and r.getMessage().startswith(("http_", "unhandled_exception"))]


@pytest.mark.parametrize("path, level", [("/no-such-page", logging.INFO),
                                         ("/apartment/x/y", logging.WARNING)])
def test_404_one_line_without_query(client, caplog, path, level):
    caplog.set_level(logging.INFO, logger="app")
    r = client.get(path, query_string={"coupon_phone": PHONE, "t": "secret"},
                   headers={"Referer": "https://example.test/ref", "X-Forwarded-For": "203.0.113.7"})
    assert r.status_code == 404
    recs = _app_records(caplog)
    assert [(x.levelno, x.getMessage()) for x in recs] == [
        (level, f"http_404 method=GET path={path}")]
    for leak in ("380000000000", "secret", "example.test", "203.0.113.7", "?"):
        assert leak not in caplog.text


def test_404_apartment_from_api(client, caplog):
    """Мертве посилання на квартиру (бек-офіс сказав 404) — теж WARNING."""
    caplog.set_level(logging.INFO, logger="app")
    assert client.get("/apartment/gone-uuid").status_code == 404
    assert [x.getMessage() for x in _app_records(caplog)] == [
        "http_404 method=GET path=/apartment/gone-uuid"]


@pytest.mark.parametrize("path", ["/wp-login.php", "/.env", "/.git/config", "/xmlrpc.php",
                                  "/wp-admin/setup-config.php", "/static/nope.css"])
def test_404_scan_and_static_are_info(client, caplog, path):
    caplog.set_level(logging.INFO, logger="app")
    assert client.get(path).status_code == 404
    recs = _app_records(caplog)
    assert len(recs) == 1
    assert recs[0].levelno == logging.INFO
    assert recs[0].getMessage().startswith("http_404 scan=1 method=GET path=")


# 23.10 21:16: один сканер, 114 шляхів за секунду; до правки ці йшли WARNING
SCANNER_PROBES = [
    "/.env.example", "/.env.aws", "/.boto", "/.amplifyrc", "/.cursor/mcp.json",
    "/.claude/settings.local.json", "/.docker/config.json", "/.Trash/x",
    "/appsettings.json", "/appsettings.Production.json", "/credentials.json", "/credentials",
    "/api/credentials.json", "/secrets.json", "/config.json", "/config.js", "/env.js",
    "/env.txt", "/aws-exports.js", "/src/aws-exports.js", "/runtime-config.js",
    "/sentry.properties", "/phpinfo", "/composer.json", "/auth.json",
    "/Dockerfile", "/env", "/env-config.js", "/app.config.js", "/settings.json",
]


@pytest.mark.parametrize("path", SCANNER_PROBES)
def test_404_secret_probes_are_scan(client, caplog, path):
    caplog.set_level(logging.INFO, logger="app")
    assert client.get(path).status_code == 404
    recs = _app_records(caplog)
    assert [(x.levelno, x.getMessage().split(" path=")[0]) for x in recs] == [
        (logging.INFO, "http_404 scan=1 method=GET")]


@pytest.mark.parametrize("path", ["/apartment/no-such-apartment", "/bookk", "/book",
                                  "/booking-confirmedd", "/api/quotee/1", "/stats/x", "/t/x",
                                  "/healthz", "/apartment/config-room"])
def test_404_site_typos_stay_warning(client, mock_client, caplog, path):
    """Опечатки в адресах сайту й /.well-known/ — не сканер: їх має бачити сторож."""
    caplog.set_level(logging.INFO, logger="app")
    assert client.get(path).status_code == 404
    recs = _app_records(caplog)
    assert [x.levelno for x in recs] == [logging.WARNING], [x.getMessage() for x in recs]


def test_404_path_cannot_forge_a_log_line(client, caplog):
    caplog.set_level(logging.INFO, logger="app")
    client.get("/x%0A[ERROR]%20fake")
    msg = _app_records(caplog)[0].getMessage()
    assert "\n" not in msg and " " not in msg.split("path=", 1)[1]


def test_5xx_response_is_error(client, mock_client, caplog, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("down")
    monkeypatch.setattr(mock_client, "get_availability", boom)
    caplog.set_level(logging.INFO, logger="app")
    r = client.get("/api/availability/apt-uuid-a")
    assert r.status_code == 503
    recs = [x for x in _app_records(caplog)]
    assert [(x.levelno, x.getMessage()) for x in recs] == [
        (logging.ERROR, "http_5xx status=503 method=GET path=/api/availability/apt-uuid-a")]


def test_unhandled_exception_one_error_line_no_traceback(app, client, mock_client, caplog,
                                                        monkeypatch):
    monkeypatch.setitem(app.config, "PROPAGATE_EXCEPTIONS", False)

    def boom(*a, **k):
        raise ValueError(f"phone {PHONE} in message")
    monkeypatch.setattr(mock_client, "get_apartment", boom)
    caplog.set_level(logging.INFO)
    r = client.get("/apartment/apt-uuid-a", query_string={"coupon_phone": PHONE})
    assert r.status_code == 500
    recs = _app_records(caplog)
    assert len(recs) == 1
    msg = recs[0].getMessage()
    assert recs[0].levelno == logging.ERROR
    assert msg.startswith("unhandled_exception err=ValueError method=GET "
                          "path=/apartment/apt-uuid-a at=app.py:")
    assert recs[0].exc_info is None
    assert "380000000000" not in caplog.text
    assert "Traceback" not in caplog.text


def test_ok_pages_log_nothing(client, caplog):
    caplog.set_level(logging.INFO, logger="app")
    assert client.get("/").status_code == 200
    assert _app_records(caplog) == []


# 24.10 02:43: POST-проба Next.js / RSC — 32 запити за 2 с; 02:02 — GET-проба ключів хмар.
# До правки все це йшло WARNING і будило сторожа.
POST_PROBES = ["/en/", "/app/", "/api/", "/api", "/_next/"]
CLOUD_KEY_PROBES = [
    "/service-account.json", "/sa.json", "/key.json", "/keyfile.json", "/gcp-key.json",
    "/gcp-sa.json", "/gcp-credentials.json", "/google-key.json", "/google-credentials.json",
    "/firebase-key.json", "/firebase-adminsdk.json", "/application_default_credentials.json",
    "/_environment", "/phpinfo.php~", "/index.bak", "/app.py.orig", "/config.save",
]


def _levels(caplog):
    return [(x.levelno, x.getMessage().split(" path=")[0]) for x in _app_records(caplog)]


@pytest.mark.parametrize("path", POST_PROBES)
@pytest.mark.parametrize("method", ["POST", "PUT", "DELETE", "PATCH"])
def test_404_non_get_outside_our_post_routes_is_scan(client, caplog, path, method):
    """Посилання гість відкриває через GET: не-GET 404 повз наші POST-маршрути — сканер."""
    caplog.set_level(logging.INFO, logger="app")
    assert client.open(path, method=method).status_code == 404
    assert _levels(caplog) == [(logging.INFO, f"http_404 scan=1 method={method}")]


@pytest.mark.parametrize("path", CLOUD_KEY_PROBES)
def test_404_cloud_key_probes_are_scan(client, caplog, path):
    caplog.set_level(logging.INFO, logger="app")
    assert client.get(path).status_code == 404
    assert _levels(caplog) == [(logging.INFO, "http_404 scan=1 method=GET")]


@pytest.mark.parametrize("path", ["/book-groupp", "/book/x/y", "/bookk", "/api/coupon/checkk",
                                  "/api/uxx", "/t/x", "/stats/loginn"])
def test_404_post_under_our_post_routes_stays_warning(client, mock_client, caplog, path):
    """Зламана форма чи маяк на нашому боці — не сканер: сторож має це бачити."""
    caplog.set_level(logging.INFO, logger="app")
    assert client.post(path).status_code == 404
    assert [x.levelno for x in _app_records(caplog)] == [logging.WARNING], \
        [x.getMessage() for x in _app_records(caplog)]


@pytest.mark.parametrize("path", ["/api/quote/nope/x", "/api/quot", "/api/rate/x",
                                  "/apartment/no-such-apartment"])
def test_404_get_near_misses_stay_warning(client, mock_client, caplog, path):
    """Опечатка під нашою адресою, і в /api/ теж, — не сканер, WARNING."""
    caplog.set_level(logging.INFO, logger="app")
    assert client.get(path).status_code == 404
    assert [x.levelno for x in _app_records(caplog)] == [logging.WARNING]


# 30.10 11:00: словниковий сканер, 125 різних каталогів за хвилину (/2019/, /cpanel/,
# /amember/…) — список імен у _SCAN_404_RE цю гонку програє. Не наша адреса — INFO без
# scan=1: сторож рахує повтор точного шляху (стара адреса, реклама), сплеск різних — ні.
FOREIGN_404 = ["/2019/", "/cpanel/", "/amember/admin-users/", "/colors/ocean/",
               "/info", "/en/privacy-policy-2/", "/static.json/x", "/.well-known/security.txt",
               "/old-page/", "/api", "/en/"]


@pytest.mark.parametrize("path", FOREIGN_404)
def test_404_foreign_paths_are_info_without_scan(client, mock_client, caplog, path):
    caplog.set_level(logging.INFO, logger="app")
    assert client.get(path).status_code == 404
    assert _levels(caplog) == [(logging.INFO, "http_404 method=GET")]


@pytest.mark.parametrize("path", ["/apartment/no-such-apartment", "/bookk",
                                  "/api/quotes", "/en/apartment/x", "/apartments",
                                  "/booking-confirmed/x", "/stats/nope", "/lang/en/x"])
def test_404_own_prefixes_are_warning(client, mock_client, caplog, path):
    caplog.set_level(logging.INFO, logger="app")
    assert client.get(path).status_code == 404
    assert _levels(caplog) == [(logging.WARNING, "http_404 method=GET")]


def test_every_get_route_is_own_404_prefix(app):
    """Новий маршрут без місця в _OWN_404_RE сховав би свої мертві посилання в INFO."""
    from app import _OWN_404_RE
    for rule in app.url_map.iter_rules():
        if rule.endpoint == "static" or rule.rule == "/":
            continue
        # лише маршрути сайту: e2e/fake_wsgi.py у тому ж процесі вішає /__fake__/state
        if app.view_functions[rule.endpoint].__module__.split(".")[0] not in ("app", "analytics"):
            continue
        sample = re.sub(r"<[^>]+>", "x", rule.rule)
        assert _OWN_404_RE.match(sample), rule.rule


def test_head_404_is_like_get(client, caplog):
    caplog.set_level(logging.INFO, logger="app")
    assert client.head("/bookk").status_code == 404
    assert [x.levelno for x in _app_records(caplog)] == [logging.WARNING]


def test_every_post_route_is_exempt_from_non_get_scan(app):
    """Новий POST-маршрут без місця в _OWN_POST_RE тихо сховав би зламану форму в INFO."""
    from app import _OWN_POST_RE
    for rule in app.url_map.iter_rules():
        if "POST" in (rule.methods or ()):
            sample = rule.rule.replace("<apt_id>", "x")
            assert _OWN_POST_RE.match(sample), rule.rule


# 27.10 17:03, усередині скан-сплеску: GET /gtm.js і /apartment/gtm.js — скрейпер видер
# рядок 'gtm.js' з інлайн-скрипта GTM у public_base.html і розв'язав його як відносну
# адресу; так само йде fbevents.js. Своїх .js поза /static/ у сайту нема — будь-який шлях
# з останнім сегментом *.js — сканер.
@pytest.mark.parametrize("path", ["/gtm.js", "/apartment/gtm.js", "/en/fbevents.js",
                                  "/apartment/apt-uuid-a/main.mjs"])
def test_404_scraped_js_names_are_scan(client, mock_client, caplog, path):
    caplog.set_level(logging.INFO, logger="app")
    assert client.get(path).status_code == 404
    assert _levels(caplog) == [(logging.INFO, "http_404 scan=1 method=GET")]


def test_404_missing_apartment_stays_warning_next_to_js(client, mock_client, caplog):
    caplog.set_level(logging.INFO, logger="app")
    assert client.get("/apartment/no-such-apartment").status_code == 404
    assert [x.levelno for x in _app_records(caplog)] == [logging.WARNING]


# 31.10 07:40–08:40: словниковий сканер секретів, 424 рядки http_404 за годину; ці йшли
# без scan=1 (по 6 разів — сигнал сторожа), /api/config і /api/v1/version — WARNING.
# Правило загальне: файл із розширенням поза /static/ (крім трьох наших) і /api/ повз
# наші API — сканер.
SECRET_FILE_PROBES = [
    "/config/aws.json", "/terraform.tfvars", "/terraform.tfstate", "/secrets/aws.json",
    "/terraform.tfstate.backup", "/wp-config.txt", "/private_key.pem", "/settings.py",
    "/src/main/resources/application.properties", "/api/config", "/api/v1/version",
    "/apartment/no-such.json", "/en/config/app.json", "/api/нема-такого",
]


@pytest.mark.parametrize("path", SECRET_FILE_PROBES)
def test_404_file_probes_are_scan(client, mock_client, caplog, path):
    caplog.set_level(logging.INFO, logger="app")
    assert client.get(path).status_code == 404
    assert _levels(caplog) == [(logging.INFO, "http_404 scan=1 method=GET")]


@pytest.mark.parametrize("path", ["/robots.txt", "/sitemap.xml", "/favicon.ico",
                                  "/apartment/apt-uuid-a", "/api/quote/apt-uuid-a",
                                  "/api/availability/apt-uuid-a", "/api/rates/apt-uuid-a"])
def test_own_files_and_api_are_not_404(client, mock_client, caplog, path):
    """Наші адреси з розширенням і наші API живі й не пишуть http_404."""
    caplog.set_level(logging.INFO, logger="app")
    assert client.get(path).status_code < 400
    assert [x for x in _app_records(caplog) if "http_404" in x.getMessage()] == []


def test_no_route_falls_under_scan_rule(app):
    """Новий маршрут з розширенням чи під /api/ поза винятками _SCAN_404_RE сховав би
    свої мертві посилання в scan=1."""
    from app import _is_scan_404
    for rule in app.url_map.iter_rules():
        if rule.endpoint == "static":
            continue
        if app.view_functions[rule.endpoint].__module__.split(".")[0] not in ("app", "analytics"):
            continue
        sample = re.sub(r"<[^>]+>", "x", rule.rule)
        for method in sorted((rule.methods or set()) - {"OPTIONS"}):
            assert not _is_scan_404(sample, method), (method, rule.rule)
