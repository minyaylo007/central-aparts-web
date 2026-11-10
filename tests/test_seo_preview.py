"""Превʼю посилань (Open Graph / X), schema.org і robots/sitemap на канонічному хості."""
import json
import re

import pytest

from config import Config

ROOT = Config.SITE_URL.rstrip("/")


def _meta(html: str, attr: str, name: str) -> list:
    return re.findall(rf'<meta {attr}="{re.escape(name)}" content="([^"]*)"', html)


def _ld(html: str) -> list:
    return [json.loads(b) for b in
            re.findall(r'<script type="application/ld\+json">(.*?)</script>', html, re.S)]


def test_home_preview_card(client, mock_client):
    html = client.get("/").get_data(as_text=True)
    assert _meta(html, "property", "og:site_name") == ["Central Square Apartments"]
    assert _meta(html, "property", "og:locale") == ["uk_UA"]
    assert _meta(html, "property", "og:image") == [f"{ROOT}/static/img/hero.webp"]
    assert _meta(html, "name", "twitter:image") == [f"{ROOT}/static/img/hero.webp"]
    assert _meta(html, "name", "twitter:card") == ["summary_large_image"]
    assert _meta(html, "property", "og:image:width") == ["1024"]
    assert _meta(html, "property", "og:image:alt")[0]
    assert 'name="robots"' not in html


def test_apartment_preview_uses_first_photo(client, mock_client):
    mock_client.apartments[0]["photos"] = ["https://img.example.test/a1.webp", "https://img.example.test/a2.webp"]
    html = client.get("/apartment/apt-uuid-a").get_data(as_text=True)
    assert _meta(html, "property", "og:image") == ["https://img.example.test/a1.webp"]
    assert _meta(html, "name", "twitter:image") == ["https://img.example.test/a1.webp"]
    assert _meta(html, "property", "og:image:width") == []   # розмірів фото бек-офісу не знаємо
    assert _meta(html, "property", "og:image:alt") == ["Апартаменти A"]


def test_apartment_without_photos_falls_back_to_hero(client, mock_client):
    """Раніше блок og_image стояв під if і підставлявся завжди → og:image=""."""
    html = client.get("/apartment/apt-uuid-a").get_data(as_text=True)
    assert _meta(html, "property", "og:image") == [f"{ROOT}/static/img/hero.webp"]


def test_apartment_structured_data(client, mock_client):
    html = client.get("/apartment/apt-uuid-a").get_data(as_text=True)
    graph = {n["@type"]: n for n in _ld(html)[0]["@graph"]}
    assert graph["Apartment"]["containedInPlace"]["@id"] == f"{ROOT}/#lodging"
    offer = graph["Product"]["offers"]
    assert offer["price"] == 1490 and offer["priceCurrency"] == "UAH"
    assert "availability" not in offer        # наявність залежить від дат


def test_home_lodging_has_id_for_apartments(client, mock_client):
    html = client.get("/").get_data(as_text=True)
    lodging = _ld(html)[0]
    assert lodging["@type"] == "LodgingBusiness" and lodging["@id"] == f"{ROOT}/#lodging"


@pytest.mark.parametrize("base", ["https://central-aparts.example", "https://www.other.example"])
def test_sitemap_and_robots_use_canonical_host(client, mock_client, base):
    robots = client.get("/robots.txt", base_url=base).get_data(as_text=True)
    assert f"Sitemap: {ROOT}/sitemap.xml" in robots
    assert "Disallow: /api/" in robots
    sitemap = client.get("/sitemap.xml", base_url=base).get_data(as_text=True)
    locs = re.findall(r"<loc>([^<]+)</loc>", sitemap)
    assert locs == [f"{ROOT}/", f"{ROOT}/apartment/apt-uuid-a", f"{ROOT}/apartment/apt-uuid-b"]


def test_confirmed_page_not_indexed(client, mock_client):
    html = client.get("/booking-confirmed?booking_id=test1234").get_data(as_text=True)
    assert '<meta name="robots" content="noindex">' in html


def test_error_page_not_indexed(client, mock_client):
    resp = client.get("/apartment/nope")
    assert resp.status_code == 404
    assert '<meta name="robots" content="noindex">' in resp.get_data(as_text=True)
