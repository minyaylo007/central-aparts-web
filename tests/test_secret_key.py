"""SECRET_KEY на проді: без власного ключа (порожньо або dev-дефолт) gunicorn не стартує,
у журналі — рядок secret_key_refused без значення ключа. Тести, `flask run` і смока
(ENV=smoke) живуть на dev-дефолті, як раніше.

Ключі тут — синтетичні, не секрети. Справжній gunicorn слухає unix-сокет у tmp_path:
жодного TCP-порту."""
import http.client
import logging
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

import wsgi
from config import DEV_SECRET_KEY, Config, secret_key_problem

ROOT = Path(__file__).resolve().parent.parent
GUNICORN = "gunicorn/26.2.0"
OWN_KEY = "synthetic-test-key-not-a-secret-0123456789"


@pytest.mark.parametrize("env, key, software, expected", [
    ("prod", None, GUNICORN, "unset"),
    ("prod", "", GUNICORN, "unset"),
    ("prod", "   ", GUNICORN, "unset"),
    ("prod", DEV_SECRET_KEY, GUNICORN, "dev_default"),
    ("prod", OWN_KEY, GUNICORN, None),
    # Не прод — dev-дефолт дозволено: тести/flask run (без gunicorn), смока, інші ENV.
    ("prod", DEV_SECRET_KEY, None, None),
    ("prod", DEV_SECRET_KEY, "Werkzeug/3.1.9", None),
    ("smoke", DEV_SECRET_KEY, GUNICORN, None),
    ("dev", None, GUNICORN, None),
])
def test_secret_key_problem(env, key, software, expected):
    assert secret_key_problem(env, key, software) == expected


@pytest.mark.critical
def test_refusal_logs_reason_without_key(caplog):
    caplog.set_level(logging.CRITICAL, logger="app")
    with pytest.raises(wsgi.InsecureSecretKey) as exc:
        wsgi.refuse_insecure_secret_key("prod", DEV_SECRET_KEY, GUNICORN)
    lines = [r.getMessage() for r in caplog.records]
    assert lines == ["secret_key_refused env=prod reason=dev_default "
                     "fix=SECRET_KEY_in_site.env"]
    assert DEV_SECRET_KEY not in str(exc.value)


def test_no_refusal_with_own_key_or_outside_prod(caplog):
    wsgi.refuse_insecure_secret_key("prod", OWN_KEY, GUNICORN)
    wsgi.refuse_insecure_secret_key("prod", DEV_SECRET_KEY, None)
    assert caplog.records == []


def test_tests_still_run_on_dev_default():
    """Тести не задають SECRET_KEY — dev-дефолт, як до правки (і wsgi імпортувався)."""
    if "SECRET_KEY" not in os.environ:
        assert Config.SECRET_KEY == DEV_SECRET_KEY


def _gunicorn_env(**extra):
    """Оточення без секретів хоста: лише PATH/HOME і те, що задав тест."""
    env = {k: os.environ[k] for k in ("PATH", "HOME", "LANG") if k in os.environ}
    env.update(BACKOFFICE_URL="http://127.0.0.1:9", PUBLIC_API_KEY="synthetic")
    env.update(extra)
    return env


def _start_gunicorn(tmp_path, env):
    sock = tmp_path / "site.sock"
    proc = subprocess.Popen(
        [sys.executable, "-m", "gunicorn", "-w", "1", "--timeout", "30",
         "-b", f"unix:{sock}", "--no-control-socket", "wsgi:application"],
        cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    return proc, sock


def _stop(proc):
    if proc.poll() is None:
        proc.terminate()  # за PID — це наш власний дочірній процес
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
    return proc.stdout.read()


@pytest.mark.critical
@pytest.mark.parametrize("key, reason", [
    (None, "dev_default"),      # змінної нема — Config бере dev-дефолт
    ("", "unset"),              # SECRET_KEY= у site.env порожнім
    (DEV_SECRET_KEY, "dev_default"),
])
def test_gunicorn_refuses_prod_without_own_key(tmp_path, key, reason):
    """ENV не задано (= prod, як у site.env) — gunicorn зупиняється сам, у журналі причина."""
    env = _gunicorn_env() if key is None else _gunicorn_env(SECRET_KEY=key)
    proc, _ = _start_gunicorn(tmp_path, env)
    try:
        proc.wait(timeout=30)
    finally:
        out = _stop(proc)
    assert proc.returncode != 0
    assert f"[CRITICAL] secret_key_refused env=prod reason={reason}" in out
    assert "Worker failed to boot" in out
    assert DEV_SECRET_KEY not in out


class _UnixHTTPConnection(http.client.HTTPConnection):
    def __init__(self, path):
        super().__init__("localhost", timeout=5)
        self._path = path

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(5)
        self.sock.connect(self._path)


@pytest.mark.critical
def test_gunicorn_serves_prod_with_own_key(tmp_path):
    """Те саме оточення, що на проді, але зі своїм ключем — /health відповідає 200."""
    proc, sock = _start_gunicorn(tmp_path, _gunicorn_env(SECRET_KEY=OWN_KEY))
    status = None
    try:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline and proc.poll() is None and status is None:
            try:
                conn = _UnixHTTPConnection(str(sock))
                conn.request("GET", "/health")
                status = conn.getresponse().status
                conn.close()
            except OSError:
                time.sleep(0.2)
    finally:
        out = _stop(proc)
    assert status == 200, out
    assert "secret_key_refused" not in out
    assert OWN_KEY not in out
