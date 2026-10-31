# Implementation Plan: Бережные обращения сайта к бек-офису

**Branch**: нет (worktree, в `master` — перемоткой; папка `specs/002-bounded-backoffice-calls/`) | **Date**: 2026-09-11 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/002-bounded-backoffice-calls/spec.md`

## Summary

Три изменения в обращениях сайта к бек-офису, все внутри `api_client.py` плюс тонкий слой ответов
в `app.py`:

1. **Кеш котировки без купона** — отдельный ограниченный (2000 записей) кеш в памяти процесса со
   сроком `QUOTE_CACHE_TTL`; только успешные ответы, только без `coupon_phone`, просроченное не
   отдаётся никогда. По умолчанию выключен.
2. **Потолок одновременных обращений** — `threading.BoundedSemaphore(N)` на процесс вокруг сетевого
   вызова в каждом читающем методе клиента; ожидание места до `BACKOFFICE_SLOT_WAIT`, затем
   исключение `BackofficeBusy` → гостю 503 `{"error": "busy"}` + `Retry-After: 5` (JSON-ручки) или
   прежнее устаревшее значение (страницы). Бронь и получение брони — мимо потолка. По умолчанию
   выключен.
3. **«Бек-офис временно недоступен»** — ответ 429/503 бек-офиса на любом вызове распознаётся по коду и
   `Retry-After` (тело не читается) → исключение `BackofficeUnavailable`: JSON-ручки — тот же 503
   `busy` с `Retry-After` бек-офиса; `/book` — flash «Бронювання тимчасово недоступне — спробуйте
   за N хвилин» с датами и гостями; страницы — прежнее устаревшее значение. Действует всегда.

Журнал — стартовые INFO о режимах, ERROR на неверные настройки без значения, WARNING
`backoffice_busy` / `backoffice_unavailable` / `book_backoffice_unavailable` не чаще раза в секунду,
`cache=hit|miss|off` в строке `quote`; ни адресов, ни телефонов, ни дат. Подменный транспорт
смоки получает выключенные по умолчанию счётчики обращений и пика для замера. Обоснования —
[research.md](research.md), контракт — [contracts/backoffice-limits.md](contracts/backoffice-limits.md).

## Technical Context

**Language/Version**: Python 3.12.3 (venv `~/venvs/central-aparts-site`); из стандартной
библиотеки — `threading`, `collections.OrderedDict`, `copy`, `math`, `time`, `contextlib`

**Primary Dependencies**: Flask 3.1.3, requests (уже есть), gunicorn 26.2.0 `gthread` (коробка) —
новых нет

**Storage**: память процесса — кеш котировки (≤ 2000 записей) и счётчик мест; ничего на диск

**Testing**: pytest + Flask test client (`tests/`), настоящий `BackofficeClient` поверх подменной
HTTP-сессии в тестах потолка, кеша и 503; `e2e/test_fake_transport.py` (pytest) для счётчиков
подмены; Playwright-смока `e2e/` — только прогон; замер — oha 1.16.0 против `fake_wsgi` на
`127.0.0.1:8010`

**Target Platform**: Linux, gunicorn `-w 3 --threads 12` за Caddy (коробка, после переезда
бек-офиса `BACKOFFICE_URL=http://127.0.0.1:8008`); AWS Lambda — код тот же, настройки не задаются

**Project Type**: web-service (Flask, серверный рендеринг + JSON-прокси к бек-офису)

**Performance Goals**: при занятом потолке ответ гостю ≤ W + 0,5 с; попадание в кеш — без сетевого
вызова; замер (SC-005): пик одновременных обращений к подмене ≤ процессы × N, при одинаковом ключе
обращений к подмене меньше не менее чем в 10 раз

**Constraints**: по умолчанию (настройки не заданы) — прежнее число и порядок обращений и прежние
ответы (FR-016), кроме реакции на 429/503 бек-офиса; `static/main.js`, шаблоны, форма брони, логика
оплаты и сбор не меняются; журнал без адресов, телефонов, дат, номеров броней (FR-014, FR-015);
таймауты обращений прежние (10 с / 30 с)

**Scale/Scope**: 4 файла кода (`config.py`, `api_client.py`, `app.py`, `translations.py`),
2 файла подмены (`e2e/fake_transport.py`, `e2e/fake_wsgi.py`), 5 файлов тестов, 2 файла
документации выката

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Инвариант | Проверка | Итог |
|---|---|---|
| I. Тесты до пуша | Каждое поведение — с pytest: выключенное состояние, кеш (ключ, срок, купон, 400/404, граница, просроченное при сбое), потолок (пик ≤ N под потоками, отказ ≤ W + 0,5 с, обход кешем и бронью), 503/429 бек-офиса на всех путях US5 (HTML- и JSON-тело), неверные настройки, журнал без ПД; счётчики подмены — `e2e/test_fake_transport.py`. Перед пушем — `ruff check .`, `python -m pytest tests/ -q`, `cd e2e && npx playwright test` (fake) зелёные | ✅ |
| II. Секреты и ПД | Журнал без адресов, телефонов (даже хвоста — в новых строках), дат, номеров броней, тела ответа бек-офиса; отдельный тест на это (FR-015, SC-006). В тестах — `+380000000000`-подобные телефоны и адреса 192.0.2.0/24, 198.51.100.0/24, 203.0.113.0/24; значения настроек в ERROR не печатаются | ✅ |
| III. Никаких новых зависимостей от AWS | Только стандартная библиотека; `wsgi_adapter.py`, `infra/` не меняются | ✅ |
| IV. Сеть | Новых слушающих процессов и портов нет; тесты — Flask test client и подменная сессия без сети; замер — `fake_wsgi` только на `127.0.0.1:8010` (PORTS.md: порт смоки и замера), гасится по PID майстера; на `8004` и бойовой бек-офис нагрузка не даётся | ✅ |
| V. Чужое не трогать | чужие проекты, конфиг общего прокси, его admin-порт, бек-офис — не затрагиваются; код бек-офиса только прочитан (research R2) | ✅ |
| VI. Git | Исполнитель: коммит после каждой фазы tasks.md, `git add` явным списком, push сразу в `master` (только CI); включение настроек на машине и выкат в прод — не часть фичи | ✅ |
| VII. Простота и границы | Только то, что в spec.md; новых зависимостей, сервисов, портов нет. Расширение границ сверх исходного задания — `translations.py` (FR-022) и `e2e/fake_transport.py`, `e2e/fake_wsgi.py`, `e2e/test_fake_transport.py` (FR-021) — согласовано дирижёром в clarify 11.09; общий межпроцессный потолок сознательно не делаем (research R3) | ✅ |

**Повторная проверка после Phase 1**: дизайн (data-model, контракт, quickstart) не добавил ни
зависимостей, ни портов, ни касаний прод-выката и `main.js`; реакция на 429/503 «всегда» —
решение дирижёра, записанное в spec.md (FR-016, «Ограничения»). Все семь пунктов ✅, Complexity
Tracking пуст.

## Project Structure

### Documentation (this feature)

```text
specs/002-bounded-backoffice-calls/
├── spec.md
├── plan.md              # этот файл
├── research.md          # Phase 0: решения (где кеш, ключ, семафор, 429/503, журнал, замер)
├── data-model.md        # Phase 1: настройки, запись кеша, места, исключения, события журнала
├── quickstart.md        # Phase 1: как проверить (pytest, смока, замер до/после)
├── contracts/
│   └── backoffice-limits.md   # настройки, ответы гостю, flash брони, строки журнала, интерфейс клиента, счётчики подмены
├── checklists/
│   └── requirements.md
└── tasks.md             # /speckit-tasks
```

### Source Code (repository root)

```text
config.py                   # + _parse_setting(); QUOTE_CACHE_TTL(_INVALID), BACKOFFICE_MAX_CONCURRENT(_INVALID),
                            #   BACKOFFICE_SLOT_WAIT(_INVALID); QUOTE_CACHE_MAX_ENTRIES = 2000 (константа)
api_client.py               # + BackofficeBusy, BackofficeUnavailable(BackofficeError); _parse_retry_after();
                            #   _Slots (BoundedSemaphore + ожидание) и set_slots(); _QuoteCache и set_quote_cache();
                            #   _raise_if_unavailable(); _RateLimitedWarning; quote_cache_status();
                            #   читающие методы: место вокруг сетевого вызова; create_booking/get_booking — без места
app.py                      # + _busy_json(); except BackofficeBusy/BackofficeUnavailable в 4 JSON-ручках;
                            #   /book: except BackofficeUnavailable → flash с минутами; строка quote: cache=;
                            #   _log_backoffice_limits_mode() при импорте; _minutes_phrase()
translations.py             # + booking_temporarily_unavailable_in, booking_temporarily_unavailable,
                            #   minute_one / minute_few / minute_many (ua и en)
tests/
├── conftest.py             # + StubSession/real_client: настоящий BackofficeClient поверх подменной сессии
├── test_limits_config.py   # НОВЫЙ: разбор настроек, стартовые INFO, ERROR без значения
├── test_quote_cache.py     # НОВЫЙ: US1 (выключено = прежнее), US2 (кеш)
├── test_backoffice_slots.py        # НОВЫЙ: US3 (потолок, отказ, обход кешем и бронью)
├── test_backoffice_unavailable.py  # НОВЫЙ: US5 (429/503 + Retry-After, HTML/JSON-тело, все пути)
└── test_limits_logging.py  # НОВЫЙ: US4 (строки журнала, частота, ни одного ПД)
e2e/
├── fake_transport.py       # + счётчики обращений и пика в FakeHTTPSession (выключены по умолчанию); headers у _FakeResponse
├── fake_wsgi.py            # + SMOKE_FAKE_COUNT=1 → счётчики в /__fake__/state (с pid)
└── test_fake_transport.py  # + тесты счётчиков
deploy/
├── README.md               # раздел о трёх настройках рядом с таблицей замера; техработы; замер до/после
└── site.env.example        # три настройки, закомментированные, с объяснением
```

Не меняются: `static/`, `templates/`, `wsgi.py` (журнал под gunicorn уже настроен, research R8),
`wsgi_adapter.py`, `infra/`, `deploy/caddy/`, `deploy/nginx/`,
`deploy/systemd/`, `e2e/tests/`, `e2e/playwright.config.ts`, `e2e/fake_backoffice.py`, `CLAUDE.md`,
`.specify/`.

**Structure Decision**: одиночный Flask-проект в корне; вся логика обращений — в `api_client.py`
(CLAUDE.md §1: все данные через него), `app.py` только переводит исключения клиента в ответ гостю.
Новых модулей в коде нет.

### Устройство изменения (для исполнителя)

- **`config.py`**: `_parse_setting(raw, *, fraction=False) -> tuple[int | float | None, bool]` —
  `(None, False)` для не задана/пусто; `^[0-9]+$` → `(int, False)`; при `fraction=True` ещё
  `^[0-9]+\.[0-9]+$` → `(float, False)`; остальное (`-1`, `abc`, `1e3`, `+2`) → `(None, True)`.
  Поля `Config`: `QUOTE_CACHE_TTL: int` (None/неверное → 0), `BACKOFFICE_MAX_CONCURRENT: int`
  (None/неверное → 0), `BACKOFFICE_SLOT_WAIT: float` (None/неверное → 2.0, `fraction=True`), плюс
  по флагу `*_INVALID`. `QUOTE_CACHE_MAX_ENTRIES = 2000` — константа класса, не окружение.
- **`api_client.py`** — по контракту §5:
  - `_Slots(n, wait)`: при `n == 0` — `contextlib.nullcontext`; иначе `BoundedSemaphore(n)`,
    `acquire(timeout=wait)`; не дождался → `_busy_log.warn(call, waited_ms)` и `raise
    BackofficeBusy(call, waited_ms)`; место отпускается в `finally`. Модульный `_slots` строится из
    `Config` при импорте; `set_slots(n, wait)` — для тестов (как `set_client`).
  - Место берётся **только вокруг сетевого вызова** внутри `fetch()` для кешируемых методов
    (`list_apartments`, `get_apartment`, `get_availability`, `get_rates`) и вокруг вызова в
    `get_quote` (промах кеша) и `check_coupon`. `create_booking`, `get_booking` — без места.
  - `_raise_if_unavailable(r, call)` — первой строкой после каждого сетевого вызова **во всех
    восьми** методах: код 429/503 → `retry_after = _parse_retry_after(r.headers.get("Retry-After"))`,
    `_unavailable_log.warn(...)`, `raise BackofficeUnavailable(status, retry_after, call)`. Тело не
    читается (`r.json()` на HTML бросил бы).
  - `_cached()` не меняется: `BackofficeBusy`/`BackofficeUnavailable` — исключения, поэтому при
    наличии записи отдаётся устаревшее (FR-011, FR-024), без неё — исключение уходит в маршрут.
  - `_QuoteCache(ttl, max_entries)`: `OrderedDict` под `Lock`; `get(key)` — просроченную запись
    удаляет и возвращает `None`, иначе `move_to_end` и `copy.deepcopy(value)`; `put(key, value)` —
    хранит `copy.deepcopy`, при переполнении `popitem(last=False)`. Ключ —
    `(apt_id, check_in, check_out, str(guests))`. Модульный `_quote_cache` из `Config`;
    `set_quote_cache(ttl, max_entries=2000)` — для тестов.
  - `get_quote`: `status = "off"` если кеш выключен или `coupon_phone`; иначе попадание → `"hit"`
    (без места, без сети), промах → место → сеть → `_raise_if_unavailable` → 400/404 → `None` (не
    кешируется) → `raise_for_status` → `put` → `"miss"`. Статус — в `threading.local`, читается
    `quote_cache_status()`; `reset_quote_cache_status()` ставит `"off"`.
  - `_RateLimitedWarning(event)`: под `Lock`, не чаще одной строки в секунду; строка несёт
    `suppressed=<K>` — сколько событий промолчали с прошлой строки.
- **`app.py`**:
  - `_busy_json(retry_after: int)` → `jsonify({"error": "busy"}), 503, {"Retry-After": str(retry_after)}`.
  - В `availability_api`, `quote_api`, `rates_api`, `coupon_check_api` перед `except Exception`:
    `except BackofficeBusy: return _busy_json(5)` и `except BackofficeUnavailable as e: return
    _busy_json(e.retry_after or 5)` — без `logger.error(..., exc_info=True)` (строка уже в клиенте).
  - `quote_api`: `reset_quote_cache_status()` перед вызовом; в строку `quote` добавить
    `cache=%s` из `quote_cache_status()` (с подменным клиентом тестов — `off`).
  - `book`: `except BackofficeUnavailable as e` **перед** `except BackofficeError`: WARNING
    `book_backoffice_unavailable status=%d retry_after=%s` (без телефона, имени, дат); flash
    `_booking_unavailable_text(t, lang, e.retry_after)`; redirect с `check_in`, `check_out`,
    `guests`, `book=1` — как ветка «прочие ошибки».
  - `_minutes_phrase(n, lang)`: ua — `n % 10 == 1 and n % 100 != 11` → `minute_one`;
    `n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14)` → `minute_few`; иначе `minute_many`;
    en — `1` → `minute_one`, иначе `minute_many`. Минуты = `math.ceil(retry_after / 60)`.
  - `_log_backoffice_limits_mode()` вызывается при импорте рядом с `_log_client_ip_mode()`.
- **`e2e/`** — по контракту §6: счётчики в `FakeHTTPSession` включаются аргументом
  `real_client_over(backend, count=True)`; `fake_wsgi.py` включает их при `SMOKE_FAKE_COUNT=1` (только
  вместе с `SMOKE_FAKE_REAL_CLIENT=1`) и добавляет в `/__fake__/state` ключ `transport`. Без
  переменной — ответ `/__fake__/state` байт-в-байт прежний.

### Вне плана (решает дирижёр)

- Строки в `CLAUDE.md` §7 про кеш котировки, потолок и реакцию на 503 (CLAUDE.md §10 просит их после
  фичи; спецификатор CLAUDE.md не трогает).
- Включение настроек на машине, переход юнита на `-w 3 --threads 12`, смена `BACKOFFICE_URL` — отдельные
  задания с цифрами ёмкости бек-офиса.
- Точный вид ответа будущего лимита бек-офиса (если не 429/503 — правка FR-020).

## Complexity Tracking

Нарушений Constitution Check нет — таблица пуста.
