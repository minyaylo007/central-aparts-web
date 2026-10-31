"""
Підміна бек-офісу для ЗАМІРУ НАВАНТАЖЕННЯ (deploy/README.md, «Замір із затримкою
бек-офісу»). Смока й CI цим за замовчуванням не користуються — fake_wsgi.py вмикає
його лише змінними оточення:

    SMOKE_FAKE_DELAY_MS=350   кожне звернення до підміни чекає 350 мс (time.sleep —
                              потік gunicorn стоїть, як на справжньому HTTP до бек-офісу).
                              0 / не задано — без затримки (смока, CI).
    SMOKE_FAKE_REAL_CLIENT=1  сайт ходить через СПРАВЖНІЙ api_client.BackofficeClient —
                              з його TTL-кешем (каталог 60 с, квартира 120 с, зайнятість
                              30 с, ціни 900 с, stale при збої), кешем котирування й
                              стелею (спека 002, якщо задані), розбором статусів і
                              JSON, — а підмінено лише HTTP-сесію всередині нього.
    SMOKE_FAKE_COUNT=1        (лише разом із SMOKE_FAKE_REAL_CLIENT=1) сесія рахує
                              звернення й пік одночасних — FakeHTTPSession(count=True),
                              /__fake__/state віддає їх у "transport" (спека 002, FR-021).
                              Рахунок — ПІСЛЯ кешів і стелі api_client: рівно те, що
                              дійшло б до бек-офісу мережею.

Чому підміна на рівні сесії, а не обгортка з «таким самим» кешем: кеш і обробка
404/400/409 — це код api_client.py, і замір має міряти саме його. Копія кешу в e2e/
розійшлася б із ним при першій правці TTL. Сесія — найнижча точка, де ще нема мережі.
"""
import json
import threading
import time
from urllib.parse import urlsplit

import requests

from api_client import BackofficeClient, BackofficeError

# методи інтерфейсу BackofficeClient — те, що в бою йде HTTP-запитом до бек-офісу
BACKOFFICE_CALLS = frozenset({
    "list_apartments", "get_apartment", "get_availability", "get_rates", "get_quote",
    "check_coupon", "create_booking", "get_booking",
    "get_group_quote", "create_group_booking",          # спека 108
})

# спека 108: код помилки групи → статус бойового бек-офісу
_GROUP_STATUS = {"apartment_not_found": 404, "apartment_config_missing": 500,
                 "dates_unavailable": 409, "too_many_holds": 409,
                 "payment_unavailable": 502}

_PREFIX = "/api/public/"
_loads = json.loads   # у post() імʼя json зайняте параметром, як у requests


class DelayedBackoffice:
    """Обгортка підміни: перед кожним зверненням до бек-офісу — sleep(delay_ms)."""

    def __init__(self, inner, delay_ms: int):
        self._inner = inner
        self._delay_s = delay_ms / 1000.0

    def __getattr__(self, name):
        attr = getattr(self._inner, name)
        if name not in BACKOFFICE_CALLS:
            return attr

        def delayed(*args, **kwargs):
            time.sleep(self._delay_s)
            return attr(*args, **kwargs)
        return delayed


class _FakeResponse:
    """Те, що BackofficeClient читає з requests.Response: status_code, headers,
    json(), raise_for_status(). Тіло проходить через JSON, як по мережі."""

    request = None  # requests.HTTPError(response=...) читає response.request

    def __init__(self, status_code: int, payload):
        self.status_code = status_code
        self._body = json.dumps(payload)
        self.headers: dict = {}  # клієнт читає Retry-After на 429/503; підміна його не шле

    def json(self):
        return json.loads(self._body)

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} fake backoffice", response=self)


def _ok(payload, missing_status=404):
    if payload is None:
        return _FakeResponse(missing_status, {"error": "not_found" if missing_status == 404 else "invalid"})
    return _FakeResponse(200, payload)


class FakeHTTPSession:
    """Замість requests.Session у BackofficeClient: URL /api/public/* → методи підміни,
    результат → статус і JSON так, як їх віддає бойовий бек-офіс.
    count=True — лічильники для заміру: calls (усього звернень) і peak (найбільше
    одночасних) навколо виклику підміни (затримка DelayedBackoffice — усередині)."""

    def __init__(self, backend, count: bool = False):
        self.backend = backend
        self.headers: dict = {}
        self.count = count
        self._lock = threading.Lock()
        self._calls = self._inflight = self._peak = 0

    def counters(self) -> dict:
        with self._lock:
            return {"calls": self._calls, "peak": self._peak}

    def get(self, url, params=None, headers=None, timeout=None):
        return self._dispatch("GET", url, params or {}, headers or {}, None)

    def post(self, url, json=None, data=None, headers=None, timeout=None):
        if data is not None:              # create_booking шле готові байти JSON
            json = _loads(data)
        return self._dispatch("POST", url, {}, headers or {}, json or {})

    def _dispatch(self, method, url, params, headers, body):
        if not self.count:
            return self._route(method, url, params, headers, body)
        with self._lock:
            self._calls += 1
            self._inflight += 1
            self._peak = max(self._peak, self._inflight)
        try:
            return self._route(method, url, params, headers, body)
        finally:
            with self._lock:
                self._inflight -= 1

    def _route(self, method, url, params, headers, body):
        # BACKOFFICE_URL може бути з префіксом шляху — маршрут після /api/public/
        path = urlsplit(url).path
        if _PREFIX not in path:
            return _FakeResponse(404, {"error": "not_found"})
        parts = path.split(_PREFIX, 1)[1].strip("/").split("/")
        b = self.backend
        ip = headers.get("X-Forwarded-For", "")
        lang = params.get("lang", "ua")
        if method == "GET":
            if parts == ["quote-group"]:
                try:
                    return _ok(b.get_group_quote(
                        [i for i in (params.get("apartment_ids") or "").split(",") if i],
                        params.get("check_in"), params.get("check_out"),
                        [g for g in (params.get("guests") or "").split(",") if g], lang=lang))
                except BackofficeError as e:
                    return _FakeResponse(_GROUP_STATUS.get(str(e), 400), e.data or {"error": str(e)})
            if parts == ["apartments"]:
                return _ok(b.list_apartments(lang=lang))
            if len(parts) == 2:
                kind, apt_id = parts
                if kind == "apartments":
                    return _ok(b.get_apartment(apt_id, lang=lang))
                if kind == "availability":
                    return _ok(b.get_availability(apt_id))
                if kind == "rates":
                    return _ok(b.get_rates(apt_id))
                if kind == "quote":
                    return _ok(b.get_quote(apt_id, params.get("check_in"), params.get("check_out"),
                                           params.get("guests", 1), params.get("coupon_phone", ""),
                                           client_ip=ip), missing_status=400)
                if kind == "booking":
                    return _ok(b.get_booking(apt_id))
        if method == "POST":
            if parts == ["coupon", "check"]:
                return _ok(b.check_coupon(body.get("phone", ""), client_ip=ip))
            if parts == ["book"]:
                try:
                    return _ok(b.create_booking(**body,
                                                idempotency_key=headers.get("Idempotency-Key", "")))
                except BackofficeError as e:
                    code = 400 if str(e) == "bad_request" else 409
                    return _FakeResponse(code, {"error": str(e)})
            if parts == ["book-group"]:
                try:
                    return _FakeResponse(201, b.create_group_booking(
                        body.get("items"), body.get("check_in"), body.get("check_out"),
                        body.get("name", ""), body.get("phone", ""),
                        idempotency_key=headers.get("Idempotency-Key", "")))
                except BackofficeError as e:
                    return _FakeResponse(_GROUP_STATUS.get(str(e), 400), e.data or {"error": str(e)})
        return _FakeResponse(404, {"error": "not_found"})


def real_client_over(backend, count: bool = False) -> BackofficeClient:
    """Справжній BackofficeClient (кеш, стеля, статуси), у якого мережу замінено
    підміною; count=True — сесія з лічильниками (client._session.counters())."""
    client = BackofficeClient()
    client._session = FakeHTTPSession(backend, count=count)
    return client
