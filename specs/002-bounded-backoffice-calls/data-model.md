# Data Model: Бережные обращения сайта к бек-офису

Своей БД у сайта нет; всё ниже живёт в памяти процесса или в окружении.

## Настройки (окружение → `config.Config`, читаются один раз при старте)

| Поле | Окружение | Тип | Не задана / пусто | Неверное | Смысл |
|---|---|---|---|---|---|
| `QUOTE_CACHE_TTL` | `QUOTE_CACHE_TTL` | int ≥ 0, только цифры | 0 (выключено) | 0 + `QUOTE_CACHE_TTL_INVALID=True` | срок записи кеша котировки, с |
| `BACKOFFICE_MAX_CONCURRENT` | `BACKOFFICE_MAX_CONCURRENT` | int ≥ 0, только цифры | 0 (выключено) | 0 + `…_INVALID=True` | N — мест на процесс |
| `BACKOFFICE_SLOT_WAIT` | `BACKOFFICE_SLOT_WAIT` | float ≥ 0, `^[0-9]+(\.[0-9]+)?$` | 2.0 | 2.0 + `…_INVALID=True` | W — ожидание места, с |
| `QUOTE_CACHE_MAX_ENTRIES` | — (константа) | int | 2000 | — | граница записей кеша на процесс |

Неверное = всё, что не подходит под шаблон после `strip()`: `-1`, `+2`, `abc`, `1e3`, `1,5`, для
целых — ещё `1.5`. Значение 0 у `QUOTE_CACHE_TTL` и `BACKOFFICE_MAX_CONCURRENT` = выключено;
`BACKOFFICE_SLOT_WAIT=0` — не ждать вовсе (сразу отказ, если мест нет).

## Запись кеша котировки (`api_client._QuoteCache`)

- **Ключ**: `(apt_id: str, check_in: str, check_out: str, guests: str)` — как уходят в бек-офис.
- **Значение**: глубокая копия успешного ответа бек-офиса (словарь котировки) + `expires_at =
  time.monotonic() + T`.
- **Правила**: кладётся только при 2xx и без `coupon_phone`; не кладётся `None` (400/404), исключения,
  `BackofficeBusy`, `BackofficeUnavailable`. Выдаётся глубокая копия, если `expires_at > now`;
  просроченная удаляется при чтении и не выдаётся никогда. Больше 2000 — вытесняется давняя по
  использованию.
- **Жизненный цикл**: нет → (промах + 2xx) → свежая → (срок) → удалена при чтении / вытеснена.
  Перезапуск процесса — пусто. Сброса после брони нет.

## Места под потолком (`api_client._Slots`)

- **Поля**: `n` (0 = выключено), `wait_s`, семафор `BoundedSemaphore(n)`.
- **Переходы на обращение**: ждёт место ≤ `wait_s` → получил → сетевой вызов → отпустил (всегда, в
  `finally`) | не дождался → `BackofficeBusy`, в бек-офис не пошёл.
- **Кто берёт**: сетевые вызовы `list_apartments`, `get_apartment`, `get_availability`, `get_rates`
  (внутри `fetch`), `get_quote` (промах), `check_coupon`. **Не берут**: попадания любых кешей,
  `create_booking`, `get_booking`.

## Исключения клиента

| Класс | Родитель | Поля | Когда |
|---|---|---|---|
| `BackofficeBusy` | `Exception` | `call: str`, `waited_ms: int` | места не было `wait_s` |
| `BackofficeUnavailable` | `BackofficeError` | `status: int` (429/503), `retry_after: int \| None`, `call: str`; `str(e) == "backoffice_unavailable"` | бек-офис ответил 429/503 |

`retry_after`: целое 1–3600 из заголовка `Retry-After`, иначе `None`.

## Статус кеша котировки на запрос

`threading.local` в `api_client`: `"off"` (кеш выключен, купон, подменный клиент, сброс) / `"hit"` /
`"miss"`. Живёт от `reset_quote_cache_status()` до конца обработки запроса в потоке.

## События журнала

| Событие | Уровень | Где | Поля | Частота |
|---|---|---|---|---|
| `quote_cache_mode` | INFO | `app.py`, импорт | `mode=off` \| `mode=on ttl= max_entries=` | 1 на процесс |
| `backoffice_slots_mode` | INFO | `app.py`, импорт | `mode=off` \| `mode=on n= wait_s=` | 1 на процесс |
| `backoffice_limits_config_invalid` | ERROR | `app.py`, импорт | `env=<имя> fallback=<off\|2>` | по одной на неверную настройку |
| `backoffice_busy` | WARNING | `api_client` | `call= waited_ms= suppressed=` | ≤ 1/с на процесс |
| `backoffice_unavailable` | WARNING | `api_client` | `status= retry_after=<сек\|none> call= suppressed=` | ≤ 1/с на процесс |
| `book_backoffice_unavailable` | WARNING | `app.py`, `/book` | `status= retry_after=` | на бронь |
| `quote` (есть) | INFO | `app.py` | + `cache=hit\|miss\|off` | на котировку |

Ни в одном событии фичи: адрес гостя, телефон (и его хвост), имя, даты, номер брони, тело ответа
бек-офиса, значение неверной настройки.

## Счётчики подмены (`e2e/fake_transport.FakeHTTPSession`, только замер)

`calls` (всего обращений), `inflight` (сейчас), `peak` (максимум `inflight`), под `Lock`; включены
только `count=True`. В `/__fake__/state` → `{"transport": {"pid": …, "calls": …, "peak": …}}`, только
при `SMOKE_FAKE_COUNT=1`.
