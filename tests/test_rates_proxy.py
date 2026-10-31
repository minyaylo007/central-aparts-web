"""Tests for the /api/rates/<apt_id> proxy + booking-page conversion elements
(day-price hints, honest scarcity line, perks row)."""
import re

import pytest


@pytest.mark.critical
class TestRatesProxy:
    def test_returns_rates(self, client, mock_client):
        r = client.get("/api/rates/apt-uuid-a")
        assert r.status_code == 200
        data = r.get_json()
        assert data["rates"]["2030-06-01"] == 2000
        assert data["rates"]["2030-06-02"] == 2300

    def test_cache_control_header(self, client, mock_client):
        r = client.get("/api/rates/apt-uuid-a")
        assert r.headers.get("Cache-Control") == "public, max-age=900"

    def test_unknown_apartment_404(self, client, mock_client):
        mock_client.rates_result = None
        assert client.get("/api/rates/nope").status_code == 404

    def test_backoffice_down_503(self, client, mock_client):
        mock_client.rates_result = RuntimeError("connection refused")
        assert client.get("/api/rates/apt-uuid-a").status_code == 503

    def test_booking_flow_unchanged(self, client, mock_client):
        # regression guard: the rates feature must not touch /book
        r = client.post("/book/apt-uuid-a", data={
            "check_in": "2030-06-01", "check_out": "2030-06-03",
            "name": "Тест", "phone": "+380000000000", "guests": "2",
        })
        assert r.status_code == 302
        assert "pay.monobank.ua" in r.headers["Location"]


class TestApartmentPageConversionElements:
    def _html(self, client):
        r = client.get("/apartment/apt-uuid-a")
        assert r.status_code == 200
        return r.get_data(as_text=True)

    def test_scarcity_line_present_but_hidden_by_default(self, client, mock_client):
        html = self._html(client)
        assert 'id="pb-scarcity"' in html
        assert "Залишився лише 1 апартамент на ці дати" in html
        # hidden until JS reveals it from a server quote with only_one_left=true
        m = re.search(r'<div[^>]*id="pb-scarcity"[^>]*>', html)
        assert m and "hidden" in m.group(0)

    def test_perks_row_rendered(self, client, mock_client):
        html = self._html(client)
        assert 'class="widget-perks"' in html
        assert "Найкраща ціна — без комісій" in html
        assert "Гнучке скасування" in html
        assert "Пряме спілкування з господарем" in html

    def test_no_countdown_or_fake_urgency(self, client, mock_client):
        html = self._html(client).lower()
        assert "countdown" not in html
        assert "таймер" not in html
