"""001-trusted-client-ip: адреса гостя, яку сайт передає бек-офісу в
X-Forwarded-For (котирування з купонним телефоном і перевірка купона), з
урахуванням TRUSTED_PROXY_COUNT. Специфікація — specs/001-trusted-client-ip/.

Лише документаційні адреси (192.0.2.0/24, 198.51.100.0/24, 203.0.113.0/24,
2001:db8::/32) і петля 127.0.0.1 / ::1; телефон синтетичний.

13.10.2026: один тест на гілку — 120 → 49. Матриця R7 і контракт §2 ідуть через
/api/coupon/check; що /api/quote/<id> з купонним телефоном бере ту саму адресу —
окремий тест, а не множник ×2 на всю матрицю. Сторож «адрес у журналі нема»
(test_us3_no_addresses_in_log) ганяє обидві ручки, як і раніше."""
import json
import logging
import re

import pytest

import config
from config import Config

# critical: адреса гостя = ліміт купонів (гроші) + жодної адреси в журналі
pytestmark = pytest.mark.critical

PHONE = "+380000000000"
# словником, щоб «+» у телефоні не став пробілом у query string
QUOTE_ARGS = {"check_in": "2030-06-01", "check_out": "2030-06-03",
              "guests": "2", "coupon_phone": PHONE}


# ---------------------------------------------------------------------------
# Помічники
# ---------------------------------------------------------------------------

@pytest.fixture
def mode(monkeypatch):
    """mode(value, invalid=False) — режим розбору так, ніби його прочитав
    config.py при старті: None = не задана, 0, N ≥ 1."""
    def _set(value, invalid=False):
        monkeypatch.setattr(Config, "TRUSTED_PROXY_COUNT", value)
        monkeypatch.setattr(Config, "TRUSTED_PROXY_COUNT_INVALID", invalid)
    return _set


def _request_kwargs(xff, remote_addr):
    kw = {"environ_base": {"REMOTE_ADDR": remote_addr}}
    if xff is not None:
        kw["headers"] = {"X-Forwarded-For": xff}
    return kw


def coupon_ip(client, mock_client, xff=None, remote_addr="127.0.0.1"):
    """POST /api/coupon/check → адреса, яку отримав підмінний бек-офіс."""
    mock_client.last_coupon_client_ip = None
    r = client.post("/api/coupon/check", json={"phone": PHONE},
                    **_request_kwargs(xff, remote_addr))
    assert r.status_code == 200
    return mock_client.last_coupon_client_ip


def quote_ip(client, mock_client, xff=None, remote_addr="127.0.0.1"):
    """GET /api/quote/<apt_id> з купонним телефоном → адреса для бек-офісу."""
    mock_client.last_quote = {}
    r = client.get("/api/quote/apt-uuid-a", query_string=QUOTE_ARGS,
                   **_request_kwargs(xff, remote_addr))
    assert r.status_code == 200
    return mock_client.last_quote["client_ip"]


ROUTES = {"coupon": coupon_ip, "quote": quote_ip}


def fallbacks(caplog):
    """Записи client_ip_fallback від логера app."""
    return [r for r in caplog.records
            if r.name == "app" and r.getMessage().startswith("client_ip_fallback")]


def fallback_reasons(caplog):
    return [re.search(r"reason=(\w+)", r.getMessage()).group(1) for r in fallbacks(caplog)]


# research.md R7 — повна матриця FR-011. Топологія → (X-Forwarded-For на вході,
# REMOTE_ADDR, {режим: (адреса для бек-офісу, код відкату або None)}).
# Режим "default" — стовпець «не задана / неверная».
TOPOLOGIES = {
    "a_cloudfront_lambda": ("198.51.100.23, 203.0.113.7", "198.51.100.23", {
        "default": ("198.51.100.23", None), 0: ("198.51.100.23", None),
        1: ("203.0.113.7", None), 2: ("198.51.100.23", None)}),
    "b_caddy_gunicorn": ("203.0.113.7", "127.0.0.1", {
        "default": ("203.0.113.7", None), 0: ("127.0.0.1", None),
        1: ("203.0.113.7", None), 2: ("127.0.0.1", "fewer_than_n")}),
    "c_caddy_nginx_gunicorn": ("203.0.113.7", "127.0.0.1", {
        "default": ("203.0.113.7", None), 0: ("127.0.0.1", None),
        1: ("203.0.113.7", None), 2: ("127.0.0.1", "fewer_than_n")}),
    "d_direct_forged": ("198.51.100.23", "192.0.2.50", {
        "default": ("198.51.100.23", None), 0: ("192.0.2.50", None),
        1: ("198.51.100.23", None), 2: ("192.0.2.50", "fewer_than_n")}),
    "d_direct_no_header": (None, "192.0.2.50", {
        "default": ("192.0.2.50", None), 0: ("192.0.2.50", None),
        1: ("192.0.2.50", "no_header"), 2: ("192.0.2.50", "no_header")}),
}

# contracts/trusted-proxy-count.md §2, рядки 4–13:
# (№, N, X-Forwarded-For, REMOTE_ADDR, результат, WARNING або None)
CONTRACT_N = [
    (4, 0, "198.51.100.23", "192.0.2.50", "192.0.2.50", None),
    (5, 1, "198.51.100.23, 203.0.113.7", "198.51.100.23", "203.0.113.7", None),
    (6, 1, "203.0.113.7", "127.0.0.1", "203.0.113.7", None),
    (7, 1, "2001:db8::1", "::1", "2001:db8::1", None),
    (8, 1, "198.51.100.23 , , 203.0.113.7 ", "127.0.0.1", "203.0.113.7", None),
    (9, 2, "198.51.100.23, 203.0.113.7, 192.0.2.10", "127.0.0.1", "203.0.113.7", None),
    (10, 1, None, "192.0.2.50", "192.0.2.50", "client_ip_fallback reason=no_header elements=0"),
    (11, 2, "203.0.113.7", "127.0.0.1", "127.0.0.1", "client_ip_fallback reason=fewer_than_n elements=1"),
    (12, 1, "unknown", "127.0.0.1", "127.0.0.1", "client_ip_fallback reason=not_ip elements=1"),
    (13, 1, "203.0.113.7:443", "127.0.0.1", "127.0.0.1", "client_ip_fallback reason=not_ip elements=1"),
]


# ---------------------------------------------------------------------------
# Налаштування TRUSTED_PROXY_COUNT (config.py)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw, expected", [
    ("  ", (None, False)),      # не задано
    ("0", (0, False)),          # нуль — окремий режим
    (" 2 ", (2, False)),        # число з пробілами
    ("-1", (None, True)),       # знак — неверно
    ("١", (None, True)),        # арабсько-індійська «1» — не ASCII-цифра
])
def test_config_parse_trusted_proxy_count(raw, expected):
    assert config._parse_trusted_proxy_count(raw) == expected


def test_config_fields_exist():
    assert hasattr(Config, "TRUSTED_PROXY_COUNT")
    assert hasattr(Config, "TRUSTED_PROXY_COUNT_INVALID")


# ---------------------------------------------------------------------------
# US1 — підробка заголовка не обнуляє ліміт купонів
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("n", [0, 1, 2])
@pytest.mark.parametrize("topology", TOPOLOGIES)
def test_us1_topology_matrix(client, mock_client, mode, caplog, topology, n):
    xff, remote_addr, expected = TOPOLOGIES[topology]
    ip, reason = expected[n]
    mode(n)
    caplog.set_level(logging.INFO, logger="app")
    assert coupon_ip(client, mock_client, xff, remote_addr) == ip
    assert fallback_reasons(caplog) == ([reason] if reason else [])


def test_us1_quote_route_takes_same_address(client, mock_client, mode, caplog):
    """Котирування з купонним телефоном іде через той самий _visitor_ip(): за
    проксі — адреса гостя, при N=0 — адреса з'єднання, відкат — із WARNING."""
    caplog.set_level(logging.INFO, logger="app")
    mode(1)
    assert quote_ip(client, mock_client, "203.0.113.7", "127.0.0.1") == "203.0.113.7"
    mode(0)
    assert quote_ip(client, mock_client, "198.51.100.23", "192.0.2.50") == "192.0.2.50"
    mode(2)
    assert quote_ip(client, mock_client, "203.0.113.7", "127.0.0.1") == "127.0.0.1"
    assert fallback_reasons(caplog) == ["fewer_than_n"]


def _lambda_event():
    """Синтетична подія Lambda Function URL — топологія (а), без AWS і мережі."""
    return {
        "requestContext": {"http": {"method": "POST", "sourceIp": "203.0.113.7"}},
        "rawPath": "/api/coupon/check",
        "rawQueryString": "",
        "headers": {"x-forwarded-for": "198.51.100.23, 203.0.113.7",
                    "content-type": "application/json",
                    "host": "www.example.test"},
        "body": json.dumps({"phone": PHONE}),
    }


@pytest.mark.parametrize("n, expected", [
    (1, "203.0.113.7"),
    (None, "198.51.100.23"),   # режим за замовчуванням — як сьогодні на Lambda
])
def test_us1_lambda_adapter(mock_client, mode, n, expected):
    import wsgi_adapter
    mode(n)
    resp = wsgi_adapter.lambda_handler(_lambda_event(), None)
    assert resp["statusCode"] == 200
    assert mock_client.last_coupon_client_ip == expected


def test_us1_forged_left_same_bucket(client, mock_client, mode):
    """SC-001: сім перевірок з різними підробленими лівими елементами —
    бек-офіс сім разів отримує ту саму адресу, поставлену проксі."""
    mode(1)
    got = [coupon_ip(client, mock_client, f"198.51.100.{i}, 203.0.113.7")
           for i in range(1, 8)]
    assert got == ["203.0.113.7"] * 7


def test_us1_two_guests_distinct(client, mock_client, mode):
    """SC-005: два гості за тією самою цепочкою — два різні відра."""
    mode(1)
    first = coupon_ip(client, mock_client, "203.0.113.7", "127.0.0.1")
    second = coupon_ip(client, mock_client, "203.0.113.8", "127.0.0.1")
    assert (first, second) == ("203.0.113.7", "203.0.113.8")


@pytest.mark.parametrize("row", CONTRACT_N, ids=lambda r: f"row{r[0]}")
def test_us1_contract_cases(client, mock_client, mode, caplog, row):
    """contracts/trusted-proxy-count.md §2, рядки 4–13: адреса для бек-офісу і
    рівно один WARNING на відкат — один рядок контракту перевіряється одним
    тестом (до 22.09 ті самі рядки проганялись двічі, окремо адреса й журнал)."""
    _, n, xff, remote_addr, expected, warning = row
    mode(n)
    caplog.set_level(logging.INFO, logger="app")
    assert coupon_ip(client, mock_client, xff, remote_addr) == expected
    app_warnings = [r for r in caplog.records
                    if r.name == "app" and r.levelno >= logging.WARNING]
    if warning is None:
        assert fallbacks(caplog) == []
        assert app_warnings == []
    else:
        assert len(app_warnings) == 1
        rec = app_warnings[0]
        assert rec.levelno == logging.WARNING
        assert rec.getMessage() == warning
        assert rec.exc_info is None


@pytest.mark.parametrize("xff, remote_addr, level", [
    (None, "127.0.0.1", logging.INFO),        # наша перевірка curl повз Caddy
    (None, "::1", logging.INFO),
    (None, "192.0.2.50", logging.WARNING),    # хтось дістався gunicorn повз проксі
    ("unknown", "127.0.0.1", logging.WARNING),  # заголовок є, але зіпсований — від Caddy
], ids=["loop4", "loop6", "not_loop", "loop_bad_header"])
def test_fallback_level_loopback_without_header_is_info(client, mock_client, mode, caplog,
                                                       xff, remote_addr, level):
    """23.10.2026: client_ip_fallback reason=no_header з петлі — INFO (4 WARNING 21.10 —
    наші curl на 127.0.0.1:8004); решта — WARNING."""
    mode(1)
    caplog.set_level(logging.INFO, logger="app")
    assert coupon_ip(client, mock_client, xff, remote_addr) == remote_addr
    assert [r.levelno for r in fallbacks(caplog)] == [level]


# ---------------------------------------------------------------------------
# US2 — без налаштування нічого не змінюється
# ---------------------------------------------------------------------------

# Три способи «не задати»: поле None, неверне значення, порожній рядок в оточенні
DEFAULT_MODES = {
    "unset": (None, False),
    "invalid": (None, True),
    "empty_env": config._parse_trusted_proxy_count(""),
}

# contracts/trusted-proxy-count.md §2, рядки 1–3 і 14:
# (№, режим, X-Forwarded-For, REMOTE_ADDR, результат)
CONTRACT_DEFAULT = [
    (1, "unset", "203.0.113.7, 192.0.2.10", "127.0.0.1", "203.0.113.7"),
    (2, "unset", None, "192.0.2.50", "192.0.2.50"),
    (3, "unset", " , 203.0.113.7", "192.0.2.50", "192.0.2.50"),
    (14, "invalid", "203.0.113.7, 192.0.2.10", "127.0.0.1", "203.0.113.7"),
]


def _pre_001_visitor_ip(xff, remote_addr):
    """Вираз _visitor_ip() до фічі 001 — еталон для SC-002."""
    return (xff or "").split(",")[0].strip() or remote_addr or ""


def test_us2_default_mode(client, mock_client, mode, caplog):
    """Три способи «не задати» × п'ять топологій R7 — як до фічі 001, без відкатів."""
    caplog.set_level(logging.INFO, logger="app")
    for default_mode, value in DEFAULT_MODES.items():
        for topology, (xff, remote_addr, expected) in TOPOLOGIES.items():
            ip, _ = expected["default"]
            mode(*value)
            got = coupon_ip(client, mock_client, xff, remote_addr)
            assert got == ip == _pre_001_visitor_ip(xff, remote_addr), (default_mode, topology)
    assert fallbacks(caplog) == []


@pytest.mark.parametrize("row", CONTRACT_DEFAULT, ids=lambda r: f"row{r[0]}")
def test_us2_default_contract_cases(client, mock_client, mode, caplog, row):
    _, default_mode, xff, remote_addr, expected = row
    mode(*DEFAULT_MODES[default_mode])
    caplog.set_level(logging.INFO, logger="app")
    got = coupon_ip(client, mock_client, xff, remote_addr)
    assert got == expected == _pre_001_visitor_ip(xff, remote_addr)
    assert fallbacks(caplog) == []


ALL_MODES = {"unset": (None, False), "0": (0, False), "1": (1, False),
             "2": (2, False), "invalid": (None, True)}
FORGED_FORWARDED = {"X-Forwarded-Proto": "http",
                    "X-Forwarded-Host": "198.51.100.23",
                    "X-Forwarded-For": "198.51.100.23"}


def _seo_bodies(client, headers=None):
    return tuple(client.get(path, base_url="https://www.example.test",
                            headers=headers or {}).data
                 for path in ("/robots.txt", "/sitemap.xml"))


def test_us2_no_proxyfix_remote_addr_untouched(app, mode):
    """FR-013: без ProxyFix-обгортки, request.remote_addr не переписується."""
    from flask import request
    from werkzeug.middleware.proxy_fix import ProxyFix
    import app as app_module

    assert not isinstance(app.wsgi_app, ProxyFix)
    mode(1)
    with app.test_request_context(environ_base={"REMOTE_ADDR": "127.0.0.1"},
                                  headers={"X-Forwarded-For": "203.0.113.7"}):
        assert app_module._visitor_ip() == "203.0.113.7"
        assert request.remote_addr == "127.0.0.1"


# ---------------------------------------------------------------------------
# US3 — режим видно в журналі, адрес у журналі нема
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("n, invalid, expected", [
    (None, False, ["INFO client_ip_mode mode=default"]),
    (0, False, ["INFO client_ip_mode mode=ignore_header"]),
    (1, False, ["INFO client_ip_mode mode=trusted_proxies n=1"]),
    (None, True, ["ERROR client_ip_config_invalid env=TRUSTED_PROXY_COUNT fallback=default",
                  "INFO client_ip_mode mode=default"]),
], ids=["default", "ignore_header", "trusted_1", "invalid"])
def test_us3_startup_mode_line(mode, caplog, monkeypatch, n, invalid, expected):
    import app as app_module
    # сире значення поруч в оточенні — у журнал воно потрапити не має
    monkeypatch.setenv("TRUSTED_PROXY_COUNT", "abc" if invalid else str(n or ""))
    mode(n, invalid)
    caplog.set_level(logging.INFO, logger="app")
    app_module._log_client_ip_mode()
    records = [r for r in caplog.records if r.name == "app"]
    assert [f"{r.levelname} {r.getMessage()}" for r in records] == expected
    assert all(r.exc_info is None for r in records)
    assert "abc" not in caplog.text


_IP_RE = re.compile(r"\d+\.\d+\.\d+\.\d+|2001:db8::\w*|::1")


def _used_addresses():
    """Усі тестові адреси з матриці й контракту — і гостей, і з'єднань."""
    rows = [(xff, ra, ip) for xff, ra, exp in TOPOLOGIES.values()
            for ip, _ in exp.values()]
    rows += [(xff, ra, ip) for _, _, xff, ra, ip, _ in CONTRACT_N]
    rows += [(xff, ra, ip) for _, _, xff, ra, ip in CONTRACT_DEFAULT]
    found = set()
    for row in rows:
        for part in row:
            found.update(_IP_RE.findall(part or ""))
    return found


def test_us3_no_addresses_in_log(client, mock_client, mode, caplog):
    """SC-003 / FR-009: після старту в усіх режимах і всіх запитів матриці R7 та
    контракту §2 (разом з усіма відкатами) — жодної тестової адреси в журналі,
    жодного трейсбека, рівно одна client_ip_fallback на кожен відкат."""
    import app as app_module
    caplog.set_level(logging.DEBUG)

    for n, invalid in ALL_MODES.values():
        mode(n, invalid)
        app_module._log_client_ip_mode()

    expected_fallbacks = 0
    for xff, remote_addr, expected in TOPOLOGIES.values():
        for key, (_, reason) in expected.items():
            mode(None if key == "default" else key)
            for route in ROUTES.values():
                route(client, mock_client, xff, remote_addr)
                expected_fallbacks += reason is not None
    for _, n, xff, remote_addr, _, warning in CONTRACT_N:
        mode(n)
        for route in ROUTES.values():
            route(client, mock_client, xff, remote_addr)
            expected_fallbacks += warning is not None
    for _, default_mode, xff, remote_addr, _ in CONTRACT_DEFAULT:
        mode(*DEFAULT_MODES[default_mode])
        for route in ROUTES.values():
            route(client, mock_client, xff, remote_addr)

    addresses = _used_addresses()
    assert {"203.0.113.7", "198.51.100.23", "192.0.2.50", "127.0.0.1",
            "2001:db8::1", "::1"} <= addresses
    text = caplog.text
    leaked = sorted(a for a in addresses if a in text)
    assert leaked == []
    assert "Traceback" not in text
    assert expected_fallbacks > 0
    assert len(fallbacks(caplog)) == expected_fallbacks


# ---------------------------------------------------------------------------
# Convergence (Phase 7)
# ---------------------------------------------------------------------------

def _lambda_seo_bodies(xff):
    """/robots.txt і /sitemap.xml через справжній wsgi_adapter — топологія (а)."""
    import wsgi_adapter
    bodies = []
    for path in ("/robots.txt", "/sitemap.xml"):
        headers = {"host": "www.example.test"}
        if xff is not None:
            headers["x-forwarded-for"] = xff
        resp = wsgi_adapter.lambda_handler({
            "requestContext": {"http": {"method": "GET", "sourceIp": "203.0.113.7"}},
            "rawPath": path, "rawQueryString": "", "headers": headers,
        }, None)
        assert resp["statusCode"] == 200
        bodies.append(resp["body"])
    return tuple(bodies)


def test_us2_request_untouched_all_topologies(client, mock_client, mode):
    """FR-007 і SC-004: посилання в /robots.txt і /sitemap.xml ті самі в усіх
    топологіях матриці R7, при всіх значеннях налаштування і з підробленими
    X-Forwarded-Proto / -Host / -For; (а) — ще й через Lambda."""
    mode(None)
    baseline = _seo_bodies(client)
    assert b"Sitemap: https://www.example.test/sitemap.xml" in baseline[0]
    assert b"<loc>https://www.example.test/</loc>" in baseline[1]
    lambda_baseline = _lambda_seo_bodies(None)
    assert lambda_baseline == tuple(b.decode("utf-8") for b in baseline)
    lambda_xff = TOPOLOGIES["a_cloudfront_lambda"][0]
    for name, (n, invalid) in ALL_MODES.items():
        mode(n, invalid)
        assert _seo_bodies(client, FORGED_FORWARDED) == baseline, name
        for topology, (xff, remote_addr, _) in TOPOLOGIES.items():
            got = tuple(client.get(path, base_url="https://www.example.test",
                                   **_request_kwargs(xff, remote_addr)).data
                        for path in ("/robots.txt", "/sitemap.xml"))
            assert got == baseline, (name, topology)
        assert _lambda_seo_bodies(lambda_xff) == lambda_baseline, name


def test_us1_empty_connection_address(client, mock_client, mode, caplog):
    """Edge case: адреси з'єднання теж нема → бек-офісу йде порожній рядок
    (у всіх режимах; відкат — зі своїм кодом)."""
    caplog.set_level(logging.INFO, logger="app")
    expected_reasons = []
    for n, xff, reason in [(None, None, None), (0, "198.51.100.23", None),
                           (1, None, "no_header"), (2, "203.0.113.7", "fewer_than_n")]:
        mode(n)
        assert coupon_ip(client, mock_client, xff, "") == "", n
        if reason:
            expected_reasons.append(reason)
    assert fallback_reasons(caplog) == expected_reasons
