"""
Самоперевірка підміни для заміру навантаження (e2e/fake_transport.py). Не входить
у tests/ — це обладнання e2e/, не застосунок. Запуск з кореня репо:

    python -m pytest e2e/test_fake_transport.py -q

13.10.2026: один тест на гілку — 15 → 10.
"""
import os
import sys
import time

E2E = os.path.dirname(os.path.abspath(__file__))
for p in (os.path.dirname(E2E), E2E):
    if p not in sys.path:
        sys.path.insert(0, p)

import threading  # noqa: E402

import pytest  # noqa: E402

import api_client  # noqa: E402
from fake_backoffice import APT_A, APT_B, FakeBackofficeClient  # noqa: E402
from fake_transport import DelayedBackoffice, real_client_over  # noqa: E402

# fake_wsgi при імпорті вішає ручку /__fake__/state на застосунок і підставляє підміну
# в api_client. Імпорт — тут, під час збирання тестів (Flask не дає додавати ручки
# після першого запиту), а клієнт повертається як був.
_prev_client = api_client._client
import fake_wsgi  # noqa: E402
api_client.set_client(_prev_client)


class _Counting:
    """Рахує, скільки разів клієнт реально дійшов до «бек-офісу»."""

    def __init__(self, inner):
        self.inner, self.calls = inner, []

    def __getattr__(self, name):
        attr = getattr(self.inner, name)

        def counted(*a, **kw):
            self.calls.append(name)
            return attr(*a, **kw)
        return counted


@pytest.fixture(autouse=True)
def _clean_cache():
    api_client._ttl_cache.clear()
    yield
    api_client._ttl_cache.clear()


def test_real_client_answers_like_fake():
    fake = FakeBackofficeClient()
    client = real_client_over(fake)
    assert client.list_apartments("ua") == fake.list_apartments("ua")
    assert client.get_apartment(APT_A, "en") == fake.get_apartment(APT_A, "en")
    assert client.get_apartment("no-such-apt") is None                 # 404 → None
    assert client.get_availability(APT_A) == fake.get_availability(APT_A)
    assert client.get_rates(APT_A) == fake.get_rates(APT_A)
    assert client.get_quote(APT_A, "bad", "dates") is None             # 400 → None
    # 11.09 перший замір ішов із BACKOFFICE_URL=…/unused: усе 404, головна порожня, але 200
    client.base = "http://127.0.0.1:9/some/prefix"
    assert client.list_apartments("ua") == fake.list_apartments("ua")
    assert client.get_quote(APT_A, "2099-01-01", "2099-01-03", 2)["nights"] == 2


def test_real_client_caches_reads_but_not_quote():
    counting = _Counting(FakeBackofficeClient())
    client = real_client_over(counting)
    for _ in range(3):
        client.list_apartments("ua")
        client.get_apartment(APT_A, "ua")
        client.get_availability(APT_A)
        client.get_quote(APT_A, "2099-01-01", "2099-01-03", 2)
    assert counting.calls.count("list_apartments") == 1
    assert counting.calls.count("get_apartment") == 1
    assert counting.calls.count("get_availability") == 1
    assert counting.calls.count("get_quote") == 3


def test_booking_errors_map_to_backoffice_statuses():
    client = real_client_over(FakeBackofficeClient())
    with pytest.raises(api_client.BackofficeError, match="bad_request"):
        client.create_booking(APT_A, "2099-01-03", "2099-01-01", 2, "Тест", "+380000000000")


# --- спека 108: група через справжній клієнт поверх підміни --------------------------

def test_group_quote_and_booking_over_real_client():
    fake = FakeBackofficeClient()
    client = real_client_over(fake)
    q = client.get_group_quote([APT_A, APT_B], "2099-01-01", "2099-01-03", [2, 3])
    assert q == fake.get_group_quote([APT_A, APT_B], "2099-01-01", "2099-01-03", [2, 3])
    assert [r["apartment_id"] for r in q["rooms"]] == [APT_A, APT_B]
    assert q["total"] == sum(r["total"] for r in q["rooms"])
    b = client.create_group_booking([{"apartment_id": APT_A, "guests": 2},
                                     {"apartment_id": APT_B, "guests": 3}],
                                    "2099-01-01", "2099-01-03", "Тест", "+380000000000",
                                    idempotency_key="0b5e7b0c-0000-4000-8000-000000000108")
    assert b["total"] == q["total"]
    assert fake.group_requests[-1]["idempotency_key"] == "0b5e7b0c-0000-4000-8000-000000000108"
    assert [i["apartment_id"] for i in client.get_booking(b["booking_id"])["group"]["items"]] == [APT_A, APT_B]


def test_group_errors_keep_status_and_apartment():
    client = real_client_over(FakeBackofficeClient())
    with pytest.raises(api_client.BackofficeError, match="apartment_not_found") as e:
        client.get_group_quote([APT_A, "no-such"], "2099-01-01", "2099-01-03", [2, 2])
    assert e.value.data["apartment_id"] == "no-such"
    with pytest.raises(api_client.BackofficeError, match="dates_unavailable") as e:
        client.create_group_booking([{"apartment_id": APT_A, "guests": 2},
                                     {"apartment_id": APT_B, "guests": 2}],
                                    "2099-01-01", "2099-01-03", "Тест [гонка]", "+380000000000")
    assert e.value.data == {"error": "dates_unavailable", "apartment_id": APT_B, "apartment": "B"}
    with pytest.raises(api_client.BackofficeError, match="need_two_apartments"):
        client.create_group_booking([{"apartment_id": APT_A, "guests": 2}],
                                    "2099-01-01", "2099-01-03", "Тест", "+380000000000")


def test_delay_applies_to_backoffice_calls_only():
    fake = FakeBackofficeClient()
    delayed = DelayedBackoffice(fake, 50)
    t0 = time.perf_counter()
    delayed.list_apartments("ua")
    assert time.perf_counter() - t0 >= 0.05
    t0 = time.perf_counter()
    delayed.state()                                  # не бек-офіс — без затримки
    assert time.perf_counter() - t0 < 0.05


# --- лічильники для заміру (спека 002, FR-021) --------------------------------------

def test_counters_count_calls_and_peak():
    client = real_client_over(DelayedBackoffice(FakeBackofficeClient(), 50), count=True)
    barrier = threading.Barrier(10)

    def worker(i):
        barrier.wait()
        client.get_quote(APT_A, f"2099-01-{i + 1:02d}", f"2099-02-{i + 1:02d}", 2)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(5)
    counters = client._session.counters()
    assert counters["calls"] == 10
    assert 2 <= counters["peak"] <= 10


@pytest.mark.parametrize("environ,real,counting", [
    ({}, False, False),
    ({"SMOKE_FAKE_REAL_CLIENT": "1", "SMOKE_FAKE_COUNT": "1"}, True, True),
])
def test_build_client(environ, real, counting):
    backend = FakeBackofficeClient()
    client, session = fake_wsgi._build_client(environ, backend)
    assert isinstance(client, api_client.BackofficeClient) is real
    if not real:
        assert client is backend
    assert (session is not None) is counting
    if counting:
        assert client._session is session
        assert session.count is True


def test_state_payload():
    state = {"last_quote": {"guests": 2}}
    assert fake_wsgi._state_payload(state, None) == state
    _, session = fake_wsgi._build_client(
        {"SMOKE_FAKE_REAL_CLIENT": "1", "SMOKE_FAKE_COUNT": "1"}, FakeBackofficeClient())
    payload = fake_wsgi._state_payload(state, session)
    assert payload == {**state, "transport": {"pid": os.getpid(), "calls": 0, "peak": 0}}
