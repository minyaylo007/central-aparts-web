"""Зовнішні script і stylesheet (31.10): точна версія в URL і integrity sha384 + crossorigin.

swiper тягнувся з jsdelivr як `swiper@11` без integrity — CDN міг віддати гостю будь-який
11.x (зокрема вразливий до GHSA-hmx5-qpq5-p643) або підмінений файл. Тепер кожен <script src>
і <link rel=stylesheet> на чужий хост — з прибитою версією і хешем; виняток — лише зі списку
нижче, з причиною. Скрипти, що вставляються з інлайн-коду (GTM, піксель), — теж зі списку:
новий динамічний завантажувач без запису тут не пройде.

Хеш рахувати по файлу з CDN і звіряти з другим джерелом (unpkg або e2e/node_modules):
    curl -sS <url> | openssl dgst -sha384 -binary | openssl base64 -A
Смока віддає CDN з e2e/node_modules — байти ті самі, тож неправильний хеш браузер у смоці заблокує.
"""
import re
from html.parser import HTMLParser
from urllib.parse import urlsplit

import pytest

# хост → причина, чому integrity неможливий
NO_SRI_HOSTS = {
    "fonts.googleapis.com": "CSS Google Fonts залежить від User-Agent (набір unicode-range і формат) — хеш не сталий",
}
# скрипти, які вставляє інлайн-код (public_base.html, __afterLoad), — не <script src>, SRI нема куди поставити
DYNAMIC_SCRIPTS = {
    "https://www.googletagmanager.com/gtm.js": "GTM: вміст контейнера міняють у кабінеті GTM, файл без версії",
    "https://connect.facebook.net/en_US/fbevents.js": "Meta Pixel: Meta оновлює файл без версії в URL",
}
# /npm/<пакет>@<X.Y.Z>/… — лише точна semver, без @11, @latest, @^1.2
EXACT_NPM = re.compile(r"^/npm/(?:@[\w.-]+/)?[\w.-]+@\d+\.\d+\.\d+/")
SRI = re.compile(r"^sha384-[A-Za-z0-9+/]{64}$")

PAGES = ["/", "/apartment/apt-uuid-a", "/apartment/no-such-apartment"]


class _ExternalRefs(HTMLParser):
    """Збирає <script src> і <link rel=stylesheet> на чужі хости, разом із <noscript>."""

    def __init__(self):
        super().__init__()
        self.refs = []          # (url, attrs)
        self.inline = []        # текст інлайн-<script>
        self._in_script = False

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "script":
            # JSON-LD і подібні — дані, не код: їхні URL нічого не вантажать
            self._in_script = "src" not in a and a.get("type") in (None, "", "text/javascript", "module")
            url = a.get("src")
        elif tag == "link" and "stylesheet" in (a.get("rel") or "").split():
            url = a.get("href")
        else:
            return
        if url and (url.startswith("//") or urlsplit(url).scheme in ("http", "https")):
            self.refs.append((url, a))

    def handle_endtag(self, tag):
        if tag == "script":
            self._in_script = False

    def handle_data(self, data):
        if self._in_script:
            self.inline.append(data)


def _violations(html: str) -> list[str]:
    p = _ExternalRefs()
    p.feed(html)
    out = []
    for url, a in p.refs:
        u = urlsplit(url)
        if u.hostname in NO_SRI_HOSTS:
            continue
        if u.hostname == "cdn.jsdelivr.net" and not EXACT_NPM.match(u.path):
            out.append(f"{url}: версія в URL не точна (X.Y.Z)")
        elif u.hostname != "cdn.jsdelivr.net":
            out.append(f"{url}: незнайомий CDN — точну версію не перевірити, додай правило або виняток")
        if not SRI.match(a.get("integrity") or ""):
            out.append(f"{url}: немає integrity sha384")
        if a.get("crossorigin") != "anonymous":
            out.append(f"{url}: немає crossorigin=\"anonymous\" (без нього integrity на чужому хості не діє)")
    for url in sorted({u.split("?", 1)[0] for u in re.findall(r"https?://[^'\"\s]+", "".join(p.inline))}):
        if url not in DYNAMIC_SCRIPTS:
            out.append(f"{url}: інлайн-скрипт вантажить невідомий URL — SRI чи виняток DYNAMIC_SCRIPTS")
    return out


def test_checker_catches_floating_version_and_missing_sri():
    """Перевірка вміє сказати «так»: на вчорашній розмітці (swiper@11 без integrity) вона кричить."""
    bad = (
        '<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/swiper@11/swiper-bundle.min.css">'
        '<script src="https://cdn.jsdelivr.net/npm/flatpickr@latest/dist/flatpickr.min.js"></script>'
        '<noscript><link rel="stylesheet" href="https://unpkg.com/x@1.0.0/x.css"></noscript>'
        "<script>var s='https://evil.example/x.js'</script>"
    )
    v = _violations(bad)
    assert sum("версія в URL не точна" in x for x in v) == 2
    assert sum("немає integrity" in x for x in v) == 3
    assert sum("немає crossorigin" in x for x in v) == 3
    assert any("незнайомий CDN" in x for x in v)
    assert any("evil.example" in x for x in v)


@pytest.mark.critical
@pytest.mark.parametrize("lang", ["ua", "en"])
@pytest.mark.parametrize("path", PAGES)
def test_external_assets_pinned_with_sri(client, mock_client, path, lang):
    if lang == "en":
        client.get("/lang/en")
    html = client.get(path).get_data(as_text=True)
    p = _ExternalRefs()
    p.feed(html)
    assert any("swiper@" in url for url, _ in p.refs), "розмітка змінилась — перевірка нічого не бачить"
    assert _violations(html) == []
