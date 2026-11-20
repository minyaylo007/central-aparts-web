"""Swiper з CDN може не дійти (блокувальник, мережа) — сторінка мусить жити без нього.

21.11: гість на «/» отримав «ReferenceError: Swiper is not defined»; виняток обривав інлайн-скрипт,
і фото карток (img.apt-row-img[data-src], нижче в тому ж скрипті) не підвантажувались.
Поведінку в браузері стереже смока 01-delivery «4б»; тут — дешева перевірка розмітки.
"""
import os
import re

TEMPLATES = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "templates")
GUARD = "typeof Swiper"


def _read(name):
    with open(os.path.join(TEMPLATES, name), encoding="utf-8") as f:
        return f.read()


def test_home_creates_carousels_only_when_swiper_loaded():
    html = _read("public_home.html")
    lines = html.splitlines()
    calls = [i for i, line in enumerate(lines) if "new Swiper(" in line]
    assert calls, "на головній нема new Swiper — тест застарів"
    for i in calls:
        # перевірка — у рядку з циклом каруселей, найближчому вище за виклик
        head = next(line for line in reversed(lines[:i]) if "apt-photo-swiper" in line)
        assert "typeof Swiper === 'function'" in head, f"рядок {i + 1}: new Swiper без перевірки"


def test_home_card_photos_load_outside_swiper_guard():
    html = _read("public_home.html")
    guard = html.index(GUARD)
    loader = html.index("img.apt-row-img[data-src]")
    # завантажувач фото — окремий вираз після циклу каруселей, а не всередині нього
    between = html[guard:loader]
    assert re.search(r"\}\);\s*\n\s*//[^\n]*\n\s*__afterLoad\(function", between), \
        "завантаження фото по data-src мусить іти поза перевіркою Swiper"


def test_apartment_gallery_init_returns_without_swiper():
    html = _read("public_apartment.html")
    body = html[html.index("function initAptSwiper()"):]
    body = body[: body.index("new Swiper(")]
    assert "typeof Swiper !== 'function'" in body and "return" in body, \
        "initAptSwiper: без Swiper має вийти до new Swiper"
