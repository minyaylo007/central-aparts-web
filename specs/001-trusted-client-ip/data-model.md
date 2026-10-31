# Data Model: Доверенный IP гостя из X-Forwarded-For

Хранимых данных у фичи нет. Ниже — значения, которые живут в памяти процесса или запроса.

## Настройка `TRUSTED_PROXY_COUNT`

| Сырое значение окружения (после `strip()`) | `Config.TRUSTED_PROXY_COUNT` | `Config.TRUSTED_PROXY_COUNT_INVALID` | Режим |
|---|---|---|---|
| не задано | `None` | `False` | по умолчанию |
| `""` (пусто или одни пробелы) | `None` | `False` | по умолчанию |
| `0` | `0` | `False` | заголовок игнорируется |
| `1`, `2`, … (только цифры, `^[0-9]+$`) | `int` | `False` | N доверенных прокси |
| всё прочее: `-1`, `+1`, `1.5`, `abc`, `1 2` | `None` | `True` | по умолчанию + ERROR при старте |

- Читается один раз при импорте `config.py`, до перезапуска не меняется.
- Значение настройки не журналируется ни при каком исходе (в INFO пишется уже разобранное N).

## Режим разбора

`default` | `ignore_header` | `trusted_proxies(n)`. Выводится из настройки. `_visitor_ip()` читает
`Config.TRUSTED_PROXY_COUNT` на каждый запрос, поэтому тесты подменяют режим через `monkeypatch`.

## Цепочка X-Forwarded-For (вход, на запрос)

- Источник — заголовок запроса `X-Forwarded-For`. Несколько заголовков сервер склеивает через
  запятую.
- Для режима `trusted_proxies`: `elements = [e.strip() for e in header.split(",") if e.strip()]`.
- Для режима `default` — ровно прежнее выражение: `header.split(",")[0].strip()` (без
  отбрасывания пустых, без проверки). Прежнее поведение сохраняется байт в байт.

## Адрес гостя (выход, на запрос)

| Режим | Условие | Результат | Журнал |
|---|---|---|---|
| default | первый элемент непуст | первый элемент как есть | — |
| default | первый элемент пуст / заголовка нет | `request.remote_addr or ""` | — |
| ignore_header | всегда | `request.remote_addr or ""` | — |
| trusted_proxies(n) | `len(elements) == 0` | `request.remote_addr or ""` | WARNING `reason=no_header elements=0` |
| trusted_proxies(n) | `0 < len(elements) < n` | `request.remote_addr or ""` | WARNING `reason=fewer_than_n elements=<len>` |
| trusted_proxies(n) | `elements[-n]` не проходит `ipaddress.ip_address` | `request.remote_addr or ""` | WARNING `reason=not_ip elements=<len>` |
| trusted_proxies(n) | иначе | `elements[-n]` | — |

- Возвращаемое значение — строка (возможно пустая); в журнал не пишется никогда.
- Пустая строка → `api_client` не ставит исходящий заголовок (поведение `api_client.py` не
  меняется).

## Код причины отката

`no_header` | `fewer_than_n` | `not_ip` — закрытый список, только в WARNING-строке.
