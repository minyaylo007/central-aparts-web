# aparts-web — Public Booking Frontend

Public booking site for Central Square Apartments (Chernivtsi). Flask + Jinja2 + requests. All data
goes through `api_client.py` to the backoffice public API (`cv.central-aparts.store/api/public`);
no database here. UI language: Ukrainian (`translations.py`). Branch: **master**.

Ця мапа — коротка навмисно: лише те, що потрібно в КОЖНІЙ правці. Смока — `e2e/README.md`.

## Внутрішня частина (лише на машині проєкту)

Викат і відкат на прод, стан прод/AWS/DNS, Caddy, рамка прогону машини, докладні патерни й
історія рішень (`docs/PATTERNS.md`, `docs/DELIVERY.md`, `deploy/README.md`) — у приватному
ops-клоні `~/repos/central-aparts-site-ops`. Його `CLAUDE.md` підключено імпортом нижче; якщо
імпорт не підвантажився (нема клону або імпорт не схвалено) — **перед викатом, роботою з
продом або з `docs/…` прочитати `~/repos/central-aparts-site-ops/CLAUDE.md` вручну**. У
публічному клоні цього файлу нема — для правки коду, тестів і смоки він не потрібен.

@~/repos/central-aparts-site-ops/CLAUDE.md

## Команди

```bash
python -m pytest -q --quick   # critical + змінені в гілці тести (~7 с); повний набір: без --quick
cd e2e && npx playwright test --grep @critical  # смока за замовчуванням (@critical)
cd e2e && npx playwright test                   # повна смока (desktop + mobile), fake-бек-офіс, без мережі
scripts/ci-local.sh  # локальні ворота = рамка CI (ruff, повний pytest, повна смока fake); підсумок — останній рядок
#   на машині проєкту — у її рамці: див. ops-CLAUDE.md (ci-local-ace.sh)
```

## Правила, які діють у кожній правці

- **Дані лише через `api_client.py`** (клас `BackofficeClient`, singleton `get_client()`, `set_client()` для тестів). Ніяких прямих звернень до БД чи сторонніх URL у маршрутах.
- **Усі рядки для гостя — в `translations.py`** (`t(key, lang)`, мови `ua`/`en`; у сесії `"ua"`, локаль Flatpickr — `uk`). У шаблонах тексту не хардкодити.
- **Гроші рахує лише сервер.** Ціни, збір, купон (−500), підсумок групи — числа з відповіді бек-офісу; JS нічого не складає. Константа туристичного збору 48 у `main.js` і потік `/book` → Монобанк — не чіпати без прямого завдання.
- **У `<script>` — лише `| tojson` або `| int`**, ніколи `{{ value }}`.
- **Помилка бек-офісу → `public_error.html` з перекладеним текстом**, не traceback. 404 від API → `abort(404)`. 429/503 → `BackofficeUnavailable` (JSON-ручки 503 `busy`, `/book` — flash з хвилинами).
- **Секрети й ПД — ніде:** ключ і URL лише з env (`config.py`); у журнал — без телефонів, адрес і URL з query (`_log_backoffice_error(event, e)`, не `exc_info=True`). Телефони в тестах і документах — лише код оператора `00`.
- **Ідемпотентність броні:** кожен submit — новий UUID у прихованому `idempotency_key`; `/book` і `/book-group` ідуть через `_post_idempotent` (тіло серіалізується один раз, повтори на 409 in_progress/503 за `Retry-After`, mismatch не повторюється). Деталі — внутрішні нотатки (`docs/PATTERNS.md` §7 в ops).
- **CSS першого екрана:** після правки `styles.css`, шаблонів або класів у `main.js` запускати `scripts/build_css.sh` — інакше `static/critical/*.css` і `bootstrap-lite.css` застаріють (`tests/test_critical_css.py` упаде). Bootstrap з CDN не повертати; деталі — внутрішні нотатки (`docs/PATTERNS.md`, «Перший екран», в ops).
- **Статика:** `static_url('styles.css')` дає `?v=<git_short>`; після `git pull` і перед рестартом обов'язковий `scripts/stamp_build.py`, інакше кеш не зсунеться. `/health` віддає коміт і `no-store`.
- **Стиль:** `snake_case`/`PascalCase`; CSS лише через токени `styles.css :root` (`--clay`, `--greige`, `--cream`, `--espresso`, `--sage`; `--oak` — лише декор), жодного hex у нових стилях, старий рожевий `#C8365E` не повертати; шрифти Cormorant Garamond + Inter, не міняти. Редизайн 10.2026 (§26 в кінці `styles.css`, перебиває старі секції): білі поверхні `--surface`, `--sage-wash`, радіуси `--r-md`/`--r-xl`/`--r-pill`, тіні `--shadow-card`/`--shadow-panel`, `--hero-scrim`; ціни — Inter, заголовки — Cormorant. Нові правила дописувати в §26.
- **Config:** клас `Config` у `config.py`, усе з env; `GTM_ID` і `FB_PIXEL_ID` — константи.
- **Lambda-адаптер `wsgi_adapter.py` не ламати** (відкат на AWS); логіку Lambda в `app.py` не додавати.

## Де що лежить

```
app.py            Flask, усі маршрути       api_client.py   усі HTTP-виклики бек-офісу
config.py         env → Config              translations.py рядки ua/en
wsgi.py           gunicorn + журнал         wsgi_adapter.py Lambda (лише відкат)
templates/        public_base / home / apartment / confirmed / error
static/           styles.css (Japandi-токени), main.js (календар, віджет, купон, група)
tests/            pytest                    e2e/            Playwright-смока, fake_backoffice.py
specs/, .specify/ Spec Kit — лише для фіч > 1 дня або про гроші/дані/вхід
```

Потік: Browser → Flask route → `api_client.py` → backoffice `/api/public` → Jinja2.

## Тести — коротко

- `pytest.ini`: маркер `critical` = стереже гостя, гроші або доступність. `--quick` (`conftest.py`) бере `critical` + файли, змінені в гілці. Новий тест без маркера — якщо це форма тексту чи розмітка.
- Мокати через `monkeypatch.setattr` на `api_client` або `set_client(...)`; env — через `monkeypatch`.
- Смока: gunicorn на `127.0.0.1:8010` піднімає сам Playwright; `SMOKE_BACKEND=fake` (без мережі) або `live` (лише читання). Браузери — `npm run browsers` (свій каталог, `e2e/browsers-env.cjs`), НЕ голий `npx playwright install`. Проєкт `mobile` ганяє лише спеку 05 (`testMatch`), решта — тільки desktop. Один тест на гілку поведінки, не на варіант тексту.
- Перед пушем — повний pytest; смока `@critical` — коли правка торкається віджета, форми, календаря або шаблонів.

## Прод

Прод — gunicorn за зворотним проксі на машині проєкту; `infra/template.yaml` і `wsgi_adapter.py` —
лише відкат на AWS Lambda. Push у master прод не викочує. Стан і порядок викату — лише в ops-CLAUDE.md.

## Оновлення цього файлу

Сюди — лише правило, без якого наступна правка зламається. Знахідки, патерни, числа замірів,
історія — у `docs/PATTERNS.md` ops-клону (відповідний розділ). Тримати цей файл до 120 рядків.
Цей файл публічний: жодних шляхів машини, IP, імен сервера, ідентифікаторів AWS, внутрішніх
служб і людей — усе таке пишеться в ops-CLAUDE.md.
