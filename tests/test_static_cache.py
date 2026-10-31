"""
Заголовок кешування на /static/* (`static_cache_headers` в app.py).

Кешується лише те, що можна кешувати: 200 (віддача файлу), 304 (успішна ревалідація —
заголовок оновлює вже збережену копію) і 206 (частина за Range). Будь-яка помилка на
/static/* — `no-store`: 404 на одруківку в шляху не повинен пережити саму одруківку.

22.09 додано відбиток версії в адресі (`?v=<git_short>`, ставить `static_url()`): збіг із
поточною версією — рік і `immutable`, решта — прежні 300 секунд.
13.10.2026: один тест на гілку — 14 → 7.
"""
import app as app_module

CACHEABLE = "public, max-age=300, s-maxage=86400"
IMMUTABLE = "public, max-age=31536000, immutable"
VERSION = "abc1234"


def _stamp(monkeypatch, version):
    """Версія викату, відома процесу ({} — Lambda або копія без .git і без
    BUILD_INFO.json). Кеш build_info() підміняємо цілим — саме його читає
    `_asset_version()`, і саме він на коробці заповнений із BUILD_INFO.json."""
    monkeypatch.setattr(app_module, "_BUILD_INFO", {"git_short": version} if version else {})


def test_static_success_codes_are_cacheable(client):
    """200, 304 і 206 — з кешуючим заголовком. Ревалідація вдалася: гостю кажуть
    «твоя копія свіжа»; no-store тут наказав би її викинути."""
    first = client.get("/static/styles.css")
    assert first.status_code == 200
    assert first.headers["Cache-Control"] == CACHEABLE
    again = client.get("/static/styles.css",
                       headers={"If-Modified-Since": first.headers["Last-Modified"]})
    assert again.status_code == 304
    assert again.headers["Cache-Control"] == CACHEABLE
    part = client.get("/static/styles.css", headers={"Range": "bytes=0-9"})
    assert part.status_code == 206
    assert part.headers["Cache-Control"] == CACHEABLE


def test_static_errors_are_not_cached(client):
    """Одруківка в шляху (404) і не той метод (405). Раніше 404 віддавався з тим
    самим заголовком, що й файл."""
    r = client.get("/static/style.css")  # правильно — styles.css
    assert r.status_code == 404
    assert r.headers["Cache-Control"] == "no-store"
    r = client.post("/static/styles.css")
    assert r.status_code == 405
    assert r.headers["Cache-Control"] == "no-store"


def test_non_static_404_is_left_alone(client):
    """Обробник чіпає лише /static/*: на решті шляхів заголовок не з'являється
    і чужого не перебиває."""
    r = client.get("/no-such-page")
    assert r.status_code == 404
    assert "Cache-Control" not in r.headers


# --- відбиток версії в адресі (22.09) --------------------------------------

def test_matching_version_gets_a_year(client, monkeypatch):
    """Адрес із відбитком цього викату змінюється разом із файлом — його можна
    віддати назавжди."""
    _stamp(monkeypatch, VERSION)
    r = client.get(f"/static/styles.css?v={VERSION}")
    assert r.status_code == 200
    assert r.headers["Cache-Control"] == IMMUTABLE


def test_any_other_case_keeps_five_minutes(client, monkeypatch):
    """Відбиток від попереднього викату (файл за адресом уже новий), голий
    /static/styles.css (закладки, чужі посилання) і процес без версії (жоден
    `?v=` не вважається збігом, навіть порожній) — усе лишається на 300 с:
    вічний кеш тут застряг би в гостя назавжди."""
    _stamp(monkeypatch, VERSION)
    for url in ("/static/styles.css?v=0000000", "/static/styles.css"):
        r = client.get(url)
        assert r.status_code == 200
        assert r.headers["Cache-Control"] == CACHEABLE, url
    _stamp(monkeypatch, None)
    for url in ("/static/styles.css?v=", f"/static/styles.css?v={VERSION}"):
        r = client.get(url)
        assert r.status_code == 200
        assert r.headers["Cache-Control"] == CACHEABLE, url


def test_error_with_matching_version_is_still_not_cached(client, monkeypatch):
    """Відбиток не робить помилку кешованою: 404 на одруківку лишається no-store."""
    _stamp(monkeypatch, VERSION)
    r = client.get(f"/static/style.css?v={VERSION}")
    assert r.status_code == 404
    assert r.headers["Cache-Control"] == "no-store"


def test_page_links_static_with_version_only_when_stamped(client, monkeypatch):
    """Шаблон підключає стилі й скрипт через static_url() — інакше довгий кеш
    ставився б на адреси, яких ніхто не просить. Без версії адреса така сама,
    як до 22.09."""
    _stamp(monkeypatch, VERSION)
    html = client.get("/").get_data(as_text=True)
    assert f"/static/styles.css?v={VERSION}" in html
    assert f"/static/main.js?v={VERSION}" in html
    _stamp(monkeypatch, None)
    html = client.get("/").get_data(as_text=True)
    assert '"/static/styles.css"' in html
    assert "?v=" not in html
