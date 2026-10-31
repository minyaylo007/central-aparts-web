"""
Smoke tests for the public booking site (власний FakeClient, без conftest).
13.10.2026: лишились гілки, яких нема в test_routes.py — колаж, режим ?test=1,
API доступності, номер і кнопка оплати на сторінці підтвердження.
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

import api_client
from app import app

# critical: головна, квартира, доступність, бронь і підтвердження
pytestmark = pytest.mark.critical


APT = {
    "id": "apt-1",
    "code": "A1",
    "name": "Park View Suite",
    "price": 1450,
    "photos": [f"https://cdn.example.com/p{i}.webp" for i in range(1, 8)],
    "amenities": ["WiFi", "Smart TV", "Кондиціонер", "Кухня", "Пральна машина"],
    "description": "Затишні апартаменти.",
}
APT2 = {**APT, "id": "apt-2", "code": "A2", "name": "Cozy Loft", "photos": APT["photos"][:3]}
TEST_APT = {**APT, "id": "apt-test", "name": "TEST", "price": 1}


class FakeClient:
    def __init__(self, fail_availability=False):
        self.fail_availability = fail_availability

    def list_apartments(self, lang="ua"):
        return [APT, APT2, TEST_APT]

    def get_apartment(self, apt_id, lang="ua"):
        return {a["id"]: a for a in (APT, APT2)}.get(apt_id)

    def get_availability(self, apt_id):
        if self.fail_availability:
            raise RuntimeError("backoffice down")
        return [{"from": "2026-07-01", "to": "2026-07-05"}]

    def get_booking(self, booking_id):
        return {"id": booking_id, "booking_number": 1042, "apartment_id": "apt-1",
                "check_in": "2026-07-10", "check_out": "2026-07-12", "total": 2996}


@pytest.fixture
def client():
    api_client.set_client(FakeClient())
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c
    api_client.set_client(None)


def test_home_renders(client):
    r = client.get("/")
    html = r.get_data(as_text=True)
    assert r.status_code == 200
    assert "Park View Suite" in html
    assert "data-apt-avail" in html          # availability badge slot
    assert 'id="availSummary"' in html       # search-filter summary slot
    assert 'id="availReset"' in html
    assert "Підібрати" not in html           # hero CTA is now "Забронювати"
    assert html.count("hsearch-submit") >= 1
    assert 'id="hs-apt"' in html             # in-form apartment selector
    assert html.count('<option value="apt-') == 2  # both public apts, TEST hidden
    assert "application/ld+json" in html     # structured data
    assert "TEST" not in html or "1 грн" not in html  # 1-UAH test apt hidden
    assert "&copy; 2025" not in html         # dynamic year, not hardcoded


def test_apartment_detail_collage_depends_on_photo_count(client):
    r = client.get("/apartment/apt-1")
    html = r.get_data(as_text=True)
    assert r.status_code == 200
    assert "has-collage" in html             # 7 photos -> collage rendered
    assert html.count("apt-collage-item") >= 5
    assert "booking-form" in html            # widget intact
    assert 'action="/book/apt-1"' in html    # booking flow untouched
    assert "application/ld+json" in html
    r = client.get("/apartment/apt-2")
    html = r.get_data(as_text=True)
    assert r.status_code == 200
    assert "has-collage" not in html         # 3 photos -> swiper only
    assert "apt-hero-swiper" in html


def test_home_test_mode_shows_test_apartment(client, monkeypatch):
    """/?test=1 appends the owner's TEST apartment (fetched by direct id)."""
    from config import Config
    monkeypatch.setattr(Config, "TEST_APARTMENT_IDS", ("apt-test",))

    class TestableClient(FakeClient):
        def get_apartment(self, apt_id, lang="ua"):
            if apt_id == "apt-test":
                return TEST_APT
            return super().get_apartment(apt_id, lang)

    api_client.set_client(TestableClient())
    html = client.get("/?test=1").get_data(as_text=True)
    assert 'value="apt-test"' in html        # in the hero selector
    assert html.count('<option value="') == 3
    # without the param the TEST apartment stays hidden
    html = client.get("/").get_data(as_text=True)
    assert 'value="apt-test"' not in html
    assert html.count('<option value="') == 2


def test_availability_api_and_backend_down(client):
    r = client.get("/api/availability/apt-1")
    assert r.status_code == 200
    assert r.get_json() == [{"from": "2026-07-01", "to": "2026-07-05"}]
    api_client.set_client(FakeClient(fail_availability=True))
    r = client.get("/api/availability/apt-1")
    assert r.status_code == 503
    assert "error" in r.get_json()


def test_confirmed_page(client):
    r = client.get("/booking-confirmed?ref=bk-1")
    html = r.get_data(as_text=True)
    assert r.status_code == 200
    assert "#1042" in html
    assert "/pay/bk-1" in html               # continue button untouched
