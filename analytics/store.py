"""SQLite статистики: сесії, події, записи rrweb (zlib).

Файл бази — Config.ANALYTICS_DB (поза репо). Не задано — модуль вимкнений, і сюди
ніхто не ходить. З'єднання коротке, на запит: WAL + busy_timeout дають кільком
воркерам gunicorn писати без блокувань довших за частки секунди.
"""
import json
import os
import sqlite3
import threading
import time
import zlib

from config import Config

REC_KEEP_DAYS = 90      # записи сесій — 90 днів (завдання)
DATA_KEEP_DAYS = 400    # агрегатні події й сесії — рік з запасом на порівняння
_PURGE_EVERY_S = 6 * 3600

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
  sid TEXT PRIMARY KEY, day TEXT NOT NULL, first_ts INTEGER NOT NULL, last_ts INTEGER NOT NULL,
  device TEXT, width INTEGER, lang TEXT, site_lang TEXT, ref TEXT, landing TEXT,
  utm_source TEXT, utm_medium TEXT, utm_campaign TEXT, utm_term TEXT, utm_content TEXT,
  ref_param TEXT, pages INTEGER DEFAULT 0, interacted INTEGER DEFAULT 0, bot INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY, ts INTEGER NOT NULL, day TEXT NOT NULL, sid TEXT, pv TEXT,
  kind TEXT NOT NULL, path TEXT, device TEXT, sel TEXT, role TEXT, txt TEXT,
  x REAL, y REAL, n INTEGER, num1 REAL, num2 REAL, num3 REAL, str1 TEXT
);
CREATE INDEX IF NOT EXISTS events_day_kind ON events(day, kind);
CREATE INDEX IF NOT EXISTS events_sid ON events(sid);
CREATE TABLE IF NOT EXISTS recordings (
  id INTEGER PRIMARY KEY, sid TEXT NOT NULL, pv TEXT, seq INTEGER, ts INTEGER NOT NULL,
  day TEXT NOT NULL, path TEXT, size INTEGER, data BLOB NOT NULL
);
CREATE INDEX IF NOT EXISTS recordings_sid ON recordings(sid, ts);
CREATE INDEX IF NOT EXISTS recordings_day ON recordings(day);
"""

_ready: set = set()
_lock = threading.Lock()
_last_purge = 0.0

# Колонки events, які можна заповнити з очищеної події (clean.clean_event)
_EVENT_COLS = ("sel", "role", "txt", "x", "y", "n", "num1", "num2", "num3", "str1")


def enabled() -> bool:
    return bool(Config.ANALYTICS_DB)


def connect() -> sqlite3.Connection:
    path = Config.ANALYTICS_DB
    con = sqlite3.connect(path, timeout=3)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA busy_timeout=3000")
    if path not in _ready:
        with _lock:
            if path not in _ready:
                con.execute("PRAGMA journal_mode=WAL")
                con.executescript(SCHEMA)
                try:
                    os.chmod(path, 0o600)
                except OSError:
                    pass
                _ready.add(path)
    return con


def day_of(ts: float) -> str:
    return time.strftime("%Y-%m-%d", time.localtime(ts))


def save_batch(sid: str, pv: str, events: list, rec: dict | None, *, bot: bool) -> None:
    now = time.time()
    ts, day = int(now * 1000), day_of(now)
    con = connect()
    try:
        with con:
            row = con.execute("SELECT device, bot FROM sessions WHERE sid=?", (sid,)).fetchone()
            device = row["device"] if row else None
            for e in events:
                if e["kind"] == "pv":
                    device = _upsert_session_pv(con, sid, e, ts, day, bot, exists=row is not None)
                    row = row or {"device": device, "bot": int(bot)}
            if row is None:
                # маяк без перегляду сторінки (pv загубився) — сесія без джерела
                con.execute("INSERT OR IGNORE INTO sessions(sid, day, first_ts, last_ts, bot) "
                            "VALUES (?,?,?,?,?)", (sid, day, ts, ts, int(bot)))
            interacted = any(e["kind"] in ("c", "rg", "dd", "dx", "fd", "f")
                             or (e["kind"] == "sc" and e.get("num2") == 1) for e in events)
            con.execute("UPDATE sessions SET last_ts=?, interacted=MAX(interacted, ?), "
                        "bot=MAX(bot, ?) WHERE sid=?", (ts, int(interacted), int(bot), sid))
            for e in events:
                cols = ["ts", "day", "sid", "pv", "kind", "path", "device"]
                vals = [ts, day, sid, pv, e["kind"], e["path"], device]
                for c in _EVENT_COLS:
                    if e.get(c) is not None:
                        cols.append(c)
                        vals.append(e[c])
                con.execute(f"INSERT INTO events({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                            vals)
            if rec:
                blob = zlib.compress(json.dumps(rec["events"], separators=(",", ":")).encode(), 6)
                con.execute("INSERT INTO recordings(sid, pv, seq, ts, day, path, size, data) "
                            "VALUES (?,?,?,?,?,?,?,?)",
                            (sid, pv, rec["seq"], ts, day, rec["path"], len(blob), blob))
        maybe_purge(con, now)
    finally:
        con.close()


def _upsert_session_pv(con, sid, e, ts, day, bot, *, exists) -> str:
    from analytics.clean import device_of
    device = device_of(e["w"])
    bot = int(bot or e.get("wd"))
    if not exists:
        u = e.get("utm") or {}
        con.execute(
            "INSERT OR IGNORE INTO sessions(sid, day, first_ts, last_ts, device, width, lang, "
            "site_lang, ref, landing, utm_source, utm_medium, utm_campaign, utm_term, utm_content, "
            "ref_param, pages, bot) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0,?)",
            (sid, day, ts, ts, device, e["w"], e["lang"], e["site_lang"], e["ref"], e["path"],
             u.get("utm_source"), u.get("utm_medium"), u.get("utm_campaign"), u.get("utm_term"),
             u.get("utm_content"), u.get("ref"), bot))
    con.execute("UPDATE sessions SET pages=pages+1, device=COALESCE(device, ?), "
                "width=COALESCE(width, ?), bot=MAX(bot, ?) WHERE sid=?",
                (device, e["w"], bot, sid))
    return device


def save_server_event(kind: str, path: str, step: str) -> None:
    """Подія, яку бачить лише сервер (перехід на оплату): без сесії."""
    now = time.time()
    con = connect()
    try:
        with con:
            con.execute("INSERT INTO events(ts, day, kind, path, str1) VALUES (?,?,?,?,?)",
                        (int(now * 1000), day_of(now), kind, path, step))
    finally:
        con.close()


def maybe_purge(con, now: float, *, force: bool = False) -> None:
    """Записи старші 90 днів і дані старші 400 — геть. Не частіше раз на 6 год на процес."""
    global _last_purge
    if not force and now - _last_purge < _PURGE_EVERY_S:
        return
    _last_purge = now
    rec_cut = day_of(now - REC_KEEP_DAYS * 86400)
    data_cut = day_of(now - DATA_KEEP_DAYS * 86400)
    with con:
        con.execute("DELETE FROM recordings WHERE day < ?", (rec_cut,))
        con.execute("DELETE FROM events WHERE day < ?", (data_cut,))
        con.execute("DELETE FROM sessions WHERE day < ?", (data_cut,))


def load_recording(con, sid: str) -> list:
    out = []
    for r in con.execute("SELECT data FROM recordings WHERE sid=? ORDER BY ts, seq", (sid,)):
        out.extend(json.loads(zlib.decompress(r["data"])))
    out.sort(key=lambda e: e.get("timestamp") or 0)   # кілька сторінок і вкладок — один час
    return out
