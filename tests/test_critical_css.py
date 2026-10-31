"""CSS першого екрана (22.10): інлайн static/critical/<група>.css + неблокуючі повні аркуші.

Стереже дві речі: (1) critical-файли зібрані з ТИХ САМИХ styles.css і bootstrap-lite.css, що
лежать у репозиторії (інакше перший кадр малюється старими правилами, а потім стрибає — CLS);
(2) <head> не повернувся до блокуючих аркушів із CDN, через які LCP на телефоні був 6,3 с.
Застарів файл → scripts/build_css.sh (у рамці прогону з CLAUDE.md).
"""
import hashlib
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "static"
GROUPS = ("home", "apartment", "other")

pytestmark = pytest.mark.real_critical_css


def _sources_hash() -> str:
    names = ("vendor/bootstrap-lite.css", "vendor/flatpickr-4.6.13.min.css", "styles.css")
    texts = [(STATIC / n).read_text(encoding="utf-8") for n in names]
    return hashlib.sha256("\0".join(texts).encode("utf-8")).hexdigest()[:16]


@pytest.mark.parametrize("group", GROUPS)
def test_critical_css_built_from_current_sources(group):
    head = (STATIC / "critical" / f"{group}.css").read_text(encoding="utf-8").split("\n", 1)[0]
    m = re.search(r"sources ([0-9a-f]{16})", head)
    assert m, f"critical/{group}.css без відбитка джерел у шапці"
    assert m.group(1) == _sources_hash(), (
        f"critical/{group}.css застарів: styles.css, bootstrap-lite.css або flatpickr змінились — "
        "запусти scripts/build_css.sh")


def _head(html: str) -> str:
    return html.split("</head>", 1)[0]


@pytest.mark.critical
@pytest.mark.parametrize("path,group", [
    ("/", "home"),
    ("/apartment/apt-uuid-a", "apartment"),
    ("/apartment/no-such-apartment", "other"),
])
def test_head_inlines_group_css_first_and_blocks_nothing(client, mock_client, path, group):
    html = client.get(path).get_data(as_text=True)
    head = _head(html)
    css = (STATIC / "critical" / f"{group}.css").read_text(encoding="utf-8")
    first_css = re.search(r"<style>|<link[^>]+rel=\"(?:stylesheet|preload)\"[^>]+as=\"style\"|<link[^>]+stylesheet", head)
    assert first_css and first_css.group(0) == "<style>", "інлайн-<style> мусить бути першим аркушем (Flatpickr)"
    style = head[first_css.end():head.index("</style>", first_css.end())]
    assert style.startswith(f"/* critical/{group}:"), f"{path}: не та група critical CSS"
    assert len(style) == len(css.replace("</", "<\\/"))
    assert "&gt;" not in style                       # CSS не екранований автоекрануванням

    # блокуючих аркушів не лишилось: усе, що rel=stylesheet поза <noscript>, — лише з media=print
    no_noscript = re.sub(r"<noscript>.*?</noscript>", "", head, flags=re.S)
    for link in re.findall(r"<link[^>]+rel=\"stylesheet\"[^>]*>", no_noscript):
        assert 'media="print"' in link, f"блокуючий аркуш у <head>: {link}"
    assert "bootstrap.min.css" not in head and "bootstrap.bundle" not in html
    assert "cdn.jsdelivr.net/npm/flatpickr@4.6.13/dist/flatpickr.min.css" not in head
    # повні аркуші вмикаються разом (__fullCssOn рахує їх): поодинці вони давали CLS
    full = re.findall(r"<link[^>]+data-full-css[^>]*>", no_noscript)
    assert re.search(rf"var __fullCssLeft = {len(full)};", head), "лічильник не дорівнює кількості аркушів"
    for name in ("vendor/bootstrap-lite.css", "vendor/flatpickr-4.6.13.min.css", "styles.css"):
        assert any(re.search(rf'href="/static/{re.escape(name)}[^"]*" media="print" data-full-css '
                             r'onload="__fullCssOn\(\)" onerror="__fullCssOn\(\)"', link) for link in full), name
        assert f"/static/{name}" in head.split("<noscript>")[-1], f"{name}: нема запасного <noscript>"
