# Implementation Plan: Доверенный IP гостя из X-Forwarded-For

**Branch**: нет (работа в `master`, папка `specs/001-trusted-client-ip/`) | **Date**: 2026-09-11 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/001-trusted-client-ip/spec.md`

## Summary

`_visitor_ip()` в `app.py` сейчас отдаёт бек-офису первый (подделываемый) элемент
X-Forwarded-For. Фича вводит настройку окружения `TRUSTED_PROXY_COUNT` (читается в `config.py`)
и три режима разбора: «не задана» — как сейчас; 0 — заголовок игнорируется; N ≥ 1 — N-й
непустой элемент справа, проверенный как IPv4/IPv6, с откатом к адресу соединения и одной
WARNING-строкой без адресов. Разбор — своя функция в `app.py`, без ProxyFix: схема, хост и
`request.remote_addr` не трогаются, поэтому ссылки `/robots.txt` и `/sitemap.xml` не меняются.
Прод (Lambda) без настройки не меняется; N на Lambda — вне фичи. Обоснования — [research.md](research.md).

## Technical Context

**Language/Version**: Python 3.12.3 (venv `~/venvs/central-aparts-site`); используются только
`ipaddress`, `logging`, `os` из стандартной библиотеки

**Primary Dependencies**: Flask 3.1.3 / Werkzeug 3.1.8 (уже есть), gunicorn 26.2.0 (коробка) —
новых нет

**Storage**: N/A (адрес гостя нигде не хранится)

**Testing**: pytest + Flask test client (`tests/`), Playwright-смока `e2e/` (без изменений, только
прогон)

**Target Platform**: Linux, gunicorn за Caddy (коробка, топологии (б), (в)); AWS Lambda через
`wsgi_adapter.py` (топология (а), не меняется); прямой запуск (г)

**Project Type**: web-service (Flask, серверный рендеринг + JSON-прокси к бек-офису)

**Performance Goals**: разбор — O(число элементов заголовка) на запрос, в пределах двух вызовов;
заметного влияния на время ответа нет

**Constraints**: ни одного адреса гостя в журнале (FR-009); не больше одной WARNING-строки на
запрос; без настройки — байт-в-байт прежнее поведение (FR-002); схема/хост запроса не меняются
(FR-007, FR-013)

**Scale/Scope**: 2 файла кода (`config.py`, `app.py`), 2 файла тестов, 2 файла документации

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Инвариант | Проверка | Итог |
|---|---|---|
| I. Тесты до пуша | Каждое изменение поведения — с pytest (матрица (а)–(г) × {не задана, 0, 1, 2, неверное}, журнал без адресов, robots/sitemap); перед пушем `python -m pytest tests/ -q` и `cd e2e && npx playwright test` (fake) — зелёные | ✅ |
| II. Секреты и ПД | Адреса гостей не пишутся в журнал (FR-009, отдельный тест); в тестах и доках — только 192.0.2.0/24, 198.51.100.0/24, 203.0.113.0/24, 2001:db8::/32; значение настройки в ERROR-строке не печатается | ✅ |
| III. Никаких новых зависимостей от AWS | Код AWS не трогает; `wsgi_adapter.py` и `infra/` не меняются; тест (а) вызывает существующий `wsgi_adapter.lambda_handler` синтетическим событием, без SDK | ✅ |
| IV. Сеть | Новых слушающих процессов и портов нет; тесты — Flask test client; смока — как всегда на 127.0.0.1:8004 | ✅ |
| V. Чужое не трогать | чужие проекты, конфиг общего прокси, его admin-порт — не затрагиваются; конфиги прокси и юниты (вне репо) не меняются | ✅ |
| VI. Git | Исполнитель: коммит после каждой фазы tasks.md, `git add` явным списком, push сразу в `master` (только CI); выкат в прод — не часть фичи | ✅ |
| VII. Простота и границы | Только то, что в spec.md; новых зависимостей, сервисов, портов нет (`ipaddress` — стандартная библиотека); ProxyFix сознательно не берём | ✅ |

**Повторная проверка после Phase 1**: дизайн (data-model, контракт, quickstart) не добавил ни
зависимостей, ни портов, ни касаний прод-выката — все семь пунктов ✅, Complexity Tracking пуст.

## Project Structure

### Documentation (this feature)

```text
specs/001-trusted-client-ip/
├── spec.md
├── plan.md              # этот файл
├── research.md          # Phase 0: решения и обоснования (ProxyFix, откат, журнал…)
├── data-model.md        # Phase 1: настройка, режим, адрес, коды причин
├── quickstart.md        # Phase 1: как проверить
├── contracts/
│   └── trusted-proxy-count.md   # контракт настройки, _visitor_ip и журнальных строк
├── checklists/
│   └── requirements.md
└── tasks.md             # /speckit-tasks
```

### Source Code (repository root)

```text
config.py                   # + TRUSTED_PROXY_COUNT, TRUSTED_PROXY_COUNT_INVALID, _parse_trusted_proxy_count()
app.py                      # _visitor_ip() по режиму; _log_client_ip_mode() при импорте;
                            #   маршруты /api/quote/<apt_id> и /api/coupon/check — без изменений вызова
tests/
├── test_client_ip.py       # НОВЫЙ: матрица (а)–(г) × режимы, откат, журнал без адресов,
│                           #   неверная настройка, robots/sitemap, отсутствие ProxyFix
└── test_coupon_ui.py       # TestClientIpForwarding: закрепить режим по умолчанию явно
deploy/
├── README.md               # раздел про TRUSTED_PROXY_COUNT; поправить «чесну засторогу»
└── site.env.example        # TRUSTED_PROXY_COUNT=1 с объяснением
```

Не меняются: `api_client.py`, `wsgi_adapter.py`, `infra/`, `static/`, `templates/`, `e2e/`,
конфиги прокси и юниты (вне репо), `translations.py`, `CLAUDE.md`.

**Structure Decision**: одиночный Flask-проект в корне репозитория; фича укладывается в
существующие `config.py`, `app.py`, `tests/`, `deploy/` без новых модулей в коде.

### Устройство изменения (для исполнителя)

- `config.py`: модульная функция `_parse_trusted_proxy_count(raw: str | None) -> tuple[int | None, bool]`
  — `(None, False)` для не задана/пусто, `(n, False)` для `^[0-9]+$` после `strip()`, `(None, True)`
  для всего остального (включая `-1`, `1.5`, `abc`, `+1`). В классе `Config` — два поля по
  образцу соседних: `TRUSTED_PROXY_COUNT` и `TRUSTED_PROXY_COUNT_INVALID`, вычисляемые из
  `os.environ.get("TRUSTED_PROXY_COUNT")` одним вызовом.
- `app.py`: `_visitor_ip()` читает `Config.TRUSTED_PROXY_COUNT` **на запрос** (чтобы тесты могли
  подменять через `monkeypatch.setattr(Config, ...)`); ветки — по контракту
  [contracts/trusted-proxy-count.md](contracts/trusted-proxy-count.md). `_log_client_ip_mode()`
  пишет INFO (и ERROR при неверной настройке) и вызывается один раз при импорте модуля.
- Вызовы в `quote_api` и `coupon_check_api` остаются как есть (`client_ip=_visitor_ip()`), по
  одному на запрос — отсюда «не больше одной WARNING-строки на запрос».

### Вне плана (решает дирижёр)

- Строка в `CLAUDE.md` §7 про `TRUSTED_PROXY_COUNT` (CLAUDE.md §10 просит её после фичи, но
  спецификатор и границы фичи CLAUDE.md не трогают).
- Устаревшие комментарии про «первый элемент» в конфиге nginx (вне репо) и
  рядом в конфиге Caddy (вне репо) — вне границ фичи.

## Complexity Tracking

Нарушений Constitution Check нет — таблица пуста.
