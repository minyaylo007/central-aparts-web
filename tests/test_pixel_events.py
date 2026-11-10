"""Meta Pixel у шаблонах: ViewContent на квартирі, Purchase лише з номером броні й з eventID.
Поведінку в браузері (Search, InitiateCheckout) стереже смока e2e/tests/08-pixel.spec.ts."""
import pathlib
import re


def test_apartment_view_content_has_content_id_and_price(client, mock_client):
    html = client.get("/apartment/apt-uuid-a").get_data(as_text=True)
    call = re.search(r"fbq\('track', 'ViewContent', \{(.*?)\}\);", html, re.S).group(1)
    assert 'content_ids: ["apt-uuid-a"]' in call
    assert "content_type: 'product'" in call
    assert "value: 1490" in call and "currency: 'UAH'" in call


def test_purchase_has_event_id_content_ids_and_fires_once(client, mock_client):
    html = client.get("/booking-confirmed?ref=test1234").get_data(as_text=True)
    assert "{ eventID: id }" in html and 'var id = "test1234"' in html
    assert "localStorage.getItem(key)" in html                 # повторне відкриття не дублює
    assert 'data.content_ids = ["apt-uuid-a"];' in html


def test_group_purchase_lists_every_apartment(client, mock_client):
    mock_client.booking_status = dict(mock_client.booking_status, group={
        "total": 9881, "items": [{"apartment_id": "apt-uuid-a", "guests": 2},
                                 {"apartment_id": "apt-uuid-b", "guests": 1}]})
    html = client.get("/booking-confirmed?ref=test1234").get_data(as_text=True)
    assert 'data.content_ids = ["apt-uuid-a", "apt-uuid-b"];' in html
    assert "data.num_items = 2;" in html


def test_no_purchase_without_booking_ref(client, mock_client):
    html = client.get("/booking-confirmed").get_data(as_text=True)
    assert "'Purchase'" not in html


def test_main_js_events_carry_content_ids():
    js = (pathlib.Path(__file__).resolve().parent.parent / "static" / "main.js").read_text(encoding="utf-8")
    assert "fbq('track'" not in js.replace("fbq('track', ev,", "")   # усе — через pixel()
    ic = re.findall(r"pixel\('InitiateCheckout', \{ content_ids: \[String\(APT_ID\)\]", js)
    assert len(ic) == 2                                           # і з ціною сервера, і без неї
    assert "pixel('Search', { content_ids: found" in js
