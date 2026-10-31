"""Знімок HTML сторінок сайту на підмінному бек-офісі — вхід для e2e/tools/build-css.mjs.

    ~/venvs/central-aparts-site/bin/python scripts/dump_pages.py <тека>

Справжній Flask-застосунок (app.py, шаблони) + e2e/fake_backoffice.py, без мережі й без
порту: сторінки рендерить тестовий клієнт. У теку лягають <імʼя>.html і pages.json
(імʼя → шлях). Нічого, крім цієї теки, не пише; живих броней не створює.
"""
import json
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [REPO_ROOT, os.path.join(REPO_ROOT, "e2e")]

from api_client import set_client  # noqa: E402
from app import app  # noqa: E402
from fake_backoffice import APT_A, FakeBackofficeClient  # noqa: E402


def main(out_dir: str) -> None:
    os.makedirs(out_dir, exist_ok=True)
    fake = FakeBackofficeClient()
    set_client(fake)
    fake.bookings["dump-1"] = {
        "id": "dump-1", "status": "paid", "apartment": "A", "apartment_id": APT_A,
        "check_in": "2026-11-20", "check_out": "2026-11-22", "nights": 2, "total": 4604,
        "booking_number": "dump-1",
    }
    pages = {
        "home": "/",
        "home-en": "/",
        "apartment": f"/apartment/{APT_A}",
        "apartment-dates": f"/apartment/{APT_A}?check_in=2026-11-20&check_out=2026-11-22&guests=2&book=1",
        "apartment-flash": None,  # POST без полів → flash «заповніть усі поля» над сторінкою
        "confirmed": "/booking-confirmed?ref=dump-1",
        "error": "/apartment/no-such-apartment",
    }
    manifest = {}
    for name, path in pages.items():
        with app.test_client() as client:
            if name == "home-en":
                client.get("/lang/en")
            if path is None:
                resp = client.post(f"/book/{APT_A}", data={}, follow_redirects=True)
                path = resp.request.full_path
            else:
                resp = client.get(path)
            if resp.status_code not in (200, 404):
                raise SystemExit(f"{name}: {path} → {resp.status_code}")
            with open(os.path.join(out_dir, f"{name}.html"), "wb") as f:
                f.write(resp.data)
            manifest[name] = path
    with open(os.path.join(out_dir, "pages.json"), "w") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=1)
    print(f"{len(manifest)} сторінок → {out_dir}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "tmp/css-pages")
