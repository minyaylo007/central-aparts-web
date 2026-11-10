"""Фід каталогу Meta /feeds/meta-catalog.csv: квартири з бек-офісу, лише читання."""
import csv
import io

import pytest

from config import Config

ROOT = Config.SITE_URL.rstrip("/")


def _rows(resp) -> list:
    return list(csv.DictReader(io.StringIO(resp.get_data(as_text=True))))


@pytest.fixture
def photos(mock_client):
    mock_client.apartments[0]["photos"] = ["https://img.example.test/a1.webp", "/static/img/a2.webp"]
    mock_client.apartments[1]["photos"] = ["https://img.example.test/b1.webp"]
    return mock_client


@pytest.mark.critical
def test_feed_lists_public_apartments_with_catalog_fields(client, photos):
    resp = client.get("/feeds/meta-catalog.csv")
    assert resp.status_code == 200 and resp.mimetype == "text/csv"
    rows = _rows(resp)
    assert [r["id"] for r in rows] == ["apt-uuid-a", "apt-uuid-b"]   # id = content_ids пікселя
    a = rows[0]
    assert a["price"] == "1490 UAH"
    assert a["availability"] == "in stock" and a["condition"] == "new"
    assert a["link"] == f"{ROOT}/apartment/apt-uuid-a"
    assert a["image_link"] == "https://img.example.test/a1.webp"
    assert a["additional_image_link"] == f"{ROOT}/static/img/a2.webp"   # відносний шлях → абсолютний
    assert a["title"].startswith("Апартаменти A") and a["description"] == "Тест опис"
    assert rows[1]["description"]                          # порожній опис → типовий текст


def test_feed_skips_apartment_without_photo_and_test_apartment(client, photos):
    photos.apartments[1]["photos"] = []
    photos.apartments.append({"id": "apt-test", "code": "T", "name": "ТЕСТ", "price": 1,
                              "photos": ["https://img.example.test/t.webp"]})
    assert [r["id"] for r in _rows(client.get("/feeds/meta-catalog.csv"))] == ["apt-uuid-a"]


@pytest.mark.critical
def test_feed_backoffice_failure_is_503_not_empty_feed(client, photos, monkeypatch):
    """Порожній фід Meta сприйняла б як «усе видалено» й зняла б рекламу."""
    def boom(lang="ua"):
        raise RuntimeError("down")
    monkeypatch.setattr(photos, "list_apartments", boom)
    resp = client.get("/feeds/meta-catalog.csv")
    assert resp.status_code == 503
    assert resp.headers["Retry-After"] == "300"
    assert "id,title" not in resp.get_data(as_text=True)


def test_feed_lang_en_and_unknown_lang_falls_back(client, photos):
    seen = []
    real = photos.list_apartments

    def spy(lang="ua"):
        seen.append(lang)
        return real(lang=lang)
    photos.list_apartments = spy
    client.get("/feeds/meta-catalog.csv?lang=en")
    client.get("/feeds/meta-catalog.csv?lang=xx")
    assert seen == ["en", "ua"]
