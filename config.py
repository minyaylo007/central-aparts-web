"""
Configuration for the v2 public booking site.
All values come from environment variables.
"""
import os
import re


def _parse_setting(raw: str | None, *,
                   fraction: bool = False) -> tuple[int | float | None, bool]:
    """Числове налаштування → (значення, неверне?). Не задано/порожньо → (None, False),
    лише ASCII-цифри → (int, False), при fraction ще «цифри.цифри» → (float, False),
    усе інше (-1, +1, 1.5 для цілих, 1e3, abc) → (None, True): сайт стартує в режимі
    за замовчуванням, а app.py пише ERROR без значення."""
    value = (raw or "").strip()
    if not value:
        return None, False
    if re.fullmatch(r"[0-9]+", value):
        return int(value), False
    if fraction and re.fullmatch(r"[0-9]+\.[0-9]+", value):
        return float(value), False
    return None, True


def _parse_trusted_proxy_count(raw: str | None) -> tuple[int | None, bool]:
    """TRUSTED_PROXY_COUNT → (N, неверне?) — ціле за _parse_setting."""
    return _parse_setting(raw)


def _setting(name: str, default, *, fraction: bool = False) -> tuple:
    """Налаштування з оточення: (значення або default, неверне?)."""
    value, invalid = _parse_setting(os.environ.get(name), fraction=fraction)
    return (default if value is None else value), invalid


# Ключ сесій для локального запуску й тестів. Код публічний — цим ключем підпис сесій
# підробить будь-хто, тож на проді з ним сайт не стартує (secret_key_problem, wsgi.py).
DEV_SECRET_KEY = "dev-secret-change-in-prod"


def secret_key_problem(env: str, key: str | None, server_software: str | None) -> str | None:
    """Чому з цим SECRET_KEY старт на проді заборонено: "unset" | "dev_default" | None.

    Прод = ENV "prod" (його значення за замовчуванням — у site.env ENV не задають)
    і gunicorn (арбітр ставить SERVER_SOFTWARE="gunicorn/<версія>" воркерам). Тести,
    `flask run` і Lambda (wsgi_adapter.py, ключ — параметром SAM) сюди не потрапляють,
    смока — ENV=smoke. Значення ключа не повертається й не логується."""
    if env != "prod" or not (server_software or "").startswith("gunicorn/"):
        return None
    if not (key or "").strip():
        return "unset"
    if key == DEV_SECRET_KEY:
        return "dev_default"
    return None


class Config:
    # Flask
    SECRET_KEY: str = os.environ.get("SECRET_KEY", DEV_SECRET_KEY)
    DEBUG: bool = os.environ.get("FLASK_DEBUG", "0") == "1"

    # Яке оточення відповідає — віддається у /health (як у бек-офісі: Config.ENV)
    ENV: str = os.environ.get("ENV", "prod")

    # Backoffice API
    BACKOFFICE_URL: str = os.environ.get("BACKOFFICE_URL", "https://cv.central-aparts.store")
    PUBLIC_API_KEY: str = os.environ.get("PUBLIC_API_KEY", "")

    # Analytics
    GTM_ID: str = "GTM-N67WWKWW"
    FB_PIXEL_ID: str = "2032782477471246"

    # Своя статистика гостей (пакет analytics/): шлях до SQLite поза репо. Не задано —
    # збір вимкнено повністю (Lambda, тести). На машині — тека поруч із чекаутом,
    # <чекаут>-data/analytics.db. STATS_KEY — ключ панелі /stats.
    ANALYTICS_DB: str = os.environ.get("ANALYTICS_DB", "").strip()
    STATS_KEY: str = os.environ.get("STATS_KEY", "").strip()

    # Site
    SITE_URL: str = os.environ.get("SITE_URL", "https://www.central-aparts.store")

    # Owner-only 1-UAH TEST apartments, shown on /?test=1 for payment tests.
    # The backoffice hides them from its public list, so the site fetches them
    # by direct id. Comma-separated env override if the test apt is recreated.
    TEST_APARTMENT_IDS: tuple = tuple(filter(None, os.environ.get(
        "TEST_APARTMENT_IDS", "94577289-2fcb-42fc-a113-832794b65309").split(",")))

    # Адреса гостя для бек-офісу (_visitor_ip в app.py): скільки довірених проксі
    # стоїть перед сайтом. Не задано — як до фічі (перший елемент X-Forwarded-For);
    # 0 — заголовок ігнорується; N ≥ 1 — N-й елемент справа. Див. deploy/README.md.
    TRUSTED_PROXY_COUNT: int | None
    TRUSTED_PROXY_COUNT_INVALID: bool
    TRUSTED_PROXY_COUNT, TRUSTED_PROXY_COUNT_INVALID = _parse_trusted_proxy_count(
        os.environ.get("TRUSTED_PROXY_COUNT"))

    # Бережні звернення до бек-офісу (спека 002). Усі три за замовчуванням вимкнені —
    # поведінка як до фічі; неверне значення — теж вимкнено (SLOT_WAIT — 2) + ERROR
    # без значення в app.py. Що ставити й чому — deploy/README.md.
    #   QUOTE_CACHE_TTL            — кеш котирування без купона, с (0 = вимкнено);
    #   BACKOFFICE_MAX_CONCURRENT  — стеля одночасних звернень процесу (0 = без стелі);
    #   BACKOFFICE_SLOT_WAIT       — скільки чекати місця під стелею, с.
    QUOTE_CACHE_TTL: int
    QUOTE_CACHE_TTL_INVALID: bool
    QUOTE_CACHE_TTL, QUOTE_CACHE_TTL_INVALID = _setting("QUOTE_CACHE_TTL", 0)
    BACKOFFICE_MAX_CONCURRENT: int
    BACKOFFICE_MAX_CONCURRENT_INVALID: bool
    BACKOFFICE_MAX_CONCURRENT, BACKOFFICE_MAX_CONCURRENT_INVALID = _setting(
        "BACKOFFICE_MAX_CONCURRENT", 0)
    BACKOFFICE_SLOT_WAIT: float
    BACKOFFICE_SLOT_WAIT_INVALID: bool
    BACKOFFICE_SLOT_WAIT, BACKOFFICE_SLOT_WAIT_INVALID = _setting(
        "BACKOFFICE_SLOT_WAIT", 2.0, fraction=True)
    BACKOFFICE_SLOT_WAIT = float(BACKOFFICE_SLOT_WAIT)
    # межа записів кешу котирування на процес — константа, не оточення
    QUOTE_CACHE_MAX_ENTRIES: int = 2000
