---

description: "Task list: бережные обращения сайта к бек-офису (002)"
---

# Tasks: Бережные обращения сайта к бек-офису

**Input**: `specs/002-bounded-backoffice-calls/` — spec.md, plan.md, research.md, data-model.md,
contracts/backoffice-limits.md, quickstart.md

**Tests**: ОБЯЗАТЕЛЬНЫ — конституция I («каждое изменение поведения идёт с тестом») и FR-018.
Тесты фазы пишутся ДО реализации и сначала падают (или, где сказано, фиксируют уже верное поведение).

**Organization**: по пользовательским историям spec.md. Порядок: US1 (P1, умолчание не сдвинулось) →
US2 (P1, кеш) → US3 (P1, потолок) → US5 (P1, «временно недоступен» / техработы) → US4 (P2, журнал,
документация, замер). US2, US3, US5 правят один `api_client.py` и один `app.py` — фазы идут
последовательно.

**Правила исполнителя** (конституция VI, CLAUDE.md): коммит после каждой фазы (`feat(002): …`,
`test(002): …`, `docs(002): …`), `git add` только явным списком путей, push сразу; перед каждым
пушем `~/venvs/central-aparts-site/bin/ruff check .`, `python -m pytest tests/ -q` (venv
`~/venvs/central-aparts-site`) и `cd e2e && npx playwright test` (fake; перед этим `ss -ltnp | grep
:8010` — порт свободен) зелёные. В тестах и доках — только синтетика: телефоны `+380000000000`,
`+380000000001`; адреса 192.0.2.0/24, 198.51.100.0/24, 203.0.113.0/24 и петля; даты 2030-06-…;
номер брони `test1234`.
Не трогать: `static/`, `templates/`, `wsgi.py`, `wsgi_adapter.py`, `infra/`, `deploy/caddy/`,
`deploy/nginx/`, `deploy/systemd/`, `e2e/tests/`, `e2e/playwright.config.ts`,
`e2e/fake_backoffice.py`, `CLAUDE.md`, `.specify/`. Логику `/book` (оплата, форма, прочие ветки
ошибок) — только добавить ветку `BackofficeUnavailable` (T026).

**Имена тестов**: функции начинаются с `test_config_`, `test_us1_`, `test_us2_`, `test_us3_`,
`test_us4_`, `test_us5_` — на этом держатся команды `-k` в «Independent Test».

**Время в тестах**: задержки подменной сессии 50–100 мс, `wait_s` 0,2 с; срок кеша — через
подмену `api_client.time.monotonic`, без `sleep` на секунды. Весь набор фичи — не дольше ~15 с.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: можно параллельно (разные файлы, нет зависимостей)
- **[Story]**: US1–US5 из spec.md

---

## Phase 1: Setup

**Purpose**: зелёная база и подменная HTTP-сессия для тестов с настоящим клиентом

- [X] T001 Прогнать `python -m pytest tests/ -q` в venv `~/venvs/central-aparts-site` на чистом `master` и записать число passed в сообщение первого коммита фичи (база для SC-001); изменений в файлах нет
- [X] T002 В `tests/conftest.py` добавить (research.md R11): класс `StubSession` с методами `get(url, params=None, headers=None, timeout=None)` и `post(url, json=None, headers=None, timeout=None)` и атрибутом `headers: dict`; ответ задаётся функцией-маршрутом `(method, path, params, headers, body) -> StubResponse`; `StubResponse(status_code, body, headers=None, content_type="application/json")` с `.status_code`, `.headers` (регистронезависимый доступ, как у `requests`, — `requests.structures.CaseInsensitiveDict`), `.json()` (на не-JSON теле бросает `ValueError`), `.raise_for_status()` (≥ 400 → `requests.HTTPError(response=self)`); в сессии — необязательная задержка `delay_s`, счётчики `calls` (по пути) и `peak` одновременных вызовов под `threading.Lock`, событие-«пробка» `gate: threading.Event | None` (вызов ждёт `gate.wait()` — чтобы держать места занятыми); маршрут по умолчанию отвечает как `MockBackofficeClient` (каталог из двух квартир `apt-uuid-a`/`apt-uuid-b`, котировка с `total` 4492 — а при непустом `coupon_phone` в параметрах ещё `coupon_discount: -500`, `coupon_applied: true` и `total` 3992, занятость `[]`, цены, купон `{"eligible": false}`, бронь `payment_url` `https://pay.monobank.ua/test`, `get_booking` → `test1234`). Фикстура `real_client(monkeypatch)` — настоящий `api_client.BackofficeClient` с `_session = StubSession(...)`, внедрённый через `set_client`; до и после теста очищает `api_client._ttl_cache`, вызывает `set_slots(0, 2.0)` и `set_quote_cache(0)` (эти функции появятся в T008/T012 — до них фикстура их не зовёт, если атрибута нет); возвращает объект с доступом к сессии

**Checkpoint**: `pytest tests/ -q` зелёный (новые помощники ничего не ломают); коммит `test(002): підмінна HTTP-сесія для справжнього BackofficeClient` + push

---

## Phase 2: Foundational — три настройки и стартовые строки режима

**Purpose**: разбор `QUOTE_CACHE_TTL`, `BACKOFFICE_MAX_CONCURRENT`, `BACKOFFICE_SLOT_WAIT` — от них зависят все истории

**⚠️ CRITICAL**: без этой фазы истории не начинать

- [X] T003 Создать `tests/test_limits_config.py`: тесты `test_config_parse_*` для `config._parse_setting` по data-model.md «Настройки» — целые (`fraction=False`): `None`, `""`, `"  "` → `(None, False)`; `"0"` → `(0, False)`; `" 30 "` → `(30, False)`; `"-1"`, `"+2"`, `"abc"`, `"1.5"`, `"1e3"`, `"1,5"`, `"١"` → `(None, True)`; дробные (`fraction=True`): `"2"` → `(2, False)`, `"0.2"` → `(0.2, False)`, `"1."`, `".5"`, `"-0.5"` → `(None, True)`; тест `test_config_fields_defaults`: при пустом окружении `Config.QUOTE_CACHE_TTL == 0`, `Config.BACKOFFICE_MAX_CONCURRENT == 0`, `Config.BACKOFFICE_SLOT_WAIT == 2.0`, все `*_INVALID` ложны, `Config.QUOTE_CACHE_MAX_ENTRIES == 2000` (перечитать через `importlib.reload(config)` под `monkeypatch.delenv`/`setenv` и вернуть модуль в исходное состояние в `finally`); убедиться, что падают
- [X] T004 В `config.py` добавить `_parse_setting(raw: str | None, *, fraction: bool = False) -> tuple[int | float | None, bool]` (после `strip()`: пусто → `(None, False)`; `^[0-9]+$` → `(int, False)`; при `fraction` ещё `^[0-9]+\.[0-9]+$` → `(float, False)`; иначе `(None, True)`) и в класс `Config` по образцу `TRUSTED_PROXY_COUNT`: `QUOTE_CACHE_TTL: int` («не задана»/неверное → 0) и `QUOTE_CACHE_TTL_INVALID`; `BACKOFFICE_MAX_CONCURRENT: int` (→ 0) и `…_INVALID`; `BACKOFFICE_SLOT_WAIT: float` («не задана»/неверное → 2.0, `fraction=True`) и `…_INVALID`; константу `QUOTE_CACHE_MAX_ENTRIES: int = 2000`; комментарий по-украински со ссылкой на `deploy/README.md`; тесты T003 зелёные
- [X] T005 В `tests/test_limits_config.py` тест `test_config_startup_lines`: вызовы `app._log_backoffice_limits_mode()` через `caplog` (логгер `app`) по contracts/backoffice-limits.md §4 — всё выключено → ровно две INFO `quote_cache_mode mode=off` и `backoffice_slots_mode mode=off`; `QUOTE_CACHE_TTL=30`, `BACKOFFICE_MAX_CONCURRENT=3`, `BACKOFFICE_SLOT_WAIT=2.0` (через `monkeypatch.setattr(Config, …)`) → `quote_cache_mode mode=on ttl=30 max_entries=2000` и `backoffice_slots_mode mode=on n=3 wait_s=2.0`; каждый `*_INVALID=True` → одна ERROR `backoffice_limits_config_invalid env=<ИМЯ> fallback=off` (для `BACKOFFICE_SLOT_WAIT` — `fallback=2`), а сырое значение (`abc`, выставленное `monkeypatch.setenv` рядом) не встречается в `caplog.text`; убедиться, что падают
- [X] T006 В `app.py` добавить `_log_backoffice_limits_mode()` по контракту §4 (сначала ERROR по каждой неверной настройке, затем две INFO) и вызвать её один раз на уровне модуля сразу после `_log_client_ip_mode()`; значений неверных настроек и адресов не писать; журнал не настраивать (research.md R8); тесты T005 зелёные

**Checkpoint**: настройки читаются, режим виден при старте; коммит `feat(002): QUOTE_CACHE_TTL, BACKOFFICE_MAX_CONCURRENT, BACKOFFICE_SLOT_WAIT у config.py` + push

---

## Phase 3: User Story 1 — без настроек ничего не меняется (Priority: P1) 🎯 MVP

**Goal**: при выключенных механизмах число и порядок обращений к бек-офису и ответы гостю — как до фичи (FR-016, SC-001)

**Independent Test**: `python -m pytest tests/test_quote_cache.py -k us1 -v`

### Tests for User Story 1 ⚠️

- [X] T007 [US1] Создать `tests/test_quote_cache.py` с тестами на фикстуре `real_client`: `test_us1_every_quote_hits_backoffice` — две одинаковые `GET /api/quote/apt-uuid-a?check_in=2030-06-01&check_out=2030-06-03&guests=2` → в сессии 2 вызова пути котировки, оба ответа 200 с `total` 4492; `test_us1_no_waiting_without_ceiling` — сессия с `delay_s=0.1`, 20 потоков, стартующих через `threading.Barrier(20)`, одновременно зовут `get_client().get_quote(...)` с разными датами → `peak == 20`, все 20 получили ответ, общее время < 1 с (никакого ожидания мест); `test_us1_zero_equals_unset` — `set_slots(0, 2.0)` и `set_quote_cache(0)` дают то же, что и два предыдущих теста; `test_us1_old_suite_untouched` — `MockBackofficeClient` (фикстура `client`) по-прежнему получает `get_quote` с теми же аргументами, что до фичи (`mock_client.last_quote`); убедиться, что падают там, где нужны `set_slots`/`set_quote_cache`

### Implementation for User Story 1

- [X] T008 [US1] В `api_client.py` добавить каркас по контракту §5 с поведением «выключено»: `_Slots(n, wait_s)` с методом-контекстом `slot(call)`, который при `n == 0` — `contextlib.nullcontext()`; модульный `_slots` из `Config.BACKOFFICE_MAX_CONCURRENT` / `Config.BACKOFFICE_SLOT_WAIT`; `set_slots(n: int, wait_s: float) -> None`; `_QuoteCache(ttl, max_entries)` с `enabled == (ttl > 0)`; модульный `_quote_cache` из `Config.QUOTE_CACHE_TTL` / `Config.QUOTE_CACHE_MAX_ENTRIES`; `set_quote_cache(ttl: int, max_entries: int = 2000) -> None` (новый пустой кеш); `quote_cache_status() -> str` и `reset_quote_cache_status() -> None` на `threading.local` (по умолчанию `"off"`); сигнатуры методов `BackofficeClient` не менять; обновить шапку модуля (котировка кешируется только по настройке, без купона); тесты T007 зелёные

**Checkpoint**: умолчание закреплено тестами; `pytest tests/ -q` и смока зелёные; коммит `feat(002): каркас стелі та кешу котирування, вимкнено за замовчуванням` + push

---

## Phase 4: User Story 2 — повторные котировки без купона не долетают до бек-офиса (Priority: P1)

**Goal**: кеш котировки по FR-001–FR-006 (ключ, срок, купон мимо, только 2xx, без просроченного, ≤ 2000 записей)

**Independent Test**: `python -m pytest tests/test_quote_cache.py -k us2 -v`

### Tests for User Story 2 ⚠️ (сначала падают)

- [X] T009 [US2] В `tests/test_quote_cache.py` тесты при `set_quote_cache(30)`: `test_us2_hundred_identical_one_call` (SC-002) — 100 одинаковых котировок без купона → 1 вызов сессии, все 100 ответов равны ответу бек-офиса; `test_us2_key_components` — по одному отличию в квартире (`apt-uuid-b`), заезде, выезде, гостях (`2` → `3`) → каждое отличие даёт новый вызов; `test_us2_language_not_in_key` — та же котировка после `GET /lang/en` → попадание (0 новых вызовов)
- [X] T010 [US2] В `tests/test_quote_cache.py`: `test_us2_coupon_bypasses_cache` (FR-003, SC-002) — при тёплом кеше 100 котировок с `coupon_phone=+380000000000` (через `query_string` словарём) → 100 вызовов, каждый с `coupon_phone` в параметрах и `X-Forwarded-For` в заголовках; затем котировка без купона — всё ещё попадание в ответ, положенный анонимной котировкой (ответ купонной не попал в кеш: у подменного ответа с купоном — поле `coupon_discount`, в анонимном ответе его нет); `test_us2_errors_not_cached` (FR-004) — ответы сессии 400, 404, 500, исключение `requests.ConnectionError` → каждая повторная котировка снова идёт в сессию; 400/404 → гостю 400, 500/исключение → 503 `unavailable`
- [X] T011 [US2] В `tests/test_quote_cache.py`: `test_us2_expiry` — подменить `api_client.time.monotonic` управляемыми часами: попадание до `ttl`, промах и новый вызов после `ttl`, новый ответ бек-офиса заменяет запись; `test_us2_no_stale_on_failure` (FR-005) — после истечения сессия бросает `requests.ConnectionError` → гостю 503 `unavailable`, а не просроченная котировка; `test_us2_bounded` (FR-006) — `set_quote_cache(30, max_entries=3)`: 4 разных ключа → в кеше 3, первый вытеснен (снова вызов), последний — попадание; плюс `api_client._quote_cache` по умолчанию построен с `max_entries == Config.QUOTE_CACHE_MAX_ENTRIES == 2000`; `test_us2_returns_copy` — изменение словаря, возвращённого `get_quote`, не меняет следующий ответ из кеша

### Implementation for User Story 2

- [X] T012 [US2] В `api_client.py` реализовать `_QuoteCache` по data-model.md «Запись кеша котировки»: `OrderedDict` под `threading.Lock`; `get(key)` — запись с `expires_at <= time.monotonic()` удалить и вернуть `None`, иначе `move_to_end` и `copy.deepcopy(value)`; `put(key, value)` — хранить `copy.deepcopy(value)` с `expires_at = time.monotonic() + ttl`, при превышении `max_entries` — `popitem(last=False)`. В `get_quote`: ключ `(apt_id, check_in, check_out, str(guests))`; если кеш включён и `coupon_phone` пуст — попадание → статус `"hit"`, вернуть копию без сети; иначе сетевой вызов как сейчас; успешный 2xx без купона → `put`, статус `"miss"`; `None` (400/404) и исключения не кладутся; с купоном или при выключенном кеше — статус `"off"`
- [X] T013 [US2] В `app.py` в `quote_api`: `reset_quote_cache_status()` перед `get_client().get_quote(...)`; в INFO-строку `quote` дописать `cache=%s` из `quote_cache_status()` (контракт §4); прочие поля строки не менять; тесты T009–T011 зелёные

**Checkpoint**: US1 и US2 зелёные; коммит `feat(002): кеш котирування без купона (QUOTE_CACHE_TTL)` + push

---

## Phase 5: User Story 3 — всплеск не забивает бек-офис, гость не висит (Priority: P1)

**Goal**: потолок N мест на процесс, ожидание ≤ W, быстрый отказ, мимо — кеши и бронь (FR-007–FR-011, FR-019)

**Independent Test**: `python -m pytest tests/test_backoffice_slots.py -k us3 -v`

### Tests for User Story 3 ⚠️ (сначала падают)

- [X] T014 [US3] Создать `tests/test_backoffice_slots.py`: `test_us3_peak_not_above_n` (SC-003) — `set_slots(3, 2.0)`, сессия `delay_s=0.05`, 30 потоков одновременно зовут по кругу `get_quote` (разные даты), `check_coupon`, `list_apartments`/`get_apartment`/`get_availability`/`get_rates` (с очисткой `_ttl_cache` перед каждым, чтобы шли в сеть) → `peak <= 3`, все 30 получили ответ; параметризовать по `Config.BACKOFFICE_URL` ∈ {`https://cv.central-aparts.store`, `http://127.0.0.1:8008`} (FR-019, клиент пересоздаётся под каждое значение)
- [X] T015 [US3] В `tests/test_backoffice_slots.py`: `test_us3_refusal_within_wait` (SC-004) — `set_slots(1, 0.2)`, сессия с `gate` держит единственное место в фоновом потоке; `get_quote` в главном потоке бросает `api_client.BackofficeBusy` с `call == "quote"` за время ≤ 0,2 + 0,5 с, а сессия не получила второго вызова; `test_us3_slot_freed_before_wait` — место освобождается через 0,05 с (`gate.set()`) → ожидающий получает обычный ответ; `test_us3_slot_released_on_error` — сессия бросает `requests.ConnectionError` при `set_slots(1, 0.2)` → следующий вызов получает место (не `BackofficeBusy`)
- [X] T016 [US3] В `tests/test_backoffice_slots.py` ответы гостю при занятом месте (`set_slots(1, 0.2)`, место держит фоновый поток; FR-011, контракт §2): `GET /api/quote/apt-uuid-a?…`, `GET /api/availability/apt-uuid-a`, `GET /api/rates/apt-uuid-a`, `POST /api/coupon/check` → 503, тело `{"error": "busy"}`, заголовок `Retry-After: 5`; `GET /` и `GET /apartment/apt-uuid-a` при прогретом и затем просроченном `_ttl_cache` → 200 из устаревшего (карточка `Апартаменти A` в HTML); при пустом кеше → `/` 200 без карточек, `/apartment/apt-uuid-a` → 500 (как сегодня; в тесте `monkeypatch.setitem(flask_app.config, "PROPAGATE_EXCEPTIONS", False)` — при `TESTING=True` Flask иначе пробрасывает исключение в тест, а не отдаёт страницу 500)
- [X] T017 [US3] В `tests/test_backoffice_slots.py`: `test_us3_cache_hits_take_no_slot` (FR-009) — при `set_quote_cache(30)` и прогретой котировке, прогретом каталоге и занятом единственном месте котировка, `/` и `/apartment/apt-uuid-a` отвечают 200 за < 0,2 с; `test_us3_booking_bypasses_ceiling` (FR-010) — при занятом месте `POST /book/apt-uuid-a` (данные как в `tests/test_quote_proxy.py::test_booking_flow_unchanged`, телефон `+380000000000`) → 302 на `https://pay.monobank.ua/test` за < 0,2 с, `GET /booking-confirmed?ref=test1234` → 200 и сессия получила вызов `booking/test1234`

### Implementation for User Story 3

- [X] T018 [US3] В `api_client.py`: класс `BackofficeBusy(Exception)` с полями `call: str`, `waited_ms: int`; `_Slots.slot(call)` при `n > 0` — `threading.BoundedSemaphore(n)`, `acquire(timeout=wait_s)`; не дождался → `logger.warning("backoffice_busy call=%s waited_ms=%d suppressed=0", …)` (частоту ограничит T041) и `raise BackofficeBusy(call, waited_ms)`; место отпускается в `finally`. Обернуть `with _slots.slot(<call>):` **только сетевой вызов**: внутри `fetch()` у `list_apartments` (`"apartments"`), `get_apartment` (`"apartment"`), `get_availability` (`"availability"`), `get_rates` (`"rates"`); в `get_quote` на промахе кеша (`"quote"`); в `check_coupon` (`"coupon_check"`). `create_booking` и `get_booking` не оборачивать. `_cached()` не менять (устаревшее при исключении — research.md R4)
- [X] T019 [US3] В `app.py`: импорт `BackofficeBusy`; `_busy_json(retry_after: int)` → `jsonify({"error": "busy"}), 503, {"Retry-After": str(retry_after)}`; в `availability_api`, `quote_api`, `rates_api`, `coupon_check_api` перед `except Exception` — `except BackofficeBusy: return _busy_json(5)` без `logger.error(..., exc_info=True)`; маршруты страниц не менять; тесты T014–T017 зелёные

**Checkpoint**: US1–US3 зелёные; коммит `feat(002): стеля одночасних звернень до бек-офісу (BACKOFFICE_MAX_CONCURRENT)` + push

---

## Phase 6: User Story 5 — техработы бек-офиса: гость понимает, что происходит (Priority: P1)

**Goal**: 429/503 бек-офиса на любом вызове → `BackofficeUnavailable` по коду и `Retry-After`, тело не читается; JSON-ручки — 503 `busy`, `/book` — flash «за N хвилин» с датами и гостями, страницы — устаревшее (FR-020, FR-022–FR-024, SC-007). Действует без настроек.

**Independent Test**: `python -m pytest tests/test_backoffice_unavailable.py -k us5 -v`

### Tests for User Story 5 ⚠️ (сначала падают)

- [X] T020 [US5] Создать `tests/test_backoffice_unavailable.py`: маршрут `StubSession`, отвечающий на ВСЕ пути 503 + заголовки `Retry-After: 300`, `Cache-Control: no-store` + тело; параметризация тела: HTML (`<html><body>Технічні роботи</body></html>`, `content_type="text/html"`) и JSON `{"error": "maintenance", "retry_after": 300}`; плюс вариант с кодом 429. Тест `test_us5_json_endpoints` — `GET /api/quote/apt-uuid-a?…`, `GET /api/availability/apt-uuid-a`, `GET /api/rates/apt-uuid-a`, `POST /api/coupon/check` → 503, `{"error": "busy"}`, `Retry-After: 300`; каждый путь — ровно один вызов сессии (без повторов)
- [X] T021 [US5] В `tests/test_backoffice_unavailable.py` тест `test_us5_retry_after_values` (FR-020, Edge Cases): `Retry-After` отсутствует, `"Wed, 21 Oct 2026 07:28:00 GMT"`, `"0"`, `"-5"`, `"1.5"`, `"3601"` → у котировки `Retry-After: 5`; `"1"` → `1`; `"3600"` → `3600`; и модульный тест `api_client._parse_retry_after` на тех же значениях (`None` / `1` / `3600`)
- [X] T022 [US5] В `tests/test_backoffice_unavailable.py` тест `test_us5_book_flash` (FR-022, SC-007): `POST /book/apt-uuid-a` с `check_in=2030-06-01`, `check_out=2030-06-03`, `guests=2`, `name=Тест`, `phone=+380000000000` → 302 на `/apartment/apt-uuid-a` с параметрами `check_in=2030-06-01`, `check_out=2030-06-03`, `guests=2`, `book=1`; flash категории `error` с текстом `Бронювання тимчасово недоступне — спробуйте за 5 хвилин.` (ua, сессия по умолчанию) и `Booking is temporarily unavailable — please try again in 5 minutes.` после `GET /lang/en`; сессия получила ровно один `POST` брони; без `Retry-After` → `Бронювання тимчасово недоступне — спробуйте трохи пізніше.`; параметризованная таблица форм (контракт §3): 60 → «за 1 хвилину», 61 → «за 2 хвилини», 300 → «за 5 хвилин», 720 → «за 12 хвилин», 1260 → «за 21 хвилину», 1320 → «за 22 хвилини», en 60 → «in 1 minute», en 120 → «in 2 minutes» (проверять через `app._booking_unavailable_text` и одним сквозным `/book`)
- [X] T023 [US5] В `tests/test_backoffice_unavailable.py`: `test_us5_coupon_no_signal` (FR-023) — `POST /api/coupon/check` для `+380000000000` и `+380000000001` → байт-в-байт одинаковые ответы (код, тело, `Retry-After`); `test_us5_pages` (FR-024) — прогретые `/` и `/apartment/apt-uuid-a`, затем бек-офис переключается на 503 и `_ttl_cache` просрочен → 200 из устаревшего; холодный кеш → `/` 200 без карточек, `/apartment/apt-uuid-a` → 500 (с `PROPAGATE_EXCEPTIONS=False`, как в T016); `GET /booking-confirmed?ref=test1234` → 200 (страница без данных брони, как при сбое сегодня); `test_us5_not_cached` — при `set_quote_cache(30)` котировка на 503 не кладётся в кеш: после возврата бек-офиса к 200 следующая котировка идёт в сессию; `test_us5_html_body_never_parsed` — у `StubResponse` с HTML-телом `.json()` ни разу не вызван (счётчик вызовов `json` в подмене)

### Implementation for User Story 5

- [X] T024 [US5] В `api_client.py`: `_parse_retry_after(value: str | None) -> int | None` (только `^[0-9]+$`, значение 1–3600, иначе `None`); класс `BackofficeUnavailable(BackofficeError)` с полями `status: int`, `retry_after: int | None`, `call: str` и `str(e) == "backoffice_unavailable"`; `_raise_if_unavailable(r, call)`: при `r.status_code in (429, 503)` — `logger.warning("backoffice_unavailable status=%d retry_after=%s call=%s suppressed=0", …)` (`retry_after` или `none`; частоту ограничит T041) и `raise BackofficeUnavailable(...)`, тело не читать; вызвать первой строкой после сетевого вызова во ВСЕХ восьми методах клиента (`list_apartments`, `get_apartment`, `get_availability`, `get_rates`, `get_quote`, `check_coupon`, `create_booking` — до разбора 409/400/502, `get_booking`) — внутри места под потолком там, где оно есть
- [X] T025 [P] [US5] В `translations.py` в `"ua"` и `"en"` добавить ключи по контракту §3: `booking_temporarily_unavailable_in` («Бронювання тимчасово недоступне — спробуйте за {n} {unit}.» / «Booking is temporarily unavailable — please try again in {n} {unit}.»), `booking_temporarily_unavailable` («Бронювання тимчасово недоступне — спробуйте трохи пізніше.» / «Booking is temporarily unavailable — please try again a little later.»), `minute_one` («хвилину» / «minute»), `minute_few` («хвилини» / «minutes»), `minute_many` («хвилин» / «minutes»)
- [X] T026 [US5] В `app.py`: импорт `BackofficeUnavailable`; в четырёх JSON-ручках рядом с веткой `BackofficeBusy` — `except BackofficeUnavailable as e: return _busy_json(e.retry_after or 5)`; `_minutes_phrase(n: int, lang: str) -> str` (ua: `n % 10 == 1 and n % 100 != 11` → `minute_one`; `n % 10 in (2, 3, 4) and n % 100 not in (12, 13, 14)` → `minute_few`; иначе `minute_many`; en: `n == 1` → `minute_one`, иначе `minute_many`); `_booking_unavailable_text(t, lang, retry_after) -> str` (есть `retry_after` → `t("booking_temporarily_unavailable_in").format(n=ceil(retry_after / 60), unit=…)`, иначе `t("booking_temporarily_unavailable")`); в `book()` перед `except BackofficeError` — `except BackofficeUnavailable as e:` → `logger.warning("book_backoffice_unavailable status=%d retry_after=%s", …)` (без телефона, его хвоста, имени, дат), `flash(_booking_unavailable_text(...), "error")`, `redirect(url_for("apartment_detail", apt_id=apt_id, guests=guests, book=1, check_in=check_in, check_out=check_out))`; прочие ветки `/book` не менять; тесты T020–T023 зелёные

**Checkpoint**: US1–US3, US5 зелёные; коммит `feat(002): 429/503 бек-офісу — зрозуміла відповідь гостю, flash броні з хвилинами` + push

---

## Phase 7: User Story 4 — эксплуатация видит, как работают рычаги (Priority: P2)

**Goal**: частота предупреждений ≤ 1/с с `suppressed`, ни одного ПД в журнале (FR-014, FR-015, SC-006); счётчики подмены для замера (FR-021); документация выката (FR-017); замер до/после (SC-005)

**Independent Test**: `python -m pytest tests/test_limits_logging.py -k us4 -v` и `python -m pytest e2e/test_fake_transport.py -v`; чтение `deploy/README.md`, `deploy/site.env.example`

### Tests for User Story 4 ⚠️ (сначала падают)

- [X] T027 [US4] Создать `tests/test_limits_logging.py`: `test_us4_busy_rate_limited` — управляемые часы в `api_client` (подмена `time.monotonic` для ограничителя), 50 отказов `BackofficeBusy` в пределах одной «секунды» → ровно 1 WARNING `backoffice_busy call=quote waited_ms=… suppressed=0`; после сдвига часов на 1 с следующий отказ → строка с `suppressed=49`; то же для `backoffice_unavailable` (строка несёт `status=503 retry_after=300 call=…`), и события двух видов считаются раздельно (FR-014, контракт §4)
- [X] T028 [US4] В `tests/test_limits_logging.py`: `test_us4_quote_cache_mark` — строка `quote` содержит `cache=miss`, затем `cache=hit` (кеш включён), `cache=off` (выключен, с купоном, с `MockBackofficeClient`); `test_us4_lines_distinct` — `backoffice_busy`, `backoffice_unavailable`, `book_backoffice_unavailable` и прежние `quote_api_error` / `book_unexpected_error` различимы по первому слову, в строках фичи `record.exc_info is None`
- [X] T029 [US4] В `tests/test_limits_logging.py` тест `test_us4_no_personal_data` (SC-006, FR-015, research.md R8): `caplog.set_level(logging.DEBUG)` для всех логгеров; прогнать сценарии T010, T016, T020, T022 и T023 с `X-Forwarded-For: 198.51.100.23, 203.0.113.7`, телефонами `+380000000000`/`+380000000001`, датами `2030-06-01`/`2030-06-03`, `ref=test1234`; утверждать для записей событий фичи (`quote_cache_mode`, `backoffice_slots_mode`, `backoffice_limits_config_invalid`, `backoffice_busy`, `backoffice_unavailable`, `book_backoffice_unavailable`): нет `198.51.100.23`, `203.0.113.7`, `+380000000000`, `380000000000`, `0000`, `0001`, `2030-06`, `test1234`, `Технічні роботи`, `maintenance`; во всём `caplog.text` нет адресов и полных телефонов; в записях фичи нет `Traceback`
- [X] T030 [P] [US4] В `e2e/test_fake_transport.py` тесты счётчиков (FR-021, контракт §6): `real_client_over(backend)` без `count` — у сессии нет счёта (или `counters()` возвращает нули) и поведение прежнее; `real_client_over(DelayedBackoffice(fake, 50), count=True)` + 10 потоков котировок → `counters()["calls"] == 10`, `peak` в пределах 2–10; у `_FakeResponse` есть `headers` (пустой словарь); тесты чистых помощников `fake_wsgi` (без перезагрузки модуля и без сети): `_build_client(environ, backend)` — при `{}` и при `{"SMOKE_FAKE_COUNT": "1"}` без `SMOKE_FAKE_REAL_CLIENT` счёта нет, при `{"SMOKE_FAKE_REAL_CLIENT": "1", "SMOKE_FAKE_COUNT": "1"}` — сессия со счётом; `_state_payload(state, session)` — без счёта возвращает `state` без изменений (равенство словарей), со счётом добавляет ключ `transport` с `pid` (равен `os.getpid()`), `calls`, `peak`

### Implementation for User Story 4

- [X] T031 [US4] В `api_client.py` класс `_RateLimitedWarning(event: str)`: `threading.Lock`, момент последней строки по `time.monotonic()`, счётчик подавленных; `warn(fmt, *args)` пишет `logger.warning(f"{event} {fmt} suppressed=%d", *args, suppressed)` не чаще раза в секунду, иначе увеличивает счётчик; экземпляры `_busy_log` и `_unavailable_log`; заменить прямые `logger.warning` из T018 и T024 на них; тесты T027–T029 зелёные (если T028–T029 зелёные уже до правки — оставить как фиксацию поведения)
- [X] T032 [P] [US4] В `e2e/fake_transport.py`: у `_FakeResponse` атрибут `headers = {}`; у `FakeHTTPSession(backend, count=False)` при `count=True` — счётчики `calls`, `inflight`, `peak` под `threading.Lock` вокруг вызова подменного бек-офиса в `_dispatch` и метод `counters() -> {"calls": int, "peak": int}`; `real_client_over(backend, count: bool = False)`; в `e2e/fake_wsgi.py` — вынести сборку в чистые помощники `_build_client(environ, backend) -> (client, counting_session | None)` (при `SMOKE_FAKE_COUNT=1` вместе с `SMOKE_FAKE_REAL_CLIENT=1` — `real_client_over(backend, count=True)`, иначе как сейчас) и `_state_payload(state: dict, session) -> dict` (со счётом — `{**state, "transport": {"pid": os.getpid(), **session.counters()}}`, без — `state` как есть); модуль зовёт их с `os.environ`, `fake_state()` отдаёт `_state_payload(fake.state(), session)`; без переменной ответ `/__fake__/state` прежний; обновить докстринги обоих файлов; тест T030 зелёный; смока без изменений зелёная
- [X] T033 [P] [US4] В `deploy/README.md` (українською, як увесь файл) після розділу «Замір із затримкою бек-офісу» додати розділ про три налаштування за FR-017: сенс `QUOTE_CACHE_TTL`, `BACKOFFICE_MAX_CONCURRENT`, `BACKOFFICE_SLOT_WAIT`; «вимкнено» за замовчуванням; правило «сайт = процеси × N; N = ⌊бюджет бек-офісу на сайт / процеси⌋» (приклад: бюджет 8 при `-w 3` → N = 2); бронь і підтвердження — повз стелю; «ціна на екрані може відставати до T», півночі — до T с (research.md R2); розрахунок за цифрами бек-офісу (research.md R12: бюджет — 50 одночасних котирувань на весь сайт, їх замір 11.09 — таблиця R12): при `-w 3 --threads 12` не більше 36 одночасних звернень, стеля не потрібна і не задається; потрібна, коли процеси × потоки > 50, тоді N = ⌊50 / процеси⌋; рекомендовані T = 30, W = 2; реакція на 429/503 бек-офісу (працює завжди) і поведінка у вікні техробіт; правило перезапуску: у вікні техробіт сервіс сайту не перезапускати, зміна `BACKOFFICE_URL` на `http://127.0.0.1:8008` і перезапуск — лише коли бек-офіс на 8008 уже відповідає без техробіт (сигнал диригента бек-офісу), одразу після перезапуску — прогрів головної й усіх квартир одним GET; процедура заміру до/після — посиланням на `specs/002-bounded-backoffice-calls/quickstart.md` §5
- [X] T034 [P] [US4] В `deploy/site.env.example` додати закоментовані `# QUOTE_CACHE_TTL=`, `# BACKOFFICE_MAX_CONCURRENT=`, `# BACKOFFICE_SLOT_WAIT=2` з поясненням українською (порожньо = вимкнено, як сьогодні; що ставити при увімкненні; посилання на розділ `deploy/README.md`)
- [X] T035 [US4] Замер до/после по `specs/002-bounded-backoffice-calls/quickstart.md` §5 (SC-005): только `127.0.0.1:8010`, перед стартом `ss -ltnp`, переменные — скрипт-файлом, гасить по PID майстера; конфигурация `-w 3 --threads 12`, `SMOKE_FAKE_DELAY_MS=350`, `SMOKE_FAKE_REAL_CLIENT=1`, `SMOKE_FAKE_COUNT=1`; сценарии A (один ключ) и B (случайные даты) до и после (`QUOTE_CACHE_TTL=30`, `BACKOFFICE_MAX_CONCURRENT=2`, `BACKOFFICE_SLOT_WAIT=2`); таблица p95 / доля 503 / сумма `calls` / `peak` по процессам — в разделе T033 в `deploy/README.md`; проверить: `peak` ≤ N у каждого процесса, в сценарии A `calls` меньше ≥ 10 раз; на `8004` и бойовой бек-офис нагрузки нет

**Checkpoint**: все истории зелёные; коммиты `feat(002): журнал стелі й недоступності без даних гостя, лічильники підміни` и `docs(002): налаштування стелі й кешу, техроботи, замір до/після` + push

---

## Phase 8: Polish & Cross-Cutting

- [X] T036 Прогнать `~/venvs/central-aparts-site/bin/ruff check .` и `python -m pytest tests/ -q` — всё зелёное, число passed ≥ база T001 + новые тесты (SC-001)
- [X] T037 Прогнать `python -m pytest e2e/test_fake_transport.py -q` и смоку `cd e2e && npx playwright test` (fake) — зелёные; `git diff origin/master --stat -- e2e/` показывает только `e2e/fake_transport.py`, `e2e/fake_wsgi.py`, `e2e/test_fake_transport.py`
- [X] T038 Проверить diff фичи: `git diff origin/master --stat` затрагивает только файлы из plan.md «Source Code»; `git diff origin/master -- static/ templates/ wsgi.py wsgi_adapter.py infra/` пуст (FR-016); грепом по diff — нет телефонов кроме `+380000000000`/`+380000000001`, адресов вне 192.0.2.0/24, 198.51.100.0/24, 203.0.113.0/24 и петли, секретов
- [X] T039 Пройти `specs/002-bounded-backoffice-calls/quickstart.md` шаги 1–4 и сверить вывод с ожидаемым; отчёт дирижёру — числа тестов, таблица замера T035, без адресов и телефонов

---

## Dependencies & Execution Order

### Phase Dependencies

- Phase 1 → Phase 2 → Phase 3 (US1) → Phase 4 (US2) → Phase 5 (US3) → Phase 6 (US5) → Phase 7 (US4) → Phase 8
- US2, US3, US5 правят один `api_client.py` и один `app.py` — последовательно. US4 (T031) правит
  строки журнала из T018 и T024 — после US3 и US5.

### Within Each Story

- Тесты фазы пишутся первыми и падают, затем реализация
- T002 → все тесты на `real_client`; T004 → T006, T008; T008 → T012, T018; T012 → T013;
  T018 → T019; T024 → T026; T025 → T026; T018 + T024 → T031; T032 → T035; T033 → T035
- T033, T034 не зависят от кода и могут идти параллельно с T027–T032

### Parallel Opportunities

- T025 (`translations.py`) — параллельно с T024 (`api_client.py`)
- T030 и T032 (`e2e/`) — параллельно с T027–T029 и T031 (`tests/`, `api_client.py`)
- T033 (`deploy/README.md`) и T034 (`deploy/site.env.example`) — параллельно друг с другом и с T027–T032

---

## Parallel Example: User Story 4

```bash
Task: "T030 тесты счётчиков в e2e/test_fake_transport.py"
Task: "T033 раздел о трёх настройках и техработах в deploy/README.md"
Task: "T034 закомментированные настройки в deploy/site.env.example"
```

---

## Implementation Strategy

### MVP First (User Story 1)

1. Phase 1 + Phase 2 → настройки читаются, режим виден при старте
2. Phase 3 → каркас с поведением «выключено», умолчание закреплено тестами
3. STOP и проверить: `pytest tests/ -q`, смока — всё как до фичи

### Incremental Delivery

Каждая фаза — отдельный коммит и пуш в `master` (только CI). Реакция на 429/503 (US5) — единственное,
что меняет поведение без настроек (решение дирижёра, FR-016). Включение настроек на машине, переход
юнита на `-w 3 --threads 12`, смена `BACKOFFICE_URL` и выкат на Lambda — **не часть фичи**.

---

## Notes

- Адреса, телефоны (и их хвосты), даты и номера броней в новые строки журнала не пишутся — проверяет T029
- `main.js` не меняется: на 503 `busy` виджет ведёт себя как на нынешний 503 (research.md R6) — если при
  реализации окажется иначе, стоп и вопрос дирижёру
- Строки в `CLAUDE.md` §7 о кеше, потолке и 503 — после фичи, решает дирижёр (CLAUDE.md не трогать)
- Точный код будущего лимита бек-офиса: если не 429/503 — правка FR-020 отдельным решением
