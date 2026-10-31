"""
HTTP client for the backoffice public API.
Wraps all calls to cv.central-aparts.store/api/public/*.

Spec 002 (all off by default — behaviour as before):
  - quote cache: only when QUOTE_CACHE_TTL > 0, only quotes WITHOUT coupon_phone,
    only 2xx answers, never stale, at most QUOTE_CACHE_MAX_ENTRIES per process;
  - ceiling: when BACKOFFICE_MAX_CONCURRENT > 0, at most N network calls of this
    process at once (booking calls bypass it); a call waits BACKOFFICE_SLOT_WAIT s.
"""
import contextlib
import copy
import json
import logging
import threading
import time
import uuid
from collections import OrderedDict
from typing import Optional

import requests

from config import Config

logger = logging.getLogger(__name__)


class BackofficeError(Exception):
    """str(e) — код помилки бек-офісу ("dates_unavailable", …); data — розібране
    JSON-тіло відповіді, якщо воно було (група: apartment_id занятої/невідомої
    кімнати). Текст гостю app.py бере з translations за кодом, не з data."""

    def __init__(self, error: str = "", data: dict | None = None):
        super().__init__(error)
        self.data = data or {}


# ---------------------------------------------------------------------------
# «Temporarily unavailable» from the backoffice (spec 002): 429/503 on ANY call,
# recognised by the code and Retry-After only — the body (HTML maintenance page
# now, JSON later) is never read. Always on. The only retry: a public read under the
# backoffice ceiling, 429 with Retry-After <= READ_RETRY_MAX_S — once (_get_read).
# ---------------------------------------------------------------------------

def _parse_retry_after(value: str | None) -> int | None:
    """Retry-After as whole seconds 1–3600; an HTTP date, 0, negative, fraction
    or more than an hour → None."""
    value = (value or "").strip()
    if value.isascii() and value.isdigit() and 1 <= int(value) <= 3600:
        return int(value)
    return None


class BackofficeUnavailable(BackofficeError):
    def __init__(self, status: int, retry_after: int | None, call: str):
        super().__init__("backoffice_unavailable")
        self.status = status
        self.retry_after = retry_after
        self.call = call


class BackofficeAuthFailed(Exception):
    """401/403 на /book і /book-group: бек-офіс не прийняв ключ сайту — поломка
    нашої конфігурації, не гостя. Навмисно НЕ BackofficeError: маршрут без своєї
    гілки впаде в загальний except з ERROR, а не в тихішу гілку кодів гостя."""

    def __init__(self, status: int, call: str):
        super().__init__("backoffice_auth_failed")
        self.status = status
        self.call = call


def _raise_if_unavailable(r, call: str) -> None:
    """First line after every network call, before any status/body handling.
    429 while _cached() holds a young stale value for this call (see
    _STALE_QUIET_S) is INFO: the guest gets that value, nothing is wrong."""
    if r.status_code in (429, 503):
        retry_after = _parse_retry_after(r.headers.get("Retry-After"))
        quiet = r.status_code == 429 and getattr(_stale_fallback, "quiet", False)
        _unavailable_log.warn("status=%d retry_after=%s call=%s", r.status_code,
                              "none" if retry_after is None else retry_after, call,
                              key="quiet" if quiet else None,
                              level=logging.INFO if quiet else logging.WARNING)
        raise BackofficeUnavailable(r.status_code, retry_after, call)


# ---------------------------------------------------------------------------
# Ceiling of simultaneous backoffice calls per process (spec 002). n == 0 — off.
# ---------------------------------------------------------------------------

class BackofficeBusy(Exception):
    """No free slot under the ceiling for wait_s — the call never went out."""

    def __init__(self, call: str, waited_ms: int):
        super().__init__("backoffice_busy")
        self.call = call
        self.waited_ms = waited_ms


class _Slots:
    def __init__(self, n: int, wait_s: float):
        self.n = n
        self.wait_s = wait_s
        self._sem = threading.BoundedSemaphore(n) if n > 0 else None

    def slot(self, call: str):
        """Context manager around ONE network call; `call` names its kind
        (quote, apartments, …) for the log — never its parameters."""
        if self._sem is None:
            return contextlib.nullcontext()
        return self._held(call)

    @contextlib.contextmanager
    def _held(self, call: str):
        t0 = time.perf_counter()
        if not self._sem.acquire(timeout=self.wait_s):
            waited_ms = int((time.perf_counter() - t0) * 1000)
            _busy_log.warn("call=%s waited_ms=%d", call, waited_ms)
            raise BackofficeBusy(call, waited_ms)
        try:
            yield
        finally:
            self._sem.release()


_slots = _Slots(Config.BACKOFFICE_MAX_CONCURRENT, Config.BACKOFFICE_SLOT_WAIT)


def set_slots(n: int, wait_s: float) -> None:
    """Replace the ceiling (tests; like set_client). n=0 — off."""
    global _slots
    _slots = _Slots(n, wait_s)


# ---------------------------------------------------------------------------
# Quote cache (spec 002): its own bounded store, NOT _ttl_cache — a quote must
# never be served stale, and its keys come from guests (dates × guests).
# ---------------------------------------------------------------------------

class _QuoteCache:
    """key -> (expires_at by time.monotonic, value). Deep copies in and out, so
    no caller shares a dict with another thread; an expired entry is dropped on
    read and never returned; over max_entries the least recently used goes."""

    def __init__(self, ttl: int, max_entries: int):
        self.ttl = ttl
        self.max_entries = max_entries
        self.enabled = ttl > 0
        self._entries: OrderedDict = OrderedDict()
        self._lock = threading.Lock()

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)

    def get(self, key):
        now = time.monotonic()
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return None
            if entry[0] <= now:
                del self._entries[key]
                return None
            self._entries.move_to_end(key)
            value = entry[1]
        return copy.deepcopy(value)

    def put(self, key, value) -> None:
        entry = (time.monotonic() + self.ttl, copy.deepcopy(value))
        with self._lock:
            self._entries[key] = entry
            self._entries.move_to_end(key)
            while len(self._entries) > self.max_entries:
                self._entries.popitem(last=False)


_quote_cache = _QuoteCache(Config.QUOTE_CACHE_TTL, Config.QUOTE_CACHE_MAX_ENTRIES)


def set_quote_cache(ttl: int, max_entries: int = 2000) -> None:
    """New empty quote cache (tests). ttl=0 — off."""
    global _quote_cache
    _quote_cache = _QuoteCache(ttl, max_entries)


# "off" | "hit" | "miss" of the last get_quote in this thread — for the `quote`
# log line in app.py (the mock clients do not know it, so it stays "off")
_quote_status = threading.local()


def quote_cache_status() -> str:
    return getattr(_quote_status, "value", "off")


def reset_quote_cache_status() -> None:
    _quote_status.value = "off"


# ---------------------------------------------------------------------------
# In-process TTL cache (per Lambda container). Read-only catalog data barely
# changes, and skipping a backoffice round trip per page render is the single
# biggest speed win. On upstream failure the stale value is served instead of
# an error page. Booking calls are NEVER cached; quotes — only in _QuoteCache.
# ---------------------------------------------------------------------------

_ttl_cache: dict = {}  # key -> (expires_at, value)


class _RateLimitedWarning:
    """WARNING не частіше одного рядка на `interval` с на процес і ключ; наступний
    рядок несе suppressed=<скільки пропущено з попереднього>. Для подій, що при
    збої бек-офісу йдуть на кожен запит: backoffice_stale_served (тут), у спеці
    002 — backoffice_busy і backoffice_unavailable (1 рядок/с, без ключа).
    key — з малого набору (вид звернення), не id і не дані гостя: кошик живе
    весь процес."""

    def __init__(self, event: str, interval: float = 1.0):
        self.event = event
        self.interval = interval
        self._lock = threading.Lock()
        self._last: dict = {}        # key -> time.monotonic() останнього рядка
        self._suppressed: dict = {}  # key -> пропущено відтоді

    def warn(self, fmt: str, *args, key=None, level: int = logging.WARNING) -> None:
        now = time.monotonic()
        with self._lock:
            last = self._last.get(key)
            if last is not None and now - last < self.interval:
                self._suppressed[key] = self._suppressed.get(key, 0) + 1
                return
            self._last[key] = now
            suppressed = self._suppressed.pop(key, 0)
        logger.log(level, f"{self.event} {fmt} suppressed=%d", *args, suppressed)


# stale у вікні техробіт бек-офісу — на кожен перегляд сторінки після TTL; рядка
# на вид звернення раз на 10 с на процес досить, масштаб — у suppressed=
_STALE_LOG_INTERVAL = 10.0
_stale_log = _RateLimitedWarning("backoffice_stale_served", _STALE_LOG_INTERVAL)
# спека 002: відмова стелі — 1 рядок/с на процес, масштаб — у suppressed=
_busy_log = _RateLimitedWarning("backoffice_busy")
# 429/503 бек-офісу — теж 1 рядок/с на процес, окремо від backoffice_busy
_unavailable_log = _RateLimitedWarning("backoffice_unavailable")
# повтор читання на 429 стелі (_get_read) — INFO, 1 рядок/с на процес і результат
_retry_log = _RateLimitedWarning("backoffice_retry")


# 23.10.2026, 429 на каталозі (22.10 14:07, 23.10 10:56). Бек-офіс тримає СТЕЛЮ одночасних
# публічних читань — 2 на свій процес (aparts backend/routes/public_api.py,
# _PUBLIC_READ_MAX_INFLIGHT; понад неї одразу 429 Retry-After: 1), не ліміт на ключ чи IP
# і не вікно. Його журнал 22.10 14:07:37: за одну секунду 4 × /apartments, 2 × /apartments/<id>
# і 2 × /availability — усі від нас: кілька потоків одного воркера (gthread, 3 × 12) разом
# промахнулись повз прострочений запис і пішли по той самий каталог. Тому:
#   - один запит на ключ і процес (single-flight): поки ведучий потік оновлює запис, інші
#     віддають старе значення, а без нього — чекають його відповіді до _FOLLOWER_WAIT_S;
#   - 429 стелі, коли є свіже-старе значення (молодше _STALE_QUIET_S), — INFO: гість бачить
#     каталог хвилинної давнини, це задумана поведінка. Старше — знову WARNING: бек-офіс
#     відмовляє довго, і сторож журналу має це побачити. 503 (техроботи) — WARNING завжди.
_STALE_QUIET_S = 600.0
_FOLLOWER_WAIT_S = 10.0
_inflight: dict = {}          # key -> threading.Event ведучого потоку
_inflight_lock = threading.Lock()
_stale_fallback = threading.local()   # .quiet — читає _raise_if_unavailable


def _cached(key, ttl: float, fetch):
    now = time.time()
    hit = _ttl_cache.get(key)
    if hit and hit[0] > now:
        return hit[1]
    with _inflight_lock:
        event = _inflight.get(key)
        leader = event is None
        if leader:
            event = _inflight[key] = threading.Event()
    if not leader:
        if hit:                       # ведучий уже оновлює — другого запиту не треба
            return hit[1]
        event.wait(_FOLLOWER_WAIT_S)
        fresh = _ttl_cache.get(key)
        if fresh and fresh[0] > now:
            return fresh[1]
        return _fetch_and_store(key, ttl, fetch, hit)   # ведучий не зміг — своя спроба
    try:
        return _fetch_and_store(key, ttl, fetch, hit)
    finally:
        with _inflight_lock:
            _inflight.pop(key, None)
        event.set()


def _fetch_and_store(key, ttl: float, fetch, hit):
    quiet = hit is not None and time.time() - (hit[0] - ttl) < _STALE_QUIET_S
    _stale_fallback.quiet = quiet
    try:
        value = fetch()
    except Exception as e:
        if hit:  # serve stale on upstream failure rather than erroring the page
            calm = quiet and isinstance(e, BackofficeUnavailable) and e.status == 429
            level = logging.INFO if calm else logging.WARNING
            _stale_log.warn("key=%s", key, key=(key[0], level), level=level)
            return hit[1]
        raise
    finally:
        _stale_fallback.quiet = False
    _ttl_cache[key] = (time.time() + ttl, value)
    return value


def _json_or_empty(r) -> dict:
    try:
        data = r.json()
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


# /book і /book-group: повтори на 409 idempotency_in_progress і 503 (спека 107 бек-офісу). Запит
# живе всередині воркера gunicorn — чекати довше цього не можна; _sleep — для тестів.
BOOK_RETRY_MAX = 2
BOOK_RETRY_BUDGET_S = 10
_sleep = time.sleep

# 27.10.2026, 429 на холодному кеші (13.10, 22.10, 23.10, 27.10 — щоразу одна відмова).
# Стеля бек-офісу (див. коментар над _STALE_QUIET_S) рахує ВСІ публічні читання процесу, а
# не ключ: 27.10 17:02 сайт послав шість різних (каталог ×2, квартира A і B, доступність A
# і B) — single-flight тут безсилий, 429 Retry-After: 1 отримало зайве. Тому публічне
# GET-читання під стелею (apartments, apartment, availability, quote, rates, quote-group —
# _public_read_limited у aparts backend/routes/public_api.py) на 429 з Retry-After не більше
# READ_RETRY_MAX_S повторюється ОДИН раз після паузи. Пауза — поза слотом нашої стелі
# (_slots), щоб сплячий потік не тримав місце. 503 (техроботи) і довший Retry-After не
# повторюються: чекати їх у воркері нема сенсу. /coupon/check (POST, не під стелею),
# /booking/<id> (не під стелею), /book і /book-group (_post_idempotent) — без змін.
# Журнал: перший 429 не пишеться; повтор — INFO backoffice_retry call=… ok=1|0 status=…;
# WARNING backoffice_unavailable — лише якщо і повтор 429/503 (через _raise_if_unavailable).
READ_RETRY_MAX_S = 2


class BackofficeClient:
    def __init__(self):
        self.base = Config.BACKOFFICE_URL.rstrip("/")
        self.key  = Config.PUBLIC_API_KEY
        self._session = requests.Session()
        self._session.headers.update({
            "X-Public-API-Key": self.key,
            "Content-Type": "application/json",
        })

    def _get_read(self, call: str, path: str, params=None, headers=None):
        """GET публічного читання під стелею бек-офісу з одним повтором на 429 (див.
        READ_RETRY_MAX_S). Повертає відповідь після _raise_if_unavailable; call — вид
        звернення для журналу, ніколи не його параметри."""
        url = f"{self.base}{path}"
        with _slots.slot(call):
            r = self._session.get(url, params=params, headers=headers, timeout=10)
        if r.status_code == 429:
            retry_after = _parse_retry_after(r.headers.get("Retry-After"))
            if retry_after is not None and retry_after <= READ_RETRY_MAX_S:
                _sleep(retry_after)                   # поза слотом стелі
                with _slots.slot(call):
                    r = self._session.get(url, params=params, headers=headers, timeout=10)
                ok = r.status_code not in (429, 503)
                _retry_log.warn("call=%s ok=%d status=%d", call, ok, r.status_code,
                                key=ok, level=logging.INFO)
        _raise_if_unavailable(r, call)
        return r

    def list_apartments(self, lang: str = "ua") -> list:
        def fetch():
            r = self._get_read("apartments", "/api/public/apartments", params={"lang": lang})
            r.raise_for_status()
            return r.json()
        return _cached(("apartments", lang), 60, fetch)

    def get_apartment(self, apt_id: str, lang: str = "ua") -> Optional[dict]:
        def fetch():
            r = self._get_read("apartment", f"/api/public/apartments/{apt_id}",
                               params={"lang": lang})
            if r.status_code == 404:
                return None
            r.raise_for_status()
            return r.json()
        return _cached(("apartment", apt_id, lang), 120, fetch)

    def get_availability(self, apt_id: str) -> list:
        def fetch():
            r = self._get_read("availability", f"/api/public/availability/{apt_id}")
            r.raise_for_status()
            return r.json()
        # short TTL: availability must feel live; /book re-validates anyway (409)
        return _cached(("availability", apt_id), 30, fetch)

    def get_quote(self, apt_id: str, check_in: str, check_out: str,
                  guests: int = 1, coupon_phone: str = "",
                  client_ip: str = "") -> Optional[dict]:
        """Server-truth stay price (same math as /book — preview == invoice).
        coupon_phone (optional): returning-guest coupon phone — the backoffice
        adds a coupon_discount line when valid + above the min-total guard.
        client_ip: the real visitor IP, forwarded as X-Forwarded-For so the
        backoffice coupon rate limiter buckets per guest, not per our Lambda.
        Returns None on invalid input (4xx) so callers can fall back.
        Spec 002: with QUOTE_CACHE_TTL and no coupon_phone, a fresh 2xx answer for
        the same (apt, dates, guests) comes from _QuoteCache — the answer does not
        depend on the guest or the language (research.md R2)."""
        cache = _quote_cache
        key = (apt_id, check_in, check_out, str(guests))
        use_cache = cache.enabled and not coupon_phone
        _quote_status.value = "off"
        if use_cache:
            hit = cache.get(key)
            if hit is not None:
                _quote_status.value = "hit"
                return hit
        params = {"check_in": check_in, "check_out": check_out, "guests": guests}
        headers = {}
        if coupon_phone:
            params["coupon_phone"] = coupon_phone
        if client_ip:
            headers["X-Forwarded-For"] = client_ip
        r = self._get_read("quote", f"/api/public/quote/{apt_id}", params=params,
                           headers=headers)
        if r.status_code in (400, 404):
            return None
        r.raise_for_status()
        value = r.json()
        if use_cache:
            cache.put(key, value)
            _quote_status.value = "miss"
        return value

    def check_coupon(self, phone: str, client_ip: str = "") -> dict:
        """Returning-guest coupon lookup. Backoffice answers a generic
        {"eligible": false} for anything but a valid coupon (no probing).
        client_ip is forwarded as X-Forwarded-For (per-guest rate buckets).
        NEVER cached — validity flips on redemption."""
        headers = {}
        if client_ip:
            headers["X-Forwarded-For"] = client_ip
        with _slots.slot("coupon_check"):
            r = self._session.post(
                f"{self.base}/api/public/coupon/check",
                json={"phone": phone},
                headers=headers,
                timeout=10,
            )
            _raise_if_unavailable(r, "coupon_check")
        r.raise_for_status()
        return r.json()

    def get_rates(self, apt_id: str) -> Optional[dict]:
        """Nightly site prices for the next ~60 days: {"rates": {date: price}}.
        Feeds the calendar day-price hints only — never used for charging
        (that is /quote + /book). Returns None on 404 (unknown/non-public)."""
        def fetch():
            r = self._get_read("rates", f"/api/public/rates/{apt_id}")
            if r.status_code == 404:
                return None
            r.raise_for_status()
            return r.json()
        # 15 min TTL mirrors the backoffice Cache-Control (max-age=900)
        return _cached(("rates", apt_id), 900, fetch)

    def create_booking(self, apartment_id: str, check_in: str, check_out: str,
                       guests: int, name: str, phone: str,
                       coupon_phone: str = "",
                       coupon_expected: str = "",
                       idempotency_key: str = "") -> dict:
        """Спека 107 бек-офісу: кожен POST /book несе Idempotency-Key — один на
        спробу бронювання (app.py бере його з форми); порожній → свій uuid4.
        Тіло серіалізується ОДИН раз, тож повтор везе побайтно те саме, що й перша
        спроба. 409 idempotency_in_progress і 503 — повтор з тим самим ключем через
        Retry-After, не більше BOOK_RETRY_MAX разів і BOOK_RETRY_BUDGET_S секунд
        очікування сумарно; не влазить у бюджет → BackofficeUnavailable, як раніше."""
        key = idempotency_key or str(uuid.uuid4())
        payload = {
            "apartment_id": apartment_id,
            "check_in": check_in,
            "check_out": check_out,
            "guests": guests,
            "name": name,
            "phone": phone,
        }
        if coupon_phone:
            # re-validated server-side; -500 applied there when eligible
            payload["coupon_phone"] = coupon_phone
            if coupon_expected:
                # the widget SHOWED the -500 — backoffice fails closed (409
                # coupon_unavailable) instead of silently charging full price
                payload["coupon_expected"] = "1"
        return self._post_idempotent("/api/public/book", payload, key, "book")

    def _post_idempotent(self, path: str, payload: dict, key: str, call: str,
                         rooms: int | None = None) -> dict:
        """Спільний цикл /book і /book-group (спека 107): тіло серіалізується ОДИН
        раз, повтор везе побайтно те саме з тим самим ключем; 409
        idempotency_in_progress і 503 — повтор через Retry-After, не більше
        BOOK_RETRY_MAX разів і BOOK_RETRY_BUDGET_S секунд очікування сумарно; не
        влазить у бюджет → BackofficeUnavailable. idempotency_key_mismatch не
        повторюється. Одна INFO-строка на спробу: backoffice_<call> key=<8>
        attempt=N status=S (+ rooms=N у групи) — без тіла, телефону, дат і URL."""
        body = json.dumps(payload, allow_nan=False).encode("utf-8")   # як json= у requests
        event = f"backoffice_{call}"
        tail = "" if rooms is None else f" rooms={rooms}"
        waited = 0
        attempt = 0
        while True:
            attempt += 1
            try:
                r = self._session.post(
                    f"{self.base}{path}",
                    data=body,
                    headers={"Idempotency-Key": key},
                    timeout=30,
                )
            except Exception as e:
                logger.info("%s key=%s attempt=%d status=%s%s", event, key[:8], attempt,
                            type(e).__name__, tail)
                raise
            logger.info("%s key=%s attempt=%d status=%d%s", event, key[:8], attempt,
                        r.status_code, tail)
            error = ""
            if r.status_code == 409:
                # distinguish coupon_unavailable / idempotency_* from dates_unavailable
                try:
                    error = r.json().get("error") or "dates_unavailable"
                except (ValueError, AttributeError):
                    error = "dates_unavailable"
            in_progress = error == "idempotency_in_progress"
            if in_progress or r.status_code == 503:
                retry_after = _parse_retry_after(r.headers.get("Retry-After"))
                if (attempt <= BOOK_RETRY_MAX and retry_after is not None
                        and waited + retry_after <= BOOK_RETRY_BUDGET_S):
                    _sleep(retry_after)
                    waited += retry_after
                    continue
                if in_progress:
                    # перша спроба з цим ключем ще йде — гостю «тимчасово недоступно»
                    raise BackofficeUnavailable(409, retry_after, call)
            break
        _raise_if_unavailable(r, call)            # до розбору 409/400/502: тіло може бути HTML
        if r.status_code in (401, 403):
            raise BackofficeAuthFailed(r.status_code, call)
        if r.status_code == 409:
            if error == "idempotency_key_mismatch":
                # ключ уже ходив з іншим тілом — помилка сайту, не гостя; не повторюємо
                logger.warning("%s_key_mismatch key=%s", event, key[:8])
            raise BackofficeError(error, _json_or_empty(r))
        if r.status_code == 400:
            data = _json_or_empty(r)
            raise BackofficeError(data.get("error") or "bad_request", data)
        if r.status_code == 502:
            raise BackofficeError("payment_unavailable")
        if rooms is not None and r.status_code in (404, 500):
            # група: 404 apartment_not_found + apartment_id, 500 apartment_config_missing.
            # Одиночна /book лишається як була — HTTPError
            data = _json_or_empty(r)
            raise BackofficeError(data.get("error") or f"http_{r.status_code}", data)
        r.raise_for_status()
        return r.json()

    def get_group_quote(self, apartment_ids: list, check_in: str, check_out: str,
                        guests: list, lang: str = "ua") -> dict:
        """Спека 108: смета кількох кімнат на спільні дати. Тіло бек-офісу як є —
        суми, строки rooms[] і unavailable[] рахує ТІЛЬКИ сервер. 400/404/500 →
        BackofficeError(error, data) (data.apartment_id у 404). Без кешу: смета
        групи рідкісна, а зайнятість має бути свіжою."""
        params = {
            "apartment_ids": ",".join(apartment_ids),
            "check_in": check_in,
            "check_out": check_out,
            "guests": ",".join(str(int(g)) for g in guests),
            "lang": lang,
        }
        r = self._get_read("quote_group", "/api/public/quote-group", params=params)
        if r.status_code in (400, 404, 500):
            data = _json_or_empty(r)
            raise BackofficeError(data.get("error") or f"http_{r.status_code}", data)
        r.raise_for_status()
        return r.json()

    def create_group_booking(self, items: list, check_in: str, check_out: str,
                             name: str, phone: str, idempotency_key: str = "") -> dict:
        """Спека 108: одна оплата за 2–6 кімнат. items — [{"apartment_id", "guests"}],
        перша — головна заявка. Той самий цикл повторів і той самий ключ, що в /book."""
        key = idempotency_key or str(uuid.uuid4())
        payload = {
            "items": [{"apartment_id": i["apartment_id"], "guests": int(i["guests"])}
                      for i in items],
            "check_in": check_in,
            "check_out": check_out,
            "name": name,
            "phone": phone,
        }
        return self._post_idempotent("/api/public/book-group", payload, key, "book_group",
                                     rooms=len(items))

    def get_booking(self, booking_id: str) -> Optional[dict]:
        r = self._session.get(f"{self.base}/api/public/booking/{booking_id}",
                              timeout=10)
        _raise_if_unavailable(r, "booking")
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return r.json()


_client: Optional[BackofficeClient] = None


def get_client() -> BackofficeClient:
    global _client
    if _client is None:
        _client = BackofficeClient()
    return _client


def set_client(c) -> None:
    """Inject mock client for tests."""
    global _client
    _client = c
