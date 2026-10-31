"""
/health — ручка версії: каже, ЯКИЙ КОМІТ тут працює.
Образець — бек-офіс (aparts/backend/app.py). Ручка не має падати НІКОЛИ:
нема BUILD_INFO.json → git; нема git → порожні рядки і ok: true.
13.10.2026: один тест на гілку — 12 → 8.
"""
import json

import pytest

import app as app_module
from config import Config

# critical: /health — за ним ходить викат, відкат і смока
pytestmark = pytest.mark.critical

FIELDS = {"ok", "git_sha", "git_short", "git_subject", "built_at", "env", "function_version"}


@pytest.fixture(autouse=True)
def clear_build_cache():
    """build_info() кешує на весь процес — інакше тести труїли б одне одного."""
    app_module._BUILD_INFO = None
    yield
    app_module._BUILD_INFO = None


def _no_build_file(monkeypatch):
    """Симулює відсутній BUILD_INFO.json (на коробці забули проштампувати)."""
    def _boom(*a, **kw):
        raise FileNotFoundError("BUILD_INFO.json")
    monkeypatch.setattr(app_module, "open", _boom, raising=False)


# --------------------------------------------------------------------------
# базовий контракт
# --------------------------------------------------------------------------

def test_health_returns_200_json_with_all_fields(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.mimetype == "application/json"
    data = r.get_json()
    assert data["ok"] is True
    assert FIELDS <= set(data)
    assert data["env"] == Config.ENV


def test_health_reports_stamped_commit_and_function_version(client, monkeypatch):
    """Коміт — зі штампа; function_version порожній під gunicorn, заповнений на
    Lambda — так і відрізняють машини."""
    monkeypatch.setattr(app_module, "_BUILD_INFO", {
        "git_sha": "0123456789abcdef0123456789abcdef01234567",
        "git_short": "0123456",
        "git_subject": "тестовий коміт",
        "built_at": "2026-09-10T00:00:00+00:00",
    })
    monkeypatch.delenv("AWS_LAMBDA_FUNCTION_VERSION", raising=False)
    data = client.get("/health").get_json()
    assert data["git_sha"] == "0123456789abcdef0123456789abcdef01234567"
    assert data["git_short"] == "0123456"
    assert data["git_subject"] == "тестовий коміт"
    assert data["built_at"] == "2026-09-10T00:00:00+00:00"
    assert data["function_version"] == ""
    monkeypatch.setenv("AWS_LAMBDA_FUNCTION_VERSION", "42")
    assert client.get("/health").get_json()["function_version"] == "42"


# --------------------------------------------------------------------------
# кешування: CloudFront стоїть перед усім сайтом (CodeUri: ../)
# --------------------------------------------------------------------------

def test_health_is_never_cached(client):
    """/health ставить no-store сам; after_request статики чіпає лише /static/*."""
    r = client.get("/health")
    assert r.headers.get("Cache-Control") == "no-store"


# --------------------------------------------------------------------------
# запасний шлях: ручка не падає ніколи
# --------------------------------------------------------------------------

def test_health_without_build_info_uses_git_or_empty_strings(client, monkeypatch):
    """Нема файлу, але є .git — коміт усе одно видно; нема й git — порожні
    рядки, але ok: true і 200."""
    _no_build_file(monkeypatch)
    monkeypatch.setattr(app_module, "_git_build_info", lambda: {
        "git_sha": "f" * 40, "git_short": "fffffff", "git_subject": "з git",
        "built_at": "",
    })
    data = client.get("/health").get_json()
    assert data["git_short"] == "fffffff"
    assert data["git_subject"] == "з git"

    app_module._BUILD_INFO = None
    monkeypatch.setattr(app_module, "_git_build_info", lambda: {})
    r = client.get("/health")
    assert r.status_code == 200
    data = r.get_json()
    assert data["ok"] is True
    assert (data["git_sha"], data["git_short"], data["built_at"]) == ("", "", "")


def test_health_survives_broken_build_info(client, monkeypatch, tmp_path):
    """Побитий JSON у BUILD_INFO.json — теж 200, а не 500."""
    def _garbage(*a, **kw):
        raise ValueError("not json")
    monkeypatch.setattr(app_module.json, "load", _garbage)
    monkeypatch.setattr(app_module, "_git_build_info", lambda: {})
    r = client.get("/health")
    assert r.status_code == 200
    assert r.get_json()["ok"] is True


def test_git_fallback_returns_dict_without_raising(monkeypatch):
    """Ані git-а, ані .git (Lambda) — {} замість винятку."""
    def _boom(*a, **kw):
        raise OSError("git: command not found")
    monkeypatch.setattr(app_module.subprocess, "check_output", _boom)
    assert app_module._git_build_info() == {}


def test_build_info_reads_file_only_once(monkeypatch):
    """Кеш у модулі: файл не читається на кожен запит."""
    calls = []
    real_open = open

    def _counting_open(*a, **kw):
        calls.append(a[0] if a else "")
        return real_open(*a, **kw)

    monkeypatch.setattr(app_module, "open", _counting_open, raising=False)
    monkeypatch.setattr(app_module, "_git_build_info", lambda: {"git_sha": "x" * 40})
    first = app_module.build_info()
    second = app_module.build_info()
    assert first is second
    assert len(calls) <= 1


# --------------------------------------------------------------------------
# секрети
# --------------------------------------------------------------------------

def test_health_leaks_no_secrets(client, monkeypatch):
    monkeypatch.setattr(Config, "SECRET_KEY", "s3cret-flask-key")
    monkeypatch.setattr(Config, "PUBLIC_API_KEY", "s3cret-api-key")
    monkeypatch.setattr(Config, "BACKOFFICE_URL", "https://cv.central-aparts.store")
    body = client.get("/health").get_data(as_text=True)
    assert "s3cret-flask-key" not in body
    assert "s3cret-api-key" not in body
    assert "central-aparts.store" not in body
    data = json.loads(body)
    assert set(data) == FIELDS
