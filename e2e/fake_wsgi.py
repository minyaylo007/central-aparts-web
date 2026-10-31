"""
gunicorn-вхід смоки в режимі SMOKE_BACKEND=fake: СПРАВЖНІЙ Flask-застосунок сайту
(app.py, шаблони, static/main.js) + підмінний бек-офіс замість HTTP.

    python -m gunicorn --chdir <repo> --pythonpath <repo>/e2e \
        -w 1 -b 127.0.0.1:8010 --no-control-socket fake_wsgi:application

Запускає його playwright.config.ts (webServer) — руками рідко треба.
Тільки для смоки й заміру навантаження: на коробці й у Lambda не використовується
(там wsgi.py / wsgi_adapter.py). Для смоки -w 1 обовʼязково — стан підміни живе в
памʼяті одного процесу.

Замір навантаження (deploy/README.md) вмикає змінні — див. fake_transport.py:
SMOKE_FAKE_DELAY_MS (затримка кожного звернення до бек-офісу), SMOKE_FAKE_REAL_CLIENT=1
(справжній BackofficeClient з TTL-кешем поверх підміни) і SMOKE_FAKE_COUNT=1 (разом із
попередньою: лічильники звернень і піку в /__fake__/state → "transport", з pid процесу —
при -w 3 у кожного воркера свої). Не задані — усе як раніше, відповідь /__fake__/state
та сама.
"""
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from flask import jsonify  # noqa: E402

from api_client import set_client  # noqa: E402
from app import app as application  # noqa: E402
from fake_backoffice import FakeBackofficeClient  # noqa: E402
from fake_transport import DelayedBackoffice, real_client_over  # noqa: E402

def _build_client(environ, backend):
    """→ (клієнт для set_client, сесія з лічильниками або None). Без
    SMOKE_FAKE_REAL_CLIENT=1 — сама підміна (смока), SMOKE_FAKE_COUNT без неї ігнорується."""
    if environ.get("SMOKE_FAKE_REAL_CLIENT") != "1":
        return backend, None
    count = environ.get("SMOKE_FAKE_COUNT") == "1"
    client = real_client_over(backend, count=count)
    return client, (client._session if count else None)


def _state_payload(state: dict, session) -> dict:
    """Відповідь /__fake__/state: без лічильників — стан підміни як є."""
    if session is None:
        return state
    return {**state, "transport": {"pid": os.getpid(), **session.counters()}}


fake = FakeBackofficeClient()
_delay_ms = int(os.environ.get("SMOKE_FAKE_DELAY_MS") or 0)
_backend = DelayedBackoffice(fake, _delay_ms) if _delay_ms > 0 else fake
_client, _counting = _build_client(os.environ, _backend)
set_client(_client)


@application.get("/__fake__/state")
def fake_state():
    """Що сайт передав у підмінний бек-офіс: остання бронь, котирування, купон.
    Існує ЛИШЕ в цьому процесі смоки — у app.py такої ручки нема."""
    resp = jsonify(_state_payload(fake.state(), _counting))
    resp.headers["Cache-Control"] = "no-store"
    return resp


__all__ = ["application"]
