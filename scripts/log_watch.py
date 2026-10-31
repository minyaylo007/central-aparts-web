#!/usr/bin/env python3
"""Сторож журналу сайту: ошибка чи warning → рядок дирижеру.

Без моделі й без мережі. Раз на 10 хв (таймер <юніт>-logwatch.timer) читає:
- `journalctl --user -u <юніт>` з останнього курсора;
- стан юніта: не active або виріс NRestarts;
- базу статистики (лише читання): помилки JS (je), відповіді /api/* з кодом 5xx або
  довші за 10 с (ap; фонові вкладки без часу, див. analytics/clean.py).

Маяк статистики (з 30.10): рядки INFO analytics_beacon_stats accepted=N (analytics/__init__.py,
раз на годину на процес і одразу на першу пачку після тиші). 24 год без жодної прийнятої
пачки — одне повідомлення, не частіше разу на добу: «гостей нема» і «запис зламався» тепер
розрізняє журнал, а не здогад. Відлік — з першого прогону сторожа (стан beacon_ok).

Ловить ERROR, CRITICAL, Traceback, WARNING. http_404 (і WARNING — наші адреси, і INFO —
чужі, з 30.10) — лише коли той самий шлях повторився 5+ разів за годину: поодинокі 404 від
ботів — шум, а битий зовнішній лінк, який гості відкривають, повторюється. http_404 scan=1 не
рахується зовсім (з 24.10): повтор сканера — не наша поломка, сканери й так видно в
журналі сайту як INFO.

Підпис = рівень + подія + шлях (або call=), цифри → N, підозрілі сегменти → :x.
Лічильник 404 — за ТОЧНИМ шляхом (без query, цифри як є): /.env.aws і /.boto обидва
показуються як /:x, але рахуються окремо, бо інакше сканер із 31 різним шляхом за
секунду виглядав би як «один шлях ×31». Сирого шляху в стані нема — лише його sha256
(перші 12 знаків) після « #» у ключі; у повідомлення хеш не йде.
Ознаки «сплеск різних 404» навмисно нема: різні шляхи сюди не доходять сигналом, рядки
сканера вже лежать у журналі сайту з scan=1, а рядок, який нікому не надсилається й не
змінює жодного рішення, — лише ще один шум (23.10).
Один підпис — одне повідомлення на годину, поки лічильник за годину не виріс удвічі
(і щонайменше до 4).
Значень із запиту, телефонів, імен, ключів, IP у повідомленні нема: у підпис іде лише
ім'я події та очищений шлях. Усе знайдене за прогін — ОДИН рядок через
команду сповіщення `<NOTIFY> site <рядок>` (Enter у чаті дирижера = одне повідомлення).

Місця беруться з середовища, типові значення виводяться з розташування чекауту:
  SITE_UNIT        юніт сайту (типово <користувач>-central-aparts-site)
  SITE_DATA_DIR    тека стану й бази статистики (типово <чекаут>-data поруч із чекаутом)
  SITE_NOTIFY_CMD  команда сповіщення (типово ~/bin/lead-say.sh)

  scripts/log_watch.py [--dry-run] [--since ЧАС] [--state ФАЙЛ] [--db ФАЙЛ]

--dry-run: друкує рядок і нічого не надсилає й не зберігає.
--since: читати журнал з цього часу (формат journalctl, напр. "2026-10-30 08:00"), а не з
курсора — перевірка правки на вже записаному журналі; лише разом із --dry-run.
"""
import argparse
import getpass
import hashlib
import json
import os
import re
import sqlite3
import subprocess
import sys
import time

_CHECKOUT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UNIT = os.environ.get("SITE_UNIT") or f"{getpass.getuser()}-central-aparts-site"
DATA_DIR = os.environ.get("SITE_DATA_DIR") or _CHECKOUT + "-data"
STATE_PATH = os.path.join(DATA_DIR, "logwatch-state.json")
DB_PATH = os.path.join(DATA_DIR, "analytics.db")
LEAD_SAY = os.environ.get("SITE_NOTIFY_CMD") or os.path.expanduser("~/bin/lead-say.sh")
WHERE = f"journalctl --user -u {UNIT}"

HOUR = 3600
FIRST_RUN_LOOKBACK = 600      # перший запуск без курсора — останні 10 хв, не вся історія
REPEAT_404 = 5                # той самий 404-шлях стільки разів за годину — сигнал
SLOW_API_MS = 10_000
MAX_TS_PER_SIG = 2000         # стеля списку міток на підпис (потоп не роздуває стан)
MAX_ITEMS = 6                 # скільки підписів влазить у рядок, решта — «і ще K»
BEACON_QUIET_S = 24 * HOUR    # стільки без прийнятої пачки маяка — сигнал, раз на добу

_LINE_RE = re.compile(r"^\[[^\]]+\] \[\d+\] \[(?P<lvl>[A-Z]+)\] (?P<msg>.*)$")
_EVENT_RE = re.compile(r"[a-z][a-z0-9_]{2,40}")
_KV_RE = re.compile(r"\b(path|call)=(\S+)")
_SEG_OK_RE = re.compile(r"(?:[a-z][a-z_\-.]{0,23}|N(?:\.[a-z]{2,4})?)")
_ALERT_LEVELS = ("WARNING", "ERROR", "CRITICAL")
_BEACON_RE = re.compile(r"^analytics_beacon_stats accepted=(\d+)\b")


def clean_segment(seg: str) -> str:
    seg = re.sub(r"\d+", "N", seg)
    seg = re.sub(r"N(?:[\-_.]?N)+", "N", seg)
    if seg == "" or _SEG_OK_RE.fullmatch(seg):
        return seg
    return ":x"


def clean_path(raw: str) -> str:
    """Шлях або call= → безпечний вигляд: без query, цифри → N, усе, що не схоже на
    коротке слово з малих літер (UUID, токен, ім'я в URL), → :x."""
    p = raw.split("?", 1)[0].split("#", 1)[0][:200]
    p = re.sub(r"%[0-9A-Fa-f]{2}", "", p)
    out = "/".join(clean_segment(s) for s in p.split("/"))
    return re.sub(r"(?:/:x){2,}", "/:x", out)[:80]


# Рядки без snake_case-події (gunicorn): лише відомі фрази. Слова з вільного тексту
# в підпис не беремо — там може бути ім'я або значення з запиту.
_KNOWN_FREE = (
    ("WORKER TIMEOUT", "gunicorn_worker_timeout"),
    ("SIGKILL", "gunicorn_worker_killed"),
    ("Error handling request", "gunicorn_error_handling_request"),
    ("Exception in worker", "gunicorn_exception_in_worker"),
    ("Socket error", "gunicorn_socket_error"),
    ("Worker exiting", "gunicorn_worker_exiting"),
)


def _free_text_event(msg: str) -> str:
    for needle, event in _KNOWN_FREE:
        if needle in msg:
            return event
    return "free_text"


_KEY_SEP = " #"


def path_key(raw: str) -> str:
    """Хеш точного шляху для лічильника 404: без query, решта як є — цифри НЕ
    згортаються (30.10: /2018/ … /2022/ від одного сканера за секунду склеїлись у «/N/ ×5»).
    Сам шлях у стан не пишемо."""
    p = raw.split("?", 1)[0].split("#", 1)[0][:200]
    return hashlib.sha256(p.encode("utf-8", "replace")).hexdigest()[:12]


def display_sig(key: str) -> str:
    """Ключ стану → те, що бачить дирижер (без хешу шляху)."""
    return key.split(_KEY_SEP, 1)[0]


def signature_of(level: str, msg: str):
    """(підпис, ключ 404-шляху або None) для рядка застосунку/gunicorn.
    Ключ 404 = підпис + хеш точного шляху: різні шляхи з однаковим очищеним виглядом
    (/.env.aws і /.boto → /:x) не склеюються в один лічильник."""
    first = msg.split(" ", 1)[0]
    event = first if _EVENT_RE.fullmatch(first) else _free_text_event(msg)
    m = _KV_RE.search(msg)
    where = f" {m.group(1)}={clean_path(m.group(2))}" if m else ""
    sig = f"{level} {event}{where}"
    if event == "http_404":
        return sig, (f"{sig}{_KEY_SEP}{path_key(m.group(2))}" if m else sig)
    return sig, None


def parse_journal_line(message: str):
    """Повертає (підпис, is_404) або None, якщо рядок нецікавий."""
    if message.startswith("Traceback"):
        return "TRACEBACK", False
    m = _LINE_RE.match(message)
    if not m:
        return None
    lvl, msg = m.group("lvl"), m.group("msg")
    sig, k404 = signature_of(lvl, msg)
    if k404:
        if " scan=1 " in f" {msg} ":
            return None
        return k404, True
    if lvl in _ALERT_LEVELS:
        return sig, False
    return None


def beacon_accepted(message: str):
    """Число прийнятих пачок із рядка analytics_beacon_stats або None."""
    m = _LINE_RE.match(message)
    if not m or m.group("lvl") != "INFO":
        return None
    b = _BEACON_RE.match(m.group("msg"))
    return int(b.group(1)) if b else None


def beacon_quiet(st, entries, now):
    """Оновлює st["beacon_ok"] (остання мітка з accepted>0) і повертає рядок сигналу або None.
    Сигнал — коли з beacon_ok минуло 24 год і з попереднього сигналу теж 24 год."""
    for ts, msg, _ in entries:
        n = beacon_accepted(msg)
        if n:
            st["beacon_ok"] = max(int(ts), int(st.get("beacon_ok") or 0))
    ok = st.setdefault("beacon_ok", now)      # перший прогін — відлік з цього моменту
    if now - ok < BEACON_QUIET_S or now - int(st.get("beacon_sent") or 0) < BEACON_QUIET_S:
        return None
    st["beacon_sent"] = now
    hours = (now - ok) // HOUR
    return (f"маяк статистики: 0 прийнятих пачок за {hours} год — гостей нема чи запис зламався? "
            f"дивись {WHERE} | grep analytics_")


# ---------- джерела ----------

def read_journal(cursor, since=None):
    cmd = ["journalctl", "--user", "-u", UNIT, "-o", "json", "--no-pager",
           "--output-fields=MESSAGE"]
    if since:
        cmd += ["--since", since]
    elif cursor:
        cmd += ["--after-cursor", cursor]
    else:
        cmd += ["--since", f"@{int(time.time()) - FIRST_RUN_LOOKBACK}"]
    out = subprocess.run(cmd, capture_output=True, text=True, timeout=60, check=False).stdout
    entries = []
    for line in out.splitlines():
        try:
            e = json.loads(line)
        except ValueError:
            continue
        msg = e.get("MESSAGE")
        if isinstance(msg, list):          # не-UTF-8 журнал віддає масивом байтів
            msg = bytes(msg).decode("utf-8", "replace")
        if not isinstance(msg, str):
            continue
        ts = int(e.get("__REALTIME_TIMESTAMP", "0")) // 1_000_000 or int(time.time())
        entries.append((ts, msg, e.get("__CURSOR")))
    return entries


def read_unit():
    out = subprocess.run(["systemctl", "--user", "show", UNIT, "-p", "ActiveState", "-p", "NRestarts"],
                         capture_output=True, text=True, timeout=30, check=False).stdout
    kv = dict(line.split("=", 1) for line in out.splitlines() if "=" in line)
    try:
        restarts = int(kv.get("NRestarts", "0"))
    except ValueError:
        restarts = 0
    return kv.get("ActiveState", "unknown"), restarts


def read_analytics(db_path, last_id):
    """[(ts, підпис)] з бази статистики після last_id; база — лише читання."""
    if not db_path or not os.path.exists(db_path):
        return [], last_id
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=5)
    try:
        if last_id is None:           # ts у базі — мілісекунди (analytics/store.py)
            row = con.execute("SELECT COALESCE(MAX(id), 0) FROM events WHERE ts <= ?",
                              ((int(time.time()) - FIRST_RUN_LOOKBACK) * 1000,)).fetchone()
            last_id = row[0]
        # сесії ботів (headless, curl…) — не гість, їхні помилки не будять
        rows = con.execute(
            "SELECT e.id, e.ts, e.kind, e.path, e.sel, e.n, e.num1, e.str1 FROM events e"
            " LEFT JOIN sessions s ON s.sid = e.sid"
            " WHERE e.id > ? AND COALESCE(s.bot, 0) = 0 AND ("
            "  e.kind = 'je' OR (e.kind = 'ap' AND (e.n >= 500 OR e.num1 > ?)))"
            " ORDER BY e.id", (last_id, SLOW_API_MS)).fetchall()
        max_id = con.execute("SELECT COALESCE(MAX(id), ?) FROM events", (last_id,)).fetchone()[0]
    finally:
        con.close()
    found = []
    for _id, ts_ms, kind, path, sel, n, num1, str1 in rows:
        ts = int(ts_ms) // 1000
        if kind == "je":
            src = clean_segment((sel or "").rsplit("/", 1)[-1])
            found.append((ts, f"JS je src={src or '-'} page={clean_path(path or '')}"))
        elif n is not None and n >= 500:
            found.append((ts, f"API ap_5xx api={clean_path(str1 or '')}"))
        else:
            found.append((ts, f"API ap_slow api={clean_path(str1 or '')}"))
    return found, max(max_id, last_id)


# ---------- стан і рішення ----------

def load_state(path):
    try:
        with open(path, encoding="utf-8") as f:
            st = json.load(f)
        return st if isinstance(st, dict) else {}
    except (OSError, ValueError):
        return {}


def save_state(path, st):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False)
    os.replace(tmp, path)


def decide(st, hits, now):
    """hits: [(ts, підпис, is_404)]. Оновлює st["sigs"], повертає [(підпис, n за годину)]
    для відправки. 404 — лише від REPEAT_404 за годину; решта — з першого разу."""
    sigs = st.setdefault("sigs", {})
    for ts, sig, _ in hits:
        rec = sigs.setdefault(sig, {"ts": [], "sent": 0, "sent_n": 0})
        rec["ts"].append(int(ts))
        if len(rec["ts"]) > MAX_TS_PER_SIG:
            del rec["ts"][:-MAX_TS_PER_SIG]
    out = []
    for sig, rec in list(sigs.items()):
        rec["ts"] = [t for t in rec["ts"] if t > now - HOUR]
        n = len(rec["ts"])
        if n == 0:
            if rec["sent"] <= now - HOUR:
                del sigs[sig]
            continue
        need = REPEAT_404 if " http_404" in sig else 1
        if n < need:
            continue
        fresh = rec["sent"] <= now - HOUR
        # «удвічі», але не 1 → 2: друга така сама помилка за годину — не нове повідомлення
        doubled = rec["sent_n"] and n >= max(2 * rec["sent_n"], 4)
        if fresh or doubled:
            out.append((sig, n))
            rec["sent"], rec["sent_n"] = now, n
    out.sort(key=lambda x: -x[1])
    return out


def format_line(alerts):
    parts = [f"{display_sig(sig)} ×{n}" for sig, n in alerts[:MAX_ITEMS]]
    more = len(alerts) - MAX_ITEMS
    tail = f"; і ще {more}" if more > 0 else ""
    return f"сторож сайту: {'; '.join(parts)}{tail} — за годину; дивись {WHERE}"


def run(state_path, db_path, dry_run, now=None, since=None):
    now = int(now or time.time())
    st = {} if since else load_state(state_path)
    hits = []

    entries = read_journal(st.get("cursor"), since)
    for ts, msg, cur in entries:
        parsed = parse_journal_line(msg)
        if parsed:
            hits.append((ts, parsed[0], parsed[1]))
    if entries and entries[-1][2]:
        st["cursor"] = entries[-1][2]

    active, restarts = read_unit()
    if active != "active":
        hits.append((now, f"UNIT state={clean_segment(active)}", False))
    prev = st.get("restarts")
    if prev is not None and restarts > prev:
        for _ in range(restarts - prev):
            hits.append((now, "UNIT restart", False))
    st["restarts"] = restarts

    found, st["db_id"] = read_analytics(db_path, st.get("db_id"))
    hits += [(ts, sig, False) for ts, sig in found]

    alerts = decide(st, hits, now)
    quiet = beacon_quiet(st, entries, now)
    line = "; ".join(x for x in (format_line(alerts) if alerts else "", quiet or "") if x)
    if dry_run:
        print(line or f"сторож сайту: тихо (рядків журналу {len(entries)}, юніт {active}, "
                      f"рестартів {restarts}, подій бази {len(found)})")
        return 0
    rc = 0
    if line:
        r = subprocess.run([LEAD_SAY, "site", line], capture_output=True, text=True,
                           timeout=30, check=False)
        if r.returncode != 0:
            # дирижер не піднятий — позначки «надіслано» не ставимо, наступний прогін повторить
            for sig, _ in alerts:
                st["sigs"][sig]["sent"] = 0
                st["sigs"][sig]["sent_n"] = 0
            if quiet:
                st["beacon_sent"] = 0
            print("log_watch: lead-say не вдався", file=sys.stderr)
            rc = 1
        else:
            print(line)
    save_state(state_path, st)
    return rc


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--since", help="читати журнал з цього часу, а не з курсора (з --dry-run)")
    ap.add_argument("--state", default=STATE_PATH)
    ap.add_argument("--db", default=DB_PATH)
    a = ap.parse_args(argv)
    if a.since and not a.dry_run:
        ap.error("--since — лише з --dry-run: інакше курсор стрибне назад")
    return run(a.state, a.db, a.dry_run, since=a.since)


if __name__ == "__main__":
    sys.exit(main())
