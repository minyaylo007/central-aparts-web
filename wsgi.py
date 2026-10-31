"""WSGI entry point for running the public site under gunicorn.

    gunicorn -w 2 --threads 4 --timeout 60 -b 127.0.0.1:8004 \
        --no-control-socket wsgi:application

--no-control-socket: the default control socket path is shared by every
gunicorn of the same user, so two sites of one user would collide on it.

Replaces the AWS Lambda packaging (`wsgi_adapter.lambda_handler`) for the
ordinary-server deployment. `wsgi_adapter.py` stays in the repo: it is still
the rollback path back to Lambda + CloudFront.
"""
import logging
import os

# Our own module loggers (app.py, api_client.py, analytics/ — its children
# analytics.* propagate up to "analytics"). Not the root logger: that would
# also pull in INFO of every library (urllib3 & co.), whose lines are not ours
# to vet for guest data. A new getLogger() in our code must be listed here —
# tests/test_wsgi_logging.py::test_every_app_logger_is_attached fails until it
# is (30.10–31.10 analytics was missing: the log watcher never saw a beacon).
APP_LOGGERS = ("app", "api_client", "analytics")


def attach_app_logging(source: logging.Logger | None = None) -> None:
    """Send INFO+ of our loggers to gunicorn's own error-log handlers.

    Without it nothing handles them under gunicorn: the root logger has no
    handlers, Python's last-resort handler prints WARNING+ only, and INFO
    (client_ip_mode, quote, coupon_check) never reached the journal. Reusing
    gunicorn's handlers keeps one stream (stderr → journald) and one format —
    `[time] [pid] [LEVEL] message`. Only gunicorn's own handlers (it marks
    them `_gunicorn`) — not whatever else sits on `gunicorn.error` (pytest
    hangs its capture handlers on non-propagating loggers). None of them
    (pytest, `flask run`) → no-op; the Lambda path (wsgi_adapter.py) never
    imports this.
    """
    source = source or logging.getLogger("gunicorn.error")
    handlers = [h for h in source.handlers if getattr(h, "_gunicorn", False)]
    if not handlers:
        return
    for name in APP_LOGGERS:
        log = logging.getLogger(name)
        log.setLevel(logging.INFO)
        for handler in handlers:
            if handler not in log.handlers:
                log.addHandler(handler)


class InsecureSecretKey(RuntimeError):
    """Prod without its own SECRET_KEY: refuse to boot instead of signing sessions
    with the key everyone can read in the public repo."""


def refuse_insecure_secret_key(env: str, key: str | None,
                               server_software: str | None) -> None:
    """Raise before the app is imported if config.secret_key_problem() objects.

    Under gunicorn the exception fails the worker boot (exit 3) and the arbiter
    halts — systemd restarts it every RestartSec, each time with this line in the
    journal, until SECRET_KEY is set in site.env. Never logs the key itself.
    """
    problem = secret_key_problem(env, key, server_software)
    if problem is None:
        return
    logging.getLogger("app").critical(
        "secret_key_refused env=%s reason=%s fix=SECRET_KEY_in_site.env", env, problem)
    raise InsecureSecretKey(
        f"refusing to start: SECRET_KEY {problem} in prod (set it in site.env)")


# Before importing app: it logs its client_ip_mode line at import time.
attach_app_logging()

from config import Config, secret_key_problem  # noqa: E402

refuse_insecure_secret_key(Config.ENV, Config.SECRET_KEY, os.environ.get("SERVER_SOFTWARE"))

from app import app as application  # noqa: E402

__all__ = ["application"]
