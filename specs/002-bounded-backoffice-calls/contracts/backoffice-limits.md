# Контракт: кеш котировки, потолок обращений, «бек-офис временно недоступен»

Нормативный документ для исполнителя и тестов. Ссылки: spec.md (FR-…), data-model.md, research.md (R…).

## §1. Настройки окружения

| Имя | Допустимо | Не задана / пусто | Неверное | 0 |
|---|---|---|---|---|
| `QUOTE_CACHE_TTL` | `^[0-9]+$` (с) | кеш выключен | кеш выключен + ERROR | кеш выключен |
| `BACKOFFICE_MAX_CONCURRENT` | `^[0-9]+$` | потолка нет | потолка нет + ERROR | потолка нет |
| `BACKOFFICE_SLOT_WAIT` | `^[0-9]+(\.[0-9]+)?$` (с) | 2 | 2 + ERROR | без ожидания |

Читаются один раз при старте процесса (`config.Config`), после `strip()`. Не зависят от
`BACKOFFICE_URL` (FR-019). Рекомендуемые при включении: `QUOTE_CACHE_TTL=30`,
`BACKOFFICE_SLOT_WAIT=2`; `BACKOFFICE_MAX_CONCURRENT` — только когда процессы × потоки > 50 (бюджет
бек-офиса на сайт, research.md R12), тогда `⌊50 / число процессов⌋`. При `-w 3 --threads 12` потолок
не задаётся (36 ≤ 50).

## §2. Ответы гостю

| Путь | Потолок занят W с (`BackofficeBusy`) | Бек-офис 429/503 (`BackofficeUnavailable`) | Прочий сбой (как сейчас) |
|---|---|---|---|
| `GET /api/quote/<apt>` | 503 `{"error":"busy"}`, `Retry-After: 5` | 503 `{"error":"busy"}`, `Retry-After: <ra или 5>` | 503 `{"error":"unavailable"}` |
| `GET /api/availability/<apt>` | то же (промах кеша, нет устаревшего) | то же | 503 `unavailable` |
| `GET /api/rates/<apt>` | то же | то же | 503 `unavailable` |
| `POST /api/coupon/check` | то же | то же | 503 `unavailable` |
| `GET /` | устаревший каталог; без него — пустой список (как сейчас) | то же | то же |
| `GET /apartment/<apt>` | устаревшая квартира/занятость; без квартиры — 500 `public_error.html` (как сейчас) | то же | то же |
| `POST /book/<apt>` | не применяется (мимо потолка) | 302 на `/apartment/<apt>?guests=&book=1&check_in=&check_out=` + flash §3 | как сейчас (`error_booking` и т. д.) |
| `GET /booking-confirmed` | не применяется (мимо потолка) | как сейчас при сбое (без данных брони) | как сейчас |

`<ra>` — `Retry-After` бек-офиса, если целое 1–3600, иначе 5. Попадание в любой кеш — обычный 200
без ожидания. Котировка с `coupon_phone` — всегда в бек-офис (при включённом потолке — под ним).

## §3. Flash брони при «временно недоступен» (`translations.py`)

| Ключ | ua | en |
|---|---|---|
| `booking_temporarily_unavailable_in` | `Бронювання тимчасово недоступне — спробуйте за {n} {unit}.` | `Booking is temporarily unavailable — please try again in {n} {unit}.` |
| `booking_temporarily_unavailable` | `Бронювання тимчасово недоступне — спробуйте трохи пізніше.` | `Booking is temporarily unavailable — please try again a little later.` |
| `minute_one` | `хвилину` | `minute` |
| `minute_few` | `хвилини` | `minutes` |
| `minute_many` | `хвилин` | `minutes` |

`n = ceil(retry_after / 60)` при `retry_after` 1–3600, иначе — ключ без числа. Примеры: 300 → «за 5
хвилин»; 60 → «за 1 хвилину»; 61 → «за 2 хвилини»; 1260 → «за 21 хвилину»; 720 → «за 12 хвилин»;
нет заголовка → «трохи пізніше». Категория flash — `error`, как у прочих ошибок брони. Бронь сайт не
повторяет.

## §4. Строки журнала

```text
INFO     quote_cache_mode mode=off
INFO     quote_cache_mode mode=on ttl=30 max_entries=2000
INFO     backoffice_slots_mode mode=off
INFO     backoffice_slots_mode mode=on n=3 wait_s=2.0
ERROR    backoffice_limits_config_invalid env=BACKOFFICE_MAX_CONCURRENT fallback=off
ERROR    backoffice_limits_config_invalid env=BACKOFFICE_SLOT_WAIT fallback=2
WARNING  backoffice_busy call=quote waited_ms=2001 suppressed=0
WARNING  backoffice_unavailable status=503 retry_after=300 call=apartments suppressed=12
WARNING  backoffice_unavailable status=429 retry_after=none call=coupon_check suppressed=0
WARNING  book_backoffice_unavailable status=503 retry_after=300
INFO     quote apt=… ci=… co=… g=… coupon=False applied=None discount=None booking_total=… ms=… cache=hit
```

Правила: `backoffice_busy` и `backoffice_unavailable` — не чаще одной строки в секунду на событие в
процессе; `suppressed` — сколько событий пропущено с прошлой строки. Без `exc_info`. В строках фичи
нет адреса гостя, телефона и его хвоста, имени, дат, номера брони, тела ответа бек-офиса, значения
неверной настройки. Строка `quote` — существующая, меняется только хвост `cache=`.

## §5. Интерфейс `api_client` (для `app.py` и тестов)

```text
class BackofficeBusy(Exception):               .call: str, .waited_ms: int
class BackofficeUnavailable(BackofficeError):  .status: int, .retry_after: int | None, .call: str

set_slots(n: int, wait_s: float) -> None            # тесты; n=0 — выключено
set_quote_cache(ttl: int, max_entries: int = 2000)  # тесты; ttl=0 — выключено, очищает кеш
quote_cache_status() -> str                         # "off" | "hit" | "miss" — для текущего потока
reset_quote_cache_status() -> None                  # ставит "off"
```

Сигнатуры методов `BackofficeClient` не меняются (подмены `tests/conftest.py` и
`e2e/fake_backoffice.py` продолжают работать без правок).

## §6. Счётчики подмены для замера (`e2e/`)

- `real_client_over(backend, count: bool = False)`; при `count=True` у `FakeHTTPSession` —
  `counters() -> {"calls": int, "peak": int}`.
- `_FakeResponse` получает `headers: dict` (пустой), чтобы клиент мог читать `Retry-After`.
- `fake_wsgi.py`: `SMOKE_FAKE_COUNT=1` (вместе с `SMOKE_FAKE_REAL_CLIENT=1`) → в ответ
  `/__fake__/state` добавляется `"transport": {"pid": <int>, "calls": <int>, "peak": <int>}`. Без
  переменной — ответ прежний, байт-в-байт.
