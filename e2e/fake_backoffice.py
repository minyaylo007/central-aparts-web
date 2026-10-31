"""
Підмінний бек-офіс для Playwright-смоки (SMOKE_BACKEND=fake).

e2e/fake_wsgi.py ставить його через api_client.set_client() — і сайт, піднятий
справжнім gunicorn, замість HTTP до cv.central-aparts.store питає цей обʼєкт.
Мережі нема взагалі.

Форма відповідей — як у живого бек-офісу (звірено 2026-09-11 читаючими ручками
/apartments, /availability, /quote, /rates) і як її читає static/main.js.

Юніт-тестів це НЕ стосується: у tests/conftest.py свій MockBackofficeClient під
конкретні асерти. Тут — правдоподібний «світ» для браузера: дати рахуються від
сьогодні, ціна залежить від дня тижня, котирування — від дат і гостей.

Як додати дані для своєї групи — e2e/README.md, розділ «Підміна бек-офісу».
"""
import copy
import re
import threading
from datetime import date, timedelta

from api_client import BackofficeError
from config import Config

# ---------------------------------------------------------------------------
# Квартири. id — стабільні: тести можуть посилатися на них напряму.
# ---------------------------------------------------------------------------

APT_A = "5a0e0000-0000-4000-8000-00000000000a"
APT_B = "5a0e0000-0000-4000-8000-00000000000b"
# 1-UAH тестова квартира: той самий id, що в config.py (TEST_APARTMENT_IDS), —
# тож /?test=1 показує її рівно тим кодом, що й на проді
TEST_APT = (Config.TEST_APARTMENT_IDS or ("5a0e0000-0000-4000-8000-0000000000ff",))[0]

_IMG = ["/static/img/hero.webp", "/static/img/home-1.webp"]

APARTMENTS = [
    {
        "id": APT_A, "code": "A", "price": 2200,
        "name": {"ua": "Апартаменти A (смока)", "en": "Apartment A (smoke)"},
        "description": {"ua": "Підмінна квартира A для смоки.", "en": "Fake apartment A for the smoke."},
        "amenities": ["WiFi", "Кухня", "Кондиціонер", "Пральна машина", "Самозаселення"],
        # 6 фото (>= 5 вмикає колаж галереї); ?n= — щоб лайтбокс бачив різні кадри
        "photos": [f"{_IMG[i % 2]}?n={i}" for i in range(6)],
    },
    {
        "id": APT_B, "code": "B", "price": 2400,
        "name": {"ua": "Апартаменти B (смока)", "en": "Apartment B (smoke)"},
        "description": {"ua": "Підмінна квартира B для смоки.", "en": "Fake apartment B for the smoke."},
        "amenities": ["WiFi", "Джакузі"],
        "photos": list(_IMG),
    },
    {
        "id": TEST_APT, "code": "T", "price": 1,
        "name": {"ua": "ТЕСТ 1 грн (смока)", "en": "TEST 1 UAH (smoke)"},
        "description": {"ua": "Тестова квартира для платежу 1 грн.", "en": "1-UAH payment test."},
        "amenities": [],
        "photos": [],
    },
]

# Зайняті ночі: (з, по) у днях від СЬОГОДНІ; «по» — день виїзду, він вільний для
# заїзду (як у бойовому бек-офісі і в main.js: date >= from && date < to).
BLOCKED_OFFSETS = {
    APT_A: [(10, 13), (20, 21)],
    APT_B: [(5, 8)],
    TEST_APT: [],
}

WEEKEND_SURCHARGE = 300    # пт/сб дорожчі — щоб ціни в календарі (група 2) не були рівні
RATES_DAYS = 60            # скільки днів уперед віддає /rates (як бойовий)
# Туристичний збір за ніч за гостя. НАВМИСНО не 48 (константа в main.js) і не 26
# (бойовий): смока групи 2 має бачити, що сума в віджеті — від сервера, а не з JS.
TAX_PER_GUEST_NIGHT = 37
BOOKING_MARKUP = 1.25      # booking_total — «ціна на Booking» для порівняння, вища за нашу

# Купон постійного гостя: телефони з правом (порівнюються за останніми 9 цифрами)
COUPON_PHONES = {"+380000000500"}
COUPON_AMOUNT = 500
COUPON_MIN_TOTAL = 4000

PAYMENT_URL = "https://pay.monobank.ua/smoke-fake-invoice"   # у браузері це заглушка сторожа

# Спека 108, група: 2–6 квартир однією оплатою (як бойовий /book-group)
GROUP_MIN, GROUP_MAX = 2, 6
# Гонка «зайняли між сметою і оплатою»: імʼя гостя з цією позначкою → /book-group
# відповідає 409 dates_unavailable на ОСТАННЮ квартиру групи. Інакше сміта вже
# бачить зайнятість, і браузер до цієї гілки не дійде (смока групи 6)
GROUP_RACE_MARK = "[гонка]"


def _digits9(phone: str) -> str:
    return re.sub(r"\D", "", phone or "")[-9:]


class FakeBackofficeClient:
    """Той самий інтерфейс, що api_client.BackofficeClient."""

    def __init__(self):
        self._lock = threading.Lock()
        self.bookings: dict = {}
        self.last_booking: dict = {}
        # усі форми /book за прогін, по порядку. Тести паралельні (desktop і mobile —
        # одночасно), тож last_booking може перезаписати сусід — фільтруйте тут за своїм імʼям
        self.booking_requests: list = []
        self.last_quote: dict = {}
        self.last_coupon_phone = None
        # спека 108: усі тіла /book-group і /quote-group за прогін, по порядку
        self.group_requests: list = []
        self.last_group_quote: dict = {}

    # --- довідники ---------------------------------------------------------

    @staticmethod
    def _find(apt_id):
        return next((a for a in APARTMENTS if a["id"] == str(apt_id)), None)

    @staticmethod
    def _localize(apt, lang):
        out = dict(apt)
        for k in ("name", "description"):
            out[k] = apt[k].get(lang) or apt[k]["ua"]
        out["amenities"] = list(apt["amenities"])
        out["photos"] = list(apt["photos"])
        return out

    @staticmethod
    def _blocked_ranges(apt_id):
        today = date.today()
        return [(today + timedelta(a), today + timedelta(b))
                for a, b in BLOCKED_OFFSETS.get(apt_id, [])]

    def _night_price(self, apt, d: date) -> int:
        return apt["price"] + (WEEKEND_SURCHARGE if d.weekday() in (4, 5) and apt["price"] > 1 else 0)

    @staticmethod
    def _coupon_ok(phone) -> bool:
        return bool(phone) and _digits9(phone) in {_digits9(p) for p in COUPON_PHONES}

    # --- читаючі ручки -----------------------------------------------------

    def list_apartments(self, lang="ua"):
        # як бойовий: тестова квартира в загальному списку НЕ віддається
        return [self._localize(a, lang) for a in APARTMENTS if a["id"] != TEST_APT]

    def get_apartment(self, apt_id, lang="ua"):
        apt = self._find(apt_id)
        return self._localize(apt, lang) if apt else None

    def get_availability(self, apt_id):
        return [{"from": f.isoformat(), "to": t.isoformat()} for f, t in self._blocked_ranges(apt_id)]

    def get_rates(self, apt_id):
        apt = self._find(apt_id)
        if not apt:
            return None
        today = date.today()
        return {"rates": {(today + timedelta(i)).isoformat(): self._night_price(apt, today + timedelta(i))
                          for i in range(RATES_DAYS)}}

    def get_quote(self, apt_id, check_in, check_out, guests=1, coupon_phone="", client_ip=""):
        with self._lock:
            self.last_quote = dict(apt_id=apt_id, check_in=check_in, check_out=check_out,
                                   guests=guests, coupon_phone=coupon_phone)
        apt = self._find(apt_id)
        try:
            ci, co, g = date.fromisoformat(check_in), date.fromisoformat(check_out), int(guests)
        except (TypeError, ValueError):
            return None
        nights = (co - ci).days
        if not apt or nights < 1 or ci < date.today() or not 1 <= g <= 4:
            return None                                   # бойовий відповідає 400 → сайт 400
        nightly = [{"date": (ci + timedelta(i)).isoformat(), "price": self._night_price(apt, ci + timedelta(i))}
                   for i in range(nights)]
        prices = [n["price"] for n in nightly]
        base = sum(prices)
        tax = TAX_PER_GUEST_NIGHT * nights * g
        q = {
            "nights": nights, "base_amount": base, "tourist_tax": tax, "total": base + tax,
            "tax_per_night": TAX_PER_GUEST_NIGHT, "uniform": len(set(prices)) == 1,
            "min_night": min(prices), "max_night": max(prices), "only_one_left": False,
            "booking_total": int(round(base * BOOKING_MARKUP / 100.0) * 100),
            "nightly": nightly,
        }
        if self._coupon_ok(coupon_phone):
            if base + tax >= COUPON_MIN_TOTAL:
                q.update(coupon_discount=-COUPON_AMOUNT, coupon_applied=True, total=base + tax - COUPON_AMOUNT)
            else:
                q.update(coupon_applied=False, coupon_min_total=COUPON_MIN_TOTAL)
        # невідомий телефон — жодних coupon_* полів (так робить бойовий: без сигналу для перебору)
        return q

    # --- «пишучі» ручки (у fake нічого нікуди не пишуть) ------------------

    def check_coupon(self, phone, client_ip=""):
        with self._lock:
            self.last_coupon_phone = phone
        if self._coupon_ok(phone):
            return {"eligible": True, "amount": COUPON_AMOUNT, "min_total": COUPON_MIN_TOTAL}
        return {"eligible": False}

    def create_booking(self, apartment_id, check_in, check_out, guests, name, phone,
                       coupon_phone="", coupon_expected="", idempotency_key=""):
        with self._lock:
            self.last_booking = dict(apartment_id=apartment_id, check_in=check_in, check_out=check_out,
                                     guests=guests, name=name, phone=phone, coupon_phone=coupon_phone,
                                     coupon_expected=coupon_expected, idempotency_key=idempotency_key)
            self.booking_requests.append(dict(self.last_booking))
        try:
            ci, co = date.fromisoformat(check_in), date.fromisoformat(check_out)
        except ValueError:
            raise BackofficeError("bad_request")
        if not self._find(apartment_id) or co <= ci:
            raise BackofficeError("bad_request")
        if any(ci < t and co > f for f, t in self._blocked_ranges(apartment_id)):
            raise BackofficeError("dates_unavailable")
        if coupon_expected and not self._coupon_ok(coupon_phone):
            raise BackofficeError("coupon_unavailable")
        q = self.get_quote(apartment_id, check_in, check_out, guests, coupon_phone) or {}
        with self._lock:
            booking_id = f"SMOKE{len(self.bookings) + 1:04d}"
            self.bookings[booking_id] = {
                "id": booking_id, "status": "paid", "apartment": self._find(apartment_id)["code"],
                "apartment_id": apartment_id, "check_in": check_in, "check_out": check_out,
                "nights": q.get("nights"), "total": q.get("total"), "booking_number": booking_id,
            }
        return {"booking_id": booking_id, "payment_url": f"{PAYMENT_URL}?ref={booking_id}",
                "total": q.get("total"), "nights": q.get("nights"),
                "base_amount": q.get("base_amount"), "tourist_tax": q.get("tourist_tax")}

    # --- спека 108: кілька квартир однією оплатою -------------------------

    def _group_check(self, ids, check_in, check_out, guests):
        """Перевірки бойового _parse_group_* у тому самому порядку → (ci, co)."""
        if not ids or len(ids) != len(guests):
            raise BackofficeError("invalid_items", {"error": "invalid_items"})
        if len(ids) < GROUP_MIN:
            raise BackofficeError("need_two_apartments", {"error": "need_two_apartments"})
        if len(ids) > GROUP_MAX:
            raise BackofficeError("too_many_apartments", {"error": "too_many_apartments"})
        if len(set(ids)) != len(ids):
            raise BackofficeError("duplicate_apartment", {"error": "duplicate_apartment"})
        try:
            gs = [int(g) for g in guests]
        except (TypeError, ValueError):
            raise BackofficeError("invalid_guests", {"error": "invalid_guests"})
        if any(not 1 <= g <= 4 for g in gs):
            raise BackofficeError("invalid_guests", {"error": "invalid_guests"})
        try:
            ci, co = date.fromisoformat(check_in), date.fromisoformat(check_out)
        except (TypeError, ValueError):
            raise BackofficeError("invalid_dates", {"error": "invalid_dates"})
        if co <= ci or ci < date.today() or (co - ci).days > 90:
            raise BackofficeError("invalid_dates", {"error": "invalid_dates"})
        for apt_id in ids:
            if not self._find(apt_id):
                raise BackofficeError("apartment_not_found",
                                      {"error": "apartment_not_found", "apartment_id": apt_id})
        return ci, co, gs

    def _group_rooms(self, ids, gs, ci, co, lang="ua"):
        rooms = []
        for apt_id, g in zip(ids, gs):
            q = self.get_quote(apt_id, ci.isoformat(), co.isoformat(), g) or {}
            apt = self._find(apt_id)
            rooms.append({
                "apartment_id": apt_id, "apartment": apt["code"],
                "name": apt["name"].get(lang) or apt["name"]["ua"], "guests": g,
                "nights": q.get("nights"), "base_amount": q.get("base_amount"),
                "tourist_tax": q.get("tourist_tax"), "total": q.get("total"),
                "nightly": q.get("nightly"), "booking_total": q.get("booking_total"),
            })
        return rooms

    def get_group_quote(self, apartment_ids, check_in, check_out, guests, lang="ua"):
        ids = [str(i) for i in apartment_ids]
        with self._lock:
            self.last_group_quote = dict(apartment_ids=ids, check_in=check_in, check_out=check_out,
                                         guests=list(guests))
        ci, co, gs = self._group_check(ids, check_in, check_out, guests)
        rooms = self._group_rooms(ids, gs, ci, co, lang)
        unavailable = [i for i in ids if any(ci < t and co > f for f, t in self._blocked_ranges(i))]
        base = sum(r["base_amount"] for r in rooms)
        tax = sum(r["tourist_tax"] for r in rooms)
        return {"nights": (co - ci).days, "check_in": check_in, "check_out": check_out,
                "rooms": rooms, "unavailable": unavailable, "available": not unavailable,
                "base_amount": base, "tourist_tax": tax, "total": base + tax,
                "booking_total": sum(r["booking_total"] or 0 for r in rooms)}

    def create_group_booking(self, items, check_in, check_out, name, phone, idempotency_key=""):
        with self._lock:
            self.group_requests.append(dict(items=[dict(i) for i in items or []], check_in=check_in,
                                            check_out=check_out, name=name, phone=phone,
                                            idempotency_key=idempotency_key))
        if not isinstance(items, list) or not all(isinstance(i, dict) for i in items):
            raise BackofficeError("invalid_items", {"error": "invalid_items"})
        if not (name or "").strip() or not (phone or "").strip():
            raise BackofficeError("missing_fields", {"error": "missing_fields"})
        ids = [str(i.get("apartment_id") or "") for i in items]
        ci, co, gs = self._group_check(ids, check_in, check_out, [i.get("guests") for i in items])
        race = GROUP_RACE_MARK in (name or "")
        for apt_id in ids:
            if (race and apt_id == ids[-1]) or any(ci < t and co > f for f, t in self._blocked_ranges(apt_id)):
                # бойовий: apartment — КОД квартири, не назва; назву гостю дає сайт
                raise BackofficeError("dates_unavailable", {
                    "error": "dates_unavailable", "apartment_id": apt_id,
                    "apartment": self._find(apt_id)["code"]})
        rooms = self._group_rooms(ids, gs, ci, co)
        base = sum(r["base_amount"] for r in rooms)
        tax = sum(r["tourist_tax"] for r in rooms)
        with self._lock:
            booking_id = f"SMOKE{len(self.bookings) + 1:04d}"
            group_items = [{"request_id": f"{booking_id}-{n}", "apartment_id": r["apartment_id"],
                            "apartment": r["apartment"], "base_amount": r["base_amount"],
                            "guests": r["guests"]} for n, r in enumerate(rooms, 1)]
            self.bookings[booking_id] = {
                "id": booking_id, "status": "paid", "apartment": rooms[0]["apartment"],
                "apartment_id": ids[0], "check_in": check_in, "check_out": check_out,
                "nights": (co - ci).days, "total": rooms[0]["total"], "booking_number": booking_id,
                "group": {"primary_id": booking_id, "role": "primary", "items": group_items,
                          "total": base + tax},
            }
        return {"booking_id": booking_id, "payment_url": f"{PAYMENT_URL}?ref={booking_id}",
                "total": base + tax, "nights": (co - ci).days, "base_amount": base,
                "tourist_tax": tax, "items": group_items}

    def get_booking(self, booking_id):
        with self._lock:
            b = self.bookings.get(booking_id)
            return dict(b) if b else None

    # --- для /__fake__/state ----------------------------------------------

    def state(self) -> dict:
        with self._lock:
            return copy.deepcopy({
                "last_booking": self.last_booking, "booking_requests": self.booking_requests,
                "last_quote": self.last_quote,
                "group_requests": self.group_requests, "last_group_quote": self.last_group_quote,
                "last_coupon_phone": self.last_coupon_phone, "bookings": self.bookings,
                "ids": {"apt_a": APT_A, "apt_b": APT_B, "test_apt": TEST_APT},
            })
