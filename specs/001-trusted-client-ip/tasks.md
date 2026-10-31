---

description: "Task list: доверенный IP гостя из X-Forwarded-For (001)"
---

# Tasks: Доверенный IP гостя из X-Forwarded-For

**Input**: `specs/001-trusted-client-ip/` — spec.md, plan.md, research.md, data-model.md,
contracts/trusted-proxy-count.md, quickstart.md

**Tests**: ОБЯЗАТЕЛЬНЫ — конституция I («каждое изменение поведения идёт с тестом») и FR-011.
Тесты фазы пишутся ДО реализации и сначала падают.

**Organization**: по пользовательским историям spec.md. US1 и US2 обе P1; US1 первая, потому что
закрывает дыру, а US2 фиксирует, что умолчание не сдвинулось.

**Правила исполнителя** (конституция VI, CLAUDE.md): коммит после каждой фазы, `git add` только
явным списком путей, push сразу; перед каждым пушем `python -m pytest tests/ -q` (venv
`~/venvs/central-aparts-site`) и `cd e2e && npx playwright test` зелёные. В тестах и доках — только
адреса 192.0.2.0/24, 198.51.100.0/24, 203.0.113.0/24, 2001:db8::/32 и петля 127.0.0.1 / ::1;
телефоны — только синтетические (`+380000000000`).
Не трогать: `api_client.py`, `wsgi_adapter.py`, `infra/`, `static/`, `templates/`, `e2e/`,
конфиги прокси и юниты (вне репо), `CLAUDE.md`, `.specify/`.

**Имена тестов**: в `tests/test_client_ip.py` имена тестовых функций начинаются с `test_us1_`,
`test_us2_`, `test_us3_` или `test_config_` — на этом держатся команды `-k us1` / `-k us2` /
`-k us3` в разделах «Independent Test».

## Format: `[ID] [P?] [Story] Description`

- **[P]**: можно параллельно (разные файлы, нет зависимостей)
- **[Story]**: US1 / US2 / US3 из spec.md

---

## Phase 1: Setup

**Purpose**: зафиксировать зелёную базу до изменений

- [X] T001 Прогнать `python -m pytest tests/ -q` в venv `~/venvs/central-aparts-site` на чистом `master` и записать число passed в сообщение первого коммита фичи (база для SC-006); изменений в файлах нет

---

## Phase 2: Foundational — настройка TRUSTED_PROXY_COUNT

**Purpose**: разбор настройки в `config.py` — от него зависят все истории

**⚠️ CRITICAL**: без этой фазы истории не начинать

- [X] T002 Создать `tests/test_client_ip.py`: тесты `test_config_*` для `config._parse_trusted_proxy_count` по data-model.md «Настройка» — `None`→`(None, False)`, `""` и `"  "`→`(None, False)`, `"0"`→`(0, False)`, `"1"`→`(1, False)`, `" 2 "`→`(2, False)`, `"-1"`, `"+1"`, `"1.5"`, `"abc"`, `"1 2"`, `"١"` (не-ASCII цифра)→`(None, True)`; убедиться, что падают
- [X] T003 В `config.py` добавить модульную функцию `_parse_trusted_proxy_count(raw: str | None) -> tuple[int | None, bool]` (правило: после `strip()` пусто → `(None, False)`; полное совпадение с `^[0-9]+$` → `(int, False)`; иначе `(None, True)`) и в класс `Config` два поля по образцу соседних: `TRUSTED_PROXY_COUNT` и `TRUSTED_PROXY_COUNT_INVALID`, вычисленные одним вызовом от `os.environ.get("TRUSTED_PROXY_COUNT")`; тесты T002 зелёные
- [X] T004 В `tests/test_client_ip.py` добавить помощники: фикстуру `mode(value, invalid=False)` через `monkeypatch.setattr(Config, "TRUSTED_PROXY_COUNT", value)` и `monkeypatch.setattr(Config, "TRUSTED_PROXY_COUNT_INVALID", invalid)`; и функции-запросы для двух маршрутов — `POST /api/coupon/check` (json `{"phone": "+380000000000"}`) и `GET /api/quote/apt-uuid-a` c `query_string={"check_in": "2030-06-01", "check_out": "2030-06-03", "guests": "2", "coupon_phone": "+380000000000"}` (словарём, чтобы `+` не превратился в пробел) — с параметрами `xff` (строка или `None` = заголовка нет) и `remote_addr` (через `environ_base={"REMOTE_ADDR": …}`), возвращающие адрес, полученный подменным бек-офисом (`mock_client.last_coupon_client_ip` / `mock_client.last_quote["client_ip"]`)

**Checkpoint**: настройка читается; коммит `feat(001): TRUSTED_PROXY_COUNT в config.py` + push

---

## Phase 3: User Story 1 — подделка заголовка не обнуляет лимит (Priority: P1) 🎯 MVP

**Goal**: при N ≥ 1 бек-офис получает N-й элемент справа; при N = 0 — адрес соединения; при нехватке — откат к адресу соединения с одной WARNING-строкой

**Independent Test**: `python -m pytest tests/test_client_ip.py -k us1 -v`

### Tests for User Story 1 ⚠️ (сначала падают)

- [X] T005 [US1] В `tests/test_client_ip.py` тест `test_us1_topology_matrix`, параметризованный по ПОЛНОЙ матрице research.md R7 (5 строк топологий (а), (б), (в), (г)-подделка, (г)-без заголовка × режимы N = 0, 1, 2) × ОБА маршрута (`/api/coupon/check`, `/api/quote/<apt_id>`); ожидаемый адрес и ожидаемый код WARNING (или его отсутствие) — ровно из таблицы R7; столбцы «не задана / неверная» той же таблицы проверяются в T012
- [X] T006 [US1] В `tests/test_client_ip.py` тест `test_us1_lambda_adapter` для топологии (а) через настоящий `wsgi_adapter.lambda_handler`: синтетическое событие Function URL (`requestContext.http.method="POST"`, `requestContext.http.sourceIp="203.0.113.7"`, `rawPath="/api/coupon/check"`, `rawQueryString=""`, `headers={"x-forwarded-for": "198.51.100.23, 203.0.113.7", "content-type": "application/json", "host": "www.example.test"}`, `body='{"phone": "+380000000000"}'`) при N = 1 → бек-офис получает `203.0.113.7`; в режиме по умолчанию → `198.51.100.23` (сегодняшнее поведение); `wsgi_adapter.py` не менять
- [X] T007 [US1] В `tests/test_client_ip.py` тест `test_us1_contract_cases` для ОБОИХ маршрутов по contracts/trusted-proxy-count.md §2, строки 4–13: N=0 игнорирует заголовок; N=2 → `203.0.113.7` из `198.51.100.23, 203.0.113.7, 192.0.2.10`; IPv6 `2001:db8::1` при `REMOTE_ADDR=::1`; пробелы и пустые элементы `198.51.100.23 , , 203.0.113.7 `; откаты `no_header`, `fewer_than_n` (N=2, один элемент), `not_ip` (`unknown` и `203.0.113.7:443`) → адрес соединения
- [X] T008 [US1] В `tests/test_client_ip.py` тест `test_us1_forged_left_same_bucket` (SC-001): N = 1, семь `POST /api/coupon/check` с `xff="<левый_i>, 203.0.113.7"`, где `левый_i` — семь разных адресов из 198.51.100.0/24 → все семь раз бек-офис получает `203.0.113.7`
- [X] T009 [US1] В `tests/test_client_ip.py` тест `test_us1_two_guests_distinct` (SC-005): N = 1, два гостя `xff="203.0.113.7"` и `xff="203.0.113.8"` при одном `remote_addr="127.0.0.1"` → бек-офис получает два разных адреса
- [X] T010 [US1] В `tests/test_client_ip.py` тест `test_us1_fallback_logs_one_warning`: при откатах T007 (`no_header` / `fewer_than_n` / `not_ip`) ровно одна запись уровня WARNING от логгера `app` на запрос, с текстом `client_ip_fallback reason=<код> elements=<число>` и `record.exc_info is None`; при N = 0 и при удачном разборе N ≥ 1 — ни одной записи `client_ip_fallback` (FR-004)

### Implementation for User Story 1

- [X] T011 [US1] В `app.py` переписать `_visitor_ip()` по data-model.md «Адрес гостя»: читать `Config.TRUSTED_PROXY_COUNT` на запрос; `0` → `request.remote_addr or ""`; `N ≥ 1` → `elements = [e.strip() for e in header.split(",") if e.strip()]`, проверка `ipaddress.ip_address(elements[-N])`, при `no_header` / `fewer_than_n` / `not_ip` — `logger.warning("client_ip_fallback reason=%s elements=%d", …)` без `exc_info` и откат к `request.remote_addr or ""`; ветку «по умолчанию» оставить ровно прежним выражением; обновить docstring (без утверждения, что первый элемент ставит CloudFront); вызовы в `quote_api` и `coupon_check_api` не менять; тесты T005–T010 зелёные

**Checkpoint**: US1 работает; `pytest tests/ -q` и смока зелёные; коммит `feat(001): N-й справа из X-Forwarded-For при TRUSTED_PROXY_COUNT` + push

---

## Phase 4: User Story 2 — без настройки ничего не меняется (Priority: P1)

**Goal**: режим по умолчанию байт-в-байт прежний; схема, хост, `remote_addr`, robots/sitemap не меняются ни в каком режиме

**Independent Test**: `python -m pytest tests/test_client_ip.py -k us2 tests/test_coupon_ui.py -v`

### Tests for User Story 2 ⚠️

- [X] T012 [US2] В `tests/test_client_ip.py` тест `test_us2_default_mode` для обоих маршрутов: столбец «не задана / неверная» полной матрицы research.md R7 (все 5 строк топологий) — отдельно для `mode(None)`, для `mode(None, invalid=True)` и для пустой строки в окружении (через `config._parse_trusted_proxy_count("")`); плюс контракт §2, строки 1–3 и 14 (` , 203.0.113.7` → адрес соединения, как сейчас); в режиме по умолчанию ни одной записи `client_ip_fallback`
- [X] T013 [P] [US2] В `tests/test_coupon_ui.py` класс `TestClientIpForwarding` (около строк 106–116): явно закрепить режим по умолчанию (`monkeypatch.setattr(Config, "TRUSTED_PROXY_COUNT", None)`) и оставить прежние утверждения «первый элемент»; в docstring/комментарии указать, что случаи с N — в `tests/test_client_ip.py`
- [X] T014 [US2] В `tests/test_client_ip.py` тест `test_us2_request_untouched` (FR-007/FR-013): для режимов `None`, `0`, `1`, `2`, `invalid` тела `/robots.txt` и `/sitemap.xml` при запросе с `base_url="https://www.example.test"` и подделанными `X-Forwarded-Proto: http`, `X-Forwarded-Host: 198.51.100.23`, `X-Forwarded-For: 198.51.100.23` совпадают с телами без этих заголовков; `app.wsgi_app` не является экземпляром `werkzeug.middleware.proxy_fix.ProxyFix`; в `app.test_request_context(environ_base={"REMOTE_ADDR": "127.0.0.1"}, headers={"X-Forwarded-For": "203.0.113.7"})` при N = 1 вызов `_visitor_ip()` не меняет `request.remote_addr` (остаётся `127.0.0.1`)

### Implementation for User Story 2

- [X] T015 [US2] Если T012–T014 падают — поправить только ветку «по умолчанию» в `_visitor_ip()` в `app.py` (не трогая веток N ≥ 0) до прежнего выражения `request.headers.get("X-Forwarded-For", "").split(",")[0].strip() or request.remote_addr or ""`; ProxyFix и иные обёртки `app.wsgi_app` не добавлять; если всё зелёное сразу — изменений нет

**Checkpoint**: US1 и US2 зелёные вместе; коммит `test(001): режим по умолчанию и robots/sitemap не меняются` + push

---

## Phase 5: User Story 3 — режим виден в журнале, адресов в журнале нет (Priority: P2)

**Goal**: одна INFO-запись о режиме при старте процесса, ERROR при неверной настройке, ни одного адреса в журнале; документация выбора N

**Independent Test**: `python -m pytest tests/test_client_ip.py -k us3 -v`; чтение README выката (вне репо) и `deploy/site.env.example`

### Tests for User Story 3 ⚠️

- [X] T016 [US3] В `tests/test_client_ip.py` тест `test_us3_startup_mode_line`: вызовы `app._log_client_ip_mode()` через `caplog` (логгер `app`) для каждого режима по contracts/trusted-proxy-count.md §3: `None` → одна INFO `client_ip_mode mode=default`; `0` → `mode=ignore_header`; `1` → `mode=trusted_proxies n=1`; `invalid=True` → одна ERROR `client_ip_config_invalid env=TRUSTED_PROXY_COUNT fallback=default` плюс INFO `mode=default`, и сырое значение (например `abc`, выставленное через `monkeypatch.setenv` рядом) не встречается в `caplog.text`
- [X] T017 [US3] В `tests/test_client_ip.py` тест `test_us3_no_addresses_in_log` (SC-003, FR-009): `caplog.set_level(logging.DEBUG)` для всех логгеров; вызвать `_log_client_ip_mode()` во всех режимах, выполнить запросы обоих маршрутов по полной матрице R7 и строкам 1–14 контракта (включая все откаты); утверждать, что ни один из использованных тестовых адресов (и адреса гостя, и адреса соединения) не встречается в `caplog.text`, в тексте нет `Traceback`, а число записей `client_ip_fallback` равно числу запросов-откатов

### Implementation for User Story 3

- [X] T018 [US3] В `app.py` добавить `_log_client_ip_mode()`: при `Config.TRUSTED_PROXY_COUNT_INVALID` — `logger.error("client_ip_config_invalid env=TRUSTED_PROXY_COUNT fallback=default")`; затем одна `logger.info` по режиму (`client_ip_mode mode=default` / `mode=ignore_header` / `mode=trusted_proxies n=%d`); вызвать один раз на уровне модуля после создания `app`; адресов и сырого значения не писать; журнал (обработчики, `basicConfig`) не настраивать; тесты T016–T017 зелёные
- [X] T019 [P] [US3] В README выката (вне репо) в разделе «Дві топології фронту» добавить подраздел про `TRUSTED_PROXY_COUNT` (українською, як і весь файл): значення «не задана» / 0 / N ≥ 1; N = 1 для топологій (Б) і (В) і чому (фронт ставить рівно одне значення); чому на Lambda N не обрано і відкат до адреси з'єднання там не захищає (`wsgi_adapter.py:57-58`, spec.md «Ограничения»); що експеримент із «Після будь-якої зміни топології» лишається обов'язковим; що на коробці без налаштування журналу видно лише WARNING/ERROR, а рядок режиму INFO — ні (research.md R3). Переписати абзац «І чесна засторога…» (зараз каже, що `_visitor_ip()` бере перший елемент) і згадку в «Що ще не зроблено» (про `_visitor_ip()` під Cloudflare) під нову поведінку
- [X] T020 [P] [US3] В `deploy/site.env.example` добавить блок `TRUSTED_PROXY_COUNT=1` с комментарием по-украински: что значит значение, что 1 — для коробки за Caddy (и за Caddy → nginx), что пусто = поведение как на Lambda сегодня (первый элемент), 0 = без прокси; ссылка на раздел README выката (вне репо)

**Checkpoint**: все истории зелёные; коммит `feat(001): журнал режима разбора + docs deploy` + push

---

## Phase 6: Polish & Cross-Cutting

- [X] T021 Прогнать `python -m pytest tests/ -q` — всё зелёное, число passed ≥ базы T001 + новые тесты (SC-006)
- [X] T022 Прогнать смоку `cd e2e && npx playwright test` (fake) — зелёная, файлы `e2e/` не изменены (SC-006)
- [X] T023 Проверить diff фичи грепом: `git diff origin/master -- app.py config.py tests/ deploy/site.env.example` не содержит IPv4-адресов вне 192.0.2.0/24, 198.51.100.0/24, 203.0.113.0/24, 127.0.0.1 и IPv6 вне 2001:db8::/32 и ::1 (прежний адрес в существующем тесте допустим, если строка не менялась), не содержит секретов; `git diff --stat` затрагивает только файлы из plan.md «Source Code»
- [X] T024 Пройти `specs/001-trusted-client-ip/quickstart.md` шаги 1–4 и сверить вывод с ожидаемым; отчёт дирижёру — с числами тестов, без адресов

---

## Dependencies & Execution Order

### Phase Dependencies

- Phase 1 → Phase 2 → Phase 3 (US1) → Phase 4 (US2) → Phase 5 (US3) → Phase 6
- US1, US2 и US3 правят один `app.py` и один `tests/test_client_ip.py` — фазы идут последовательно, не параллельно

### Within Each Story

- Тесты фазы пишутся первыми и падают, затем реализация
- T011 зависит от T003, T004; T015 — от T011; T018 — от T003
- T019, T020 не зависят от кода и могут идти параллельно с T016–T018

### Parallel Opportunities

- T013 (`tests/test_coupon_ui.py`) — параллельно с T012/T014 (другой файл)
- T019 (README выката (вне репо)) и T020 (`deploy/site.env.example`) — параллельно друг с другом и с T016–T018

---

## Parallel Example: User Story 3

```bash
Task: "T019 раздел TRUSTED_PROXY_COUNT в README выката (вне репо)"
Task: "T020 блок TRUSTED_PROXY_COUNT=1 в deploy/site.env.example"
```

---

## Implementation Strategy

### MVP First (User Story 1)

1. Phase 1 + Phase 2 → настройка читается
2. Phase 3 → дыра закрыта при N ≥ 1; без настройки поведение то же (ветка по умолчанию не трогается)
3. STOP и проверить: `pytest tests/ -q`, смока

### Incremental Delivery

Каждая фаза — отдельный коммит и пуш в `master` (только CI). Выкат в прод (Lambda)
и установка на коробку — **не часть фичи**, только с «да» владельца.

---

## Notes

- Адреса и значения настройки в журнал не пишутся нигде — проверяет T017
- `wsgi_adapter.py:57-58` (REMOTE_ADDR = первый элемент на Lambda) — известное ограничение, не
  править (spec.md «Ограничения»)
- Настройку журнала коробки (чтобы INFO был виден) не делать — вне фичи (research.md R3)
- Устаревшие комментарии в конфигах nginx и Caddy (вне репо) про «первый элемент» — вне
  фичи; сообщить дирижёру, не править

## Phase 7: Convergence

- [X] T025 В `tests/test_client_ip.py` тест `test_us2_request_untouched_all_topologies`: тела `/robots.txt` и `/sitemap.xml` для каждой строки топологий research.md R7 (её X-Forwarded-For и REMOTE_ADDR) × все режимы (не задана, 0, 1, 2, неверная) совпадают с базой режима по умолчанию без заголовков; для топологии (а) — дополнительно через `wsgi_adapter.lambda_handler` (ссылки `https://` от заголовка `host`, одинаковые во всех режимах) per SC-004 (partial)
- [X] T026 В `tests/test_client_ip.py` тест `test_us1_empty_connection_address`: при пустом REMOTE_ADDR бек-офис получает пустую строку в режиме по умолчанию без заголовка, при N = 0 и при откате N ≥ 1 (`no_header`), на обоих маршрутах per spec.md Edge Cases «адрес соединения тоже отсутствует» (partial)
