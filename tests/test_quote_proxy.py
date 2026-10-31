"""Tests for the /api/quote/<apt_id> proxy (server-truth stay pricing)."""

import pytest

# critical: котирування — ціна, яку бачить і платить гість
pytestmark = pytest.mark.critical


class TestQuoteProxy:
    def test_returns_quote(self, client, mock_client):
        r = client.get("/api/quote/apt-uuid-a"
                       "?check_in=2030-06-01&check_out=2030-06-03&guests=2")
        assert r.status_code == 200
        q = r.get_json()
        assert q["base_amount"] == 4300
        assert q["total"] == 4492
        assert q["uniform"] is False
        assert len(q["nightly"]) == 2

    def test_invalid_input_400(self, client, mock_client):
        mock_client.quote_result = "invalid"
        r = client.get("/api/quote/apt-uuid-a?check_in=x&check_out=y")
        assert r.status_code == 400

    def test_backoffice_down_503(self, client, mock_client):
        mock_client.quote_result = RuntimeError("connection refused")
        r = client.get("/api/quote/apt-uuid-a"
                       "?check_in=2030-06-01&check_out=2030-06-03")
        assert r.status_code == 503

    def test_booking_flow_unchanged(self, client, mock_client):
        # regression guard: the quote feature must not touch /book
        r = client.post("/book/apt-uuid-a", data={
            "check_in": "2030-06-01", "check_out": "2030-06-03",
            "name": "Тест", "phone": "+380000000000", "guests": "2",
        })
        assert r.status_code == 302
        assert "pay.monobank.ua" in r.headers["Location"]
        assert mock_client.last_booking["check_in"] == "2030-06-01"
