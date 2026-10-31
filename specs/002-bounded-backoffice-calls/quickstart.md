# Quickstart: проверка фичи «Бережные обращения к бек-офису»

Пункты 1–4 — без сети и без бек-офиса. Пункт 5 (замер) — только против подмены на
`127.0.0.1:8010`; на сервис `8004` и на бойовой бек-офис нагрузку не давать.

## Предусловия

```bash
cd ~/repos/central-aparts-site          # или своё рабочее дерево
source ~/venvs/central-aparts-site/bin/activate
```

## 1. Тесты фичи

```bash
python -m pytest tests/test_limits_config.py tests/test_quote_cache.py \
  tests/test_backoffice_slots.py tests/test_backoffice_unavailable.py tests/test_limits_logging.py -v
python -m pytest e2e/test_fake_transport.py -v
```

Ожидается — зелёные случаи по [contracts/backoffice-limits.md](contracts/backoffice-limits.md):
- без настроек — каждая котировка идёт в бек-офис, мест нет (US1);
- кеш: 100 одинаковых → 1 обращение; купон, 400/404, ошибки, 429/503 — мимо кеша; просроченное при
  сбое не отдаётся; 2001-й ключ вытесняет давний (US2);
- потолок: пик одновременных ≤ N под 30 потоками, отказ ≤ W + 0,5 с, попадания кеша и бронь мест не
  берут (US3);
- 503 + `Retry-After: 300` + HTML (и JSON) на всех путях — §2 контракта; бронь — flash «за 5
  хвилин» с датами и гостями (US5);
- журнал — строки §4, частота ≤ 1/с, ни одного тестового телефона, адреса, даты, номера брони (US4).

## 2. Прежние тесты и линтер

```bash
ruff check .
python -m pytest tests/ -q
```

## 3. Смока (конституция I)

```bash
cd e2e && npx playwright test          # SMOKE_BACKEND=fake; перед этим ss -ltnp | grep :8010 — порт свободен
```

Ожидается: зелёная, без правок в `e2e/tests/`.

## 4. Строки режима при старте (вручную)

Под gunicorn сервиса (`wsgi.py`, `attach_app_logging`) INFO видны в журнале сами; при запуске
через `python -c` обработчиков нет — для INFO он ставится в команде. Переменные окружения в
рабочем дереве сессии задавать скрипт-файлом (харнесс отклоняет `VAR=… команда`), например
`$CLAUDE_JOB_DIR/tmp/mode.sh` с `export QUOTE_CACHE_TTL=30` и т. п. и запуском:

```bash
python -c 'import logging; logging.basicConfig(level=logging.INFO); import app' 2>&1 | grep -E 'quote_cache_mode|backoffice_'
```

Ожидается: без переменных — `mode=off` дважды; с `QUOTE_CACHE_TTL=30`, `BACKOFFICE_MAX_CONCURRENT=3`
— `mode=on ttl=30 max_entries=2000` и `mode=on n=3 wait_s=2.0`; с `BACKOFFICE_MAX_CONCURRENT=abc` —
ERROR `backoffice_limits_config_invalid env=BACKOFFICE_MAX_CONCURRENT fallback=off` и `mode=off`,
значения `abc` нигде нет.

## 5. Замер до/после на подмене (SC-005)

Та же подготовка, что во внутренней инструкции выката, «Замір із затримкою бек-офісу»: oha 1.16.0
(бинарник `oha-linux-amd64` из релизов oha), `fake_wsgi` на `127.0.0.1:8010`.

1. `ss -ltnp | grep :8010` — порт свободен.
2. Скрипт-файл запуска с `export SMOKE_FAKE_DELAY_MS=350`, `export SMOKE_FAKE_REAL_CLIENT=1`,
   `export SMOKE_FAKE_COUNT=1` и командой
   `python -m gunicorn --chdir <repo> --pythonpath <repo>/e2e -w 3 --threads 12 --timeout 60 -b 127.0.0.1:8010 --no-control-socket fake_wsgi:application`.
   Для «после» — в тот же файл `export QUOTE_CACHE_TTL=30`, `export BACKOFFICE_MAX_CONCURRENT=<N>`
   (для замера — 2: учебное значение, чтобы потолок заведомо срабатывал; для машины при
   `-w 3 --threads 12` потолок не задаётся, research.md R12), `export BACKOFFICE_SLOT_WAIT=2`.
3. Проверить содержимое: `total` в `/api/quote/<APT_A>?…` и каталог на `/`.
4. Сценарий A, один ключ:
   `oha -z 60s -c 50 -w 'http://127.0.0.1:8010/api/quote/<APT_A>?check_in=<+30 дней>&check_out=<+32>&guests=2'`.
5. Сценарий B, разные ключи (100 комбинаций, выезд всегда позже заезда; месяц — через 1–2 месяца
   от даты замера):
   `oha -z 60s -c 50 -w --rand-regex-url 'http://127.0.0.1:8010/api/quote/<APT_A>\?check_in=<ГГГГ-ММ>-1[0-9]&check_out=<ГГГГ-ММ>-2[0-9]&guests=2'`.
6. После каждого сценария — `curl -s http://127.0.0.1:8010/__fake__/state` 10–15 раз (новые
   соединения попадают в разные процессы): собрать `transport` по трём `pid`, сложить `calls`,
   записать `peak` каждого.
7. Погасить сервер по PID майстера (`ss -ltnp` → `kill -TERM <pid>`), убедиться, что `8010` свободен;
   перезапуск между «до» и «после» обнуляет счётчики.

Ожидается «после» по сравнению с «до»:
- `peak` каждого процесса ≤ N (сценарии A и B);
- сценарий A: сумма `calls` меньше не менее чем в 10 раз (кеш: ≈ по одному промаху на процесс за 30 с);
- сценарий B: p95 котировки ≤ W + 0,5 + 0,35 с; есть доля 503 `busy` — это и есть защита бек-офиса.
Таблица (p95, доля 503, `calls`, `peak`) — в `deploy/README.md` рядом с таблицей замера.

## 6. Что этим НЕ проверяется

Ёмкость настоящего бек-офиса на машине (его собственный замер — research.md R12, здесь не перепроверяется), настоящий режим техработ
(`MAINTENANCE=1` в бек-офисе) и включение настроек на машине — отдельные задания.
