# Quickstart: проверка фичи «Доверенный IP гостя»

Всё ниже — без сети и без бек-офиса: подменный клиент из `tests/conftest.py` и смока в режиме
fake. Никаких новых портов; смока сама поднимает gunicorn на `127.0.0.1:8004`.

## Предусловия

```bash
cd ~/repos/central-aparts-site          # или своё рабочее дерево
source ~/venvs/central-aparts-site/bin/activate
```

## 1. Тесты фичи

```bash
python -m pytest tests/test_client_ip.py -v
```

Ожидается: зелёные случаи по контракту
([contracts/trusted-proxy-count.md](contracts/trusted-proxy-count.md), таблица §2):
- матрица (а)–(г) × {не задана, 0, 1, 2, неверная} для `/api/coupon/check` и `/api/quote/<apt_id>`;
- топология (а) — и через Flask test client, и через `wsgi_adapter.lambda_handler`;
- откаты `no_header` / `fewer_than_n` / `not_ip` — ровно одна WARNING-строка на запрос;
- журнал старта и откатов — ни одного тестового адреса, ни одного трейсбека;
- `/robots.txt` и `/sitemap.xml` одинаковы при любом режиме; `app.wsgi_app` не ProxyFix.

## 2. Прежние тесты — режим по умолчанию не сломан

```bash
python -m pytest tests/ -q
```

Ожидается: всё зелёное, включая `tests/test_coupon_ui.py::TestClientIpForwarding`.

## 3. Смока (конституция I)

```bash
cd e2e && npx playwright test          # SMOKE_BACKEND=fake по умолчанию
```

Ожидается: зелёная, без правок в `e2e/`.

## 4. Строка режима при старте (по желанию, вручную)

Журнал в проекте не настраивается, и без обработчика Python печатает только WARNING и выше.
Поэтому для просмотра INFO обработчик ставится прямо в команде:

```bash
TRUSTED_PROXY_COUNT=1   python -c 'import logging; logging.basicConfig(level=logging.INFO); import app' 2>&1 | grep client_ip_
TRUSTED_PROXY_COUNT=abc python -c 'import logging; logging.basicConfig(level=logging.INFO); import app' 2>&1 | grep client_ip_
python -c 'import logging; logging.basicConfig(level=logging.INFO); import app' 2>&1 | grep client_ip_
```

Ожидается соответственно: `client_ip_mode mode=trusted_proxies n=1`; ERROR
`client_ip_config_invalid …` плюс `client_ip_mode mode=default`; `client_ip_mode mode=default`.
Ни в одной строке нет адресов и значения `abc`.

На коробке под gunicorn без настройки журнала видны только WARNING/ERROR (research.md R3).

## 5. Что этим НЕ проверяется

Живая цепочка (Caddy/nginx на коробке, CloudFront на Lambda). Живой эксперимент «7 запросов с
подделкой → 5 × 422, затем 429» — из README выката (вне репо), при установке коробки и только с
разрешения владельца; на Lambda N не задаётся (spec.md → «Ограничения»).
