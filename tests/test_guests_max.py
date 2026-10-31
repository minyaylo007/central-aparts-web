"""Стеля гостей (30.10, розбір (14)): одне число — app.GUESTS_MAX — у всіх трьох лічильниках
(головна, віджет квартири, select групи) і в підказці «+ на максимумі». JS бере його з data-max."""
import re

import app as app_module


def _render(client, monkeypatch, path, n=3):
    # не 4: так видно, що число йде з константи, а не зашите в шаблон
    monkeypatch.setattr(app_module, "GUESTS_MAX", n)
    return client.get(path).get_data(as_text=True)


def test_home_counter_takes_max_from_server(client, mock_client, monkeypatch):
    html = _render(client, monkeypatch, "/")
    assert re.search(r'class="hsearch-guests" data-max="3"', html)
    m = re.search(r'<p class="hsearch-hint" id="hs-guests-hint"[^>]*>([^<]*)</p>', html)
    assert m and "hidden" in m.group(0)
    assert m.group(1) == "Не більше 3 гостей на один апартамент"


def test_apartment_counter_and_group_select_take_max_from_server(client, mock_client, monkeypatch):
    html = _render(client, monkeypatch, "/apartment/apt-uuid-a")
    assert re.search(r'class="guest-counter"[^>]*data-max="3"', html)
    m = re.search(r'<p class="guests-max-hint" id="guest-max-hint"[^>]*>([^<]*)</p>', html)
    assert m and "hidden" in m.group(0)
    assert m.group(1) == "Не більше 3 гостей на один апартамент"
    selects = re.findall(r'<select class="group-room-guests".*?</select>', html, re.S)
    assert selects, "у фейку є інші квартири — група мусить рендеритись"
    for sel in selects:
        assert re.findall(r'<option value="(\d+)"', sel) == ["1", "2", "3"]


def test_guests_max_hint_in_english(client, mock_client, monkeypatch):
    client.get("/lang/en")
    html = _render(client, monkeypatch, "/")
    assert "Up to 3 guests per apartment" in html
