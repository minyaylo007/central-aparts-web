"""
Tests for aparts-web Flask routes.
All backoffice calls are handled by MockBackofficeClient (no real HTTP).
13.10.2026: один тест на гілку поведінки — 34 → 17, жодна гілка не втрачена.
"""

import re

import pytest

# critical: головна, квартира, бронь до Монобанку, підтвердження, мова
pytestmark = pytest.mark.critical

BOOK_FORM = {"check_in": "2026-05-20", "check_out": "2026-05-23", "guests": "2",
             "name": "Тест Тестів", "phone": "+380000000000"}
TEST_APT = {"id": "apt-uuid-test", "code": "TEST", "name": "Тестова кімната",
            "price": 1, "amenities": [], "description": "", "photos": []}


class TestHomePage:
    def test_home_lists_apartments_with_prices(self, client, mock_client):
        r = client.get("/")
        assert r.status_code == 200
        assert "Central Square".encode() in r.data
        assert "Апартаменти A".encode() in r.data
        assert "Апартаменти B".encode() in r.data
        digits = re.sub(r"\D", "", " ".join(re.findall(r'class="price-value">([^<]*)<', r.get_data(as_text=True))))
        assert "1490" in digits and "1850" in digits

    def test_home_short_reviews_badge_only_partner_in_footer(self, client, mock_client):
        """Аудит 26.11, п.6: у #reviews — лише бейдж Booking, блок власникам — посилання в підвалі."""
        html = client.get("/").get_data(as_text=True)
        assert 'class="booking-badge"' in html
        assert "trust-grid" not in html
        assert 'id="contact-cta"' not in html
        footer = html[html.index('<footer'):]
        assert "Здати нерухомість в управління" in footer

    def test_home_empty_apartments(self, client, mock_client):
        mock_client.apartments = []
        assert client.get("/").status_code == 200

    def test_test_apartment_hidden_on_home_but_reachable(self, client, mock_client):
        """1-UAH payment-test apartment: never on the public home page, but the
        owner can still open it by direct URL for payment tests."""
        mock_client.apartments.append(dict(TEST_APT))
        r = client.get("/")
        assert r.status_code == 200
        assert "Тестова кімната".encode() not in r.data
        assert "Апартаменти A".encode() in r.data  # real ones still shown
        assert client.get("/apartment/apt-uuid-test").status_code == 200


class TestApartmentDetail:
    def test_apartment_loads_with_price_and_availability(self, client, mock_client):
        mock_client.blocked = [{"from": "2026-05-10", "to": "2026-05-15"}]
        r = client.get("/apartment/apt-uuid-a")
        assert r.status_code == 200
        assert "Апартаменти A".encode() in r.data
        assert b"1490" in r.data
        assert b"2026-05-10" in r.data

    def test_apartment_not_found(self, client, mock_client):
        assert client.get("/apartment/nonexistent").status_code == 404

    def test_widget_defaults(self, client, mock_client):
        r = client.get("/apartment/apt-uuid-a")
        assert b'id="f-guests" value="2"' in r.data
        assert "Найкраща ціна — напряму".encode() in r.data


class TestBooking:
    def test_book_sends_form_and_redirects_to_payment(self, client, mock_client):
        r = client.post("/book/apt-uuid-a", data=BOOK_FORM)
        assert r.status_code == 302
        assert "monobank" in r.headers["Location"]
        lb = mock_client.last_booking
        assert lb["apartment_id"] == "apt-uuid-a"
        assert (lb["check_in"], lb["check_out"], lb["guests"]) == ("2026-05-20", "2026-05-23", 2)
        assert (lb["name"], lb["phone"]) == ("Тест Тестів", "+380000000000")

    def test_book_missing_fields_redirects_with_flash(self, client, mock_client):
        r = client.post("/book/apt-uuid-a",
                        data={"check_in": "2026-05-20", "check_out": "2026-05-23"},
                        follow_redirects=True)
        assert r.status_code == 200
        assert "Заповніть".encode() in r.data

    def test_book_dates_unavailable(self, client, mock_client):
        mock_client.raise_on_book = "dates_unavailable"
        r = client.post("/book/apt-uuid-a", data=BOOK_FORM, follow_redirects=True)
        assert r.status_code == 200
        assert "заброньовані".encode() in r.data

    def test_book_payment_unavailable(self, client, mock_client):
        mock_client.raise_on_book = "payment_unavailable"
        r = client.post("/book/apt-uuid-a", data=BOOK_FORM, follow_redirects=True)
        assert r.status_code == 200
        assert "оплати".encode() in r.data


class TestBookingConfirmed:
    def test_confirmed_with_ref_shows_details_without_cross_sell(self, client, mock_client):
        r = client.get("/booking-confirmed?ref=test1234")
        html = r.get_data(as_text=True)
        assert r.status_code == 200
        assert "підтверджено" in html
        assert "2026-05-01" in html  # check_in from mock
        assert "/pay/test1234" in html
        assert "5\u00a0220 грн" in html          # сума — як у віджеті (аудит 26.11, п.8)
        assert "Потрібен другий апартамент" not in html
        assert "Що далі?" not in html            # «що далі» — один рядок, не три кроки

    def test_unknown_ref_or_no_ref_is_not_confirmed(self, client, mock_client):
        """Аудит 26.11, п.4: бронь за ref не знайдено — нейтральний текст і контакт,
        без «підтверджено», без кнопки до документів і без події Purchase."""
        for url in ("/booking-confirmed?ref=nonexistent", "/booking-confirmed"):
            r = client.get(url)
            html = r.get_data(as_text=True)
            assert r.status_code == 200
            assert "підтверджено" not in html
            assert "Не бачимо цього бронювання" in html
            assert "/pay/" not in html
            assert "'Purchase'" not in html
            assert "tel:+380502648312" in html

    def test_confirmed_survives_backoffice_error(self, client, mock_client):
        """Бек-офіс не відповів — сторінка жива, але нічого не обіцяє."""
        def boom(booking_id):
            raise RuntimeError("api down")
        mock_client.get_booking = boom
        r = client.get("/booking-confirmed?ref=test1234")
        assert r.status_code == 200
        assert "підтверджено".encode() not in r.data


class TestLang:
    def test_lang_switch_sets_session_and_ignores_unknown(self, client, mock_client):
        assert client.get("/lang/en").status_code == 302
        with client.session_transaction() as sess:
            assert sess.get("lang") == "en"
        assert client.get("/lang/fr").status_code == 302
        with client.session_transaction() as sess:
            assert sess.get("lang") == "en"      # невідома мова нічого не міняє
        assert client.get("/lang/ua").status_code == 302
        with client.session_transaction() as sess:
            assert sess.get("lang") == "ua"

    def test_translations_on_home(self, client, mock_client):
        with client.session_transaction() as sess:
            sess["lang"] = "en"
        assert b"Book Now" in client.get("/").data
        with client.session_transaction() as sess:
            sess["lang"] = "ua"
        assert "Забронювати".encode("utf-8") in client.get("/").data


class TestErrorPages:
    def test_404_returns_404(self, client, mock_client):
        assert client.get("/nonexistent-page").status_code == 404
