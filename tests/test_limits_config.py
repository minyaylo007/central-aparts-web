"""
Налаштування фічі 002 (config.py) і стартові рядки режиму (app.py): QUOTE_CACHE_TTL,
BACKOFFICE_MAX_CONCURRENT, BACKOFFICE_SLOT_WAIT. Не задано — вимкнено (як до фічі);
неверне — вимкнено + одна ERROR з іменем, без значення.
Не critical (13.10.2026): фіча вимкнена за замовчуванням. Один тест на гілку — 30 → 11.
"""
import importlib
import logging

import pytest

import app as app_module
import config
from config import Config, _parse_setting

LIMIT_ENVS = ("QUOTE_CACHE_TTL", "BACKOFFICE_MAX_CONCURRENT", "BACKOFFICE_SLOT_WAIT")


@pytest.mark.parametrize("raw,expected", [
    ("  ", (None, False)),          # не задано
    (" 30 ", (30, False)),          # число з пробілами
    ("-1", (None, True)),           # знак — неверно
    ("١", (None, True)),            # не ASCII-цифра (isdigit() каже «так»)
])
def test_config_parse_int(raw, expected):
    assert _parse_setting(raw) == expected


@pytest.mark.parametrize("raw,expected", [
    (None, (None, False)),
    (" 2.5 ", (2.5, False)),
    (".5", (None, True)),           # лише повна форма «ціле.дріб»
])
def test_config_parse_fraction(raw, expected):
    assert _parse_setting(raw, fraction=True) == expected


def _reloaded(monkeypatch, env: dict):
    """Config, перечитаний під заданим оточенням. Старий клас лишається тим, що
    вже імпортували app.py та api_client (їх перечитування не зачіпає)."""
    for name in LIMIT_ENVS:
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    return importlib.reload(config).Config


@pytest.fixture
def restore_config():
    original = config.Config
    yield
    config.Config = original


def test_config_fields_defaults(monkeypatch, restore_config):
    c = _reloaded(monkeypatch, {})
    assert c.QUOTE_CACHE_TTL == 0
    assert c.BACKOFFICE_MAX_CONCURRENT == 0
    assert c.BACKOFFICE_SLOT_WAIT == 2.0
    assert isinstance(c.BACKOFFICE_SLOT_WAIT, float)
    assert not (c.QUOTE_CACHE_TTL_INVALID or c.BACKOFFICE_MAX_CONCURRENT_INVALID
                or c.BACKOFFICE_SLOT_WAIT_INVALID)
    assert c.QUOTE_CACHE_MAX_ENTRIES == 2000


def test_config_fields_set_and_invalid(monkeypatch, restore_config):
    c = _reloaded(monkeypatch, {"QUOTE_CACHE_TTL": "30", "BACKOFFICE_MAX_CONCURRENT": "3",
                                "BACKOFFICE_SLOT_WAIT": "0.5"})
    assert (c.QUOTE_CACHE_TTL, c.BACKOFFICE_MAX_CONCURRENT, c.BACKOFFICE_SLOT_WAIT) == (30, 3, 0.5)
    c = _reloaded(monkeypatch, {name: "abc" for name in LIMIT_ENVS})
    assert (c.QUOTE_CACHE_TTL, c.BACKOFFICE_MAX_CONCURRENT, c.BACKOFFICE_SLOT_WAIT) == (0, 0, 2.0)
    assert (c.QUOTE_CACHE_TTL_INVALID and c.BACKOFFICE_MAX_CONCURRENT_INVALID
            and c.BACKOFFICE_SLOT_WAIT_INVALID)
    c = _reloaded(monkeypatch, {"QUOTE_CACHE_TTL": "", "BACKOFFICE_MAX_CONCURRENT": "0"})
    assert (c.QUOTE_CACHE_TTL, c.BACKOFFICE_MAX_CONCURRENT) == (0, 0)
    assert not (c.QUOTE_CACHE_TTL_INVALID or c.BACKOFFICE_MAX_CONCURRENT_INVALID)


_EVENTS = ("quote_cache_mode", "backoffice_slots_mode", "backoffice_limits_config_invalid")


def _mode_lines(caplog):
    return [(r.levelname, r.getMessage()) for r in caplog.records
            if r.getMessage().split(" ", 1)[0] in _EVENTS]


def _set(monkeypatch, ttl=0, n=0, wait=2.0, invalid=()):
    monkeypatch.setattr(Config, "QUOTE_CACHE_TTL", ttl)
    monkeypatch.setattr(Config, "BACKOFFICE_MAX_CONCURRENT", n)
    monkeypatch.setattr(Config, "BACKOFFICE_SLOT_WAIT", wait)
    for name in LIMIT_ENVS:
        monkeypatch.setattr(Config, f"{name}_INVALID", name in invalid)


def test_config_startup_lines_off_and_on(monkeypatch, caplog):
    caplog.set_level(logging.DEBUG, logger="app")
    _set(monkeypatch)
    app_module._log_backoffice_limits_mode()
    assert _mode_lines(caplog) == [("INFO", "quote_cache_mode mode=off"),
                                   ("INFO", "backoffice_slots_mode mode=off")]
    caplog.clear()
    _set(monkeypatch, ttl=30, n=3, wait=2.0)
    app_module._log_backoffice_limits_mode()
    assert _mode_lines(caplog) == [
        ("INFO", "quote_cache_mode mode=on ttl=30 max_entries=2000"),
        ("INFO", "backoffice_slots_mode mode=on n=3 wait_s=2.0")]


def test_config_startup_lines_invalid(monkeypatch, caplog):
    """Неверне значення — одна ERROR з іменем змінної і запасним значенням,
    без самого значення; режими далі йдуть як вимкнені."""
    name, fallback = "BACKOFFICE_SLOT_WAIT", "2"
    caplog.set_level(logging.DEBUG, logger="app")
    monkeypatch.setenv(name, "abc")
    _set(monkeypatch, invalid=(name,))
    app_module._log_backoffice_limits_mode()
    lines = _mode_lines(caplog)
    assert lines[0] == ("ERROR", f"backoffice_limits_config_invalid env={name} fallback={fallback}")
    assert [lvl for lvl, _ in lines] == ["ERROR", "INFO", "INFO"]
    assert "abc" not in caplog.text
