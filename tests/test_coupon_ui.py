"""Returning-guest coupon UI (2026-08-14): the /api/coupon/check proxy, the
coupon_phone pass-through in /api/quote and /book, and the widget markup."""
import pytest

from config import Config


@pytest.mark.critical
class TestCouponCheckProxy:
    def test_eligible_passthrough(self, client, mock_client):
        mock_client.coupon_result = {"eligible": True, "amount": 500,
                                     "min_total": 4000}
        r = client.post("/api/coupon/check", json={"phone": "+380000000000"})
        assert r.status_code == 200
        assert r.get_json() == {"eligible": True, "amount": 500,
                                "min_total": 4000}
        assert mock_client.last_coupon_phone == "+380000000000"

    def test_generic_negative_passthrough(self, client, mock_client):
        r = client.post("/api/coupon/check", json={"phone": "+380000000000"})
        assert r.get_json() == {"eligible": False}

    def test_empty_phone_short_circuits(self, client, mock_client):
        r = client.post("/api/coupon/check", json={"phone": ""})
        assert r.get_json() == {"eligible": False}
        assert mock_client.last_coupon_phone is None   # backoffice never called

    def test_backoffice_error_503(self, client, mock_client):
        mock_client.coupon_result = RuntimeError("down")
        r = client.post("/api/coupon/check", json={"phone": "+380000000000"})
        assert r.status_code == 503


@pytest.mark.critical
class TestQuoteCouponPassthrough:
    def test_coupon_phone_forwarded(self, client, mock_client):
        mock_client.quote_result = dict(mock_client.quote_result)
        mock_client.quote_result.update(coupon_discount=-500,
                                        coupon_applied=True, total=3992)
        r = client.get("/api/quote/apt-uuid-a?check_in=2030-06-01"
                       "&check_out=2030-06-03&guests=2"
                       "&coupon_phone=%2B380000000000")
        assert r.status_code == 200
        assert r.get_json()["coupon_discount"] == -500
        assert mock_client.last_quote["coupon_phone"] == "+380000000000"

    def test_without_coupon_phone_empty(self, client, mock_client):
        r = client.get("/api/quote/apt-uuid-a?check_in=2030-06-01"
                       "&check_out=2030-06-03&guests=2")
        assert r.status_code == 200
        assert mock_client.last_quote["coupon_phone"] == ""


@pytest.mark.critical
class TestBookCouponPassthrough:
    def test_coupon_phone_reaches_backoffice(self, client, mock_client):
        r = client.post("/book/apt-uuid-a", data={
            "check_in": "2030-06-01", "check_out": "2030-06-03",
            "guests": "2", "name": "Іван", "phone": "+380000000000",
            "coupon_phone": "+380000000000",
        })
        assert r.status_code == 302
        assert mock_client.last_booking["coupon_phone"] == "+380000000000"

    def test_without_coupon_still_books(self, client, mock_client):
        r = client.post("/book/apt-uuid-a", data={
            "check_in": "2030-06-01", "check_out": "2030-06-03",
            "guests": "2", "name": "Іван", "phone": "+380000000000",
        })
        assert r.status_code == 302
        assert mock_client.last_booking["coupon_phone"] == ""
        assert mock_client.last_booking["coupon_expected"] == ""


@pytest.mark.critical
class TestCouponExpectedWiring:
    """The widget saw the -500 line -> /book carries coupon_expected="1" and a
    backoffice 409 coupon_unavailable flashes the honest re-quote message."""

    def test_coupon_expected_forwarded(self, client, mock_client):
        r = client.post("/book/apt-uuid-a", data={
            "check_in": "2030-06-01", "check_out": "2030-06-03",
            "guests": "2", "name": "Іван", "phone": "+380000000000",
            "coupon_phone": "+380000000000", "coupon_expected": "1",
        })
        assert r.status_code == 302
        assert mock_client.last_booking["coupon_phone"] == "+380000000000"
        assert mock_client.last_booking["coupon_expected"] == "1"

    def test_coupon_unavailable_409_flashes_honest_message(self, client,
                                                           mock_client):
        mock_client.raise_on_book = "coupon_unavailable"
        r = client.post("/book/apt-uuid-a", data={
            "check_in": "2030-06-01", "check_out": "2030-06-03",
            "guests": "2", "name": "Іван", "phone": "+380000000000",
            "coupon_phone": "+380000000000", "coupon_expected": "1",
        }, follow_redirects=True)
        assert r.status_code == 200
        html = r.data.decode("utf-8")
        assert "купон уже недійсний" in html
        # generic booking error NOT shown — this is the specific coupon message
        assert "Не вдалося створити платіж" not in html

    def test_dates_unavailable_flash_unchanged(self, client, mock_client):
        mock_client.raise_on_book = "dates_unavailable"
        r = client.post("/book/apt-uuid-a", data={
            "check_in": "2030-06-01", "check_out": "2030-06-03",
            "guests": "2", "name": "Іван", "phone": "+380000000000",
        }, follow_redirects=True)
        assert "купон уже недійсний" not in r.data.decode("utf-8")


@pytest.mark.critical
class TestClientIpForwarding:
    """Режим за замовчуванням (TRUSTED_PROXY_COUNT не задано) — перший елемент
    X-Forwarded-For, як до 001. Випадки з N — у tests/test_client_ip.py."""

    @pytest.fixture(autouse=True)
    def _default_mode(self, monkeypatch):
        monkeypatch.setattr(Config, "TRUSTED_PROXY_COUNT", None)
        monkeypatch.setattr(Config, "TRUSTED_PROXY_COUNT_INVALID", False)

    def test_coupon_check_forwards_visitor_ip(self, client, mock_client):
        client.post("/api/coupon/check", json={"phone": "+380000000000"},
                    headers={"X-Forwarded-For": "203.0.113.7, 10.0.0.1"})
        assert mock_client.last_coupon_client_ip == "203.0.113.7"

    def test_quote_forwards_visitor_ip(self, client, mock_client):
        client.get("/api/quote/apt-uuid-a?check_in=2030-06-01"
                   "&check_out=2030-06-03&guests=2",
                   headers={"X-Forwarded-For": "203.0.113.7"})
        assert mock_client.last_quote["client_ip"] == "203.0.113.7"


class TestWidgetMarkup:
    def test_apartment_page_has_coupon_ui(self, client, mock_client):
        html = client.get("/apartment/apt-uuid-a").data.decode("utf-8")
        # collapsed entry link
        assert "Маєте купон постійного гостя?" in html
        assert 'id="coupon-toggle"' in html
        # phone input + apply + status + hidden pass-through field
        assert 'id="coupon-phone-input"' in html
        assert 'id="coupon-apply"' in html
        assert 'id="coupon-status"' in html
        assert 'name="coupon_phone"' in html
        # breakdown line (hidden until the server quote confirms)
        assert 'id="pb-coupon-row"' in html
        assert "Купон постійного гостя" in html
        # i18n strings for the JS
        assert "COUPON_TXT" in html

    def test_coupon_body_collapsed_by_default(self, client, mock_client):
        html = client.get("/apartment/apt-uuid-a").data.decode("utf-8")
        import re
        m = re.search(r'<div class="coupon-body"[^>]*>', html)
        assert m and "hidden" in m.group(0)

    def test_apartment_page_has_coupon_expected_hidden_field(self, client,
                                                             mock_client):
        html = client.get("/apartment/apt-uuid-a").data.decode("utf-8")
        assert 'name="coupon_expected"' in html
        assert 'id="f-coupon-expected"' in html


@pytest.mark.critical
class TestHoldLimitMessage:
    """409 too_many_holds від бекофісу (ліміт неоплачених холдів на телефон,
    інцидент 2026-09-09) мусить читатись як зрозуміла порада, а не як «сталася
    помилка» — гість не зробив нічого поганого."""

    def test_too_many_holds_flashes_actionable_message(self, client, mock_client):
        mock_client.raise_on_book = "too_many_holds"
        r = client.post("/book/apt-uuid-a", data={
            "check_in": "2030-06-01", "check_out": "2030-06-03",
            "guests": "2", "name": "Anastasia", "phone": "+380000000000",
        }, follow_redirects=True)
        assert r.status_code == 200
        html = r.data.decode("utf-8")
        assert "незавершені бронювання" in html
        assert "Не вдалося створити платіж" not in html

    def test_too_many_holds_carries_dates_back(self, client, mock_client):
        """Дати несемо назад: гість має побачити той самий стей і завершити
        оплату, а не порожню форму."""
        mock_client.raise_on_book = "too_many_holds"
        r = client.post("/book/apt-uuid-a", data={
            "check_in": "2030-06-01", "check_out": "2030-06-03",
            "guests": "2", "name": "Anastasia", "phone": "+380000000000",
        })
        assert r.status_code == 302
        assert "check_in=2030-06-01" in r.headers["Location"]
        assert "check_out=2030-06-03" in r.headers["Location"]
