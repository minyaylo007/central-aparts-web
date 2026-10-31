"""Агрегати статистики — одні й ті самі для панелі /stats і scripts/stats_report.py.

«Люди» — сесії без ознак бота, у яких була хоч одна взаємодія (клік, прокрутка,
фокус поля, крок воронки). Сесії без взаємодії рахуються окремо: це і боти, що
вдають браузер, і ті, хто відкрив і закрив вкладку.
"""
import time

from analytics.store import day_of

# Кроки воронки по порядку: (ключ, підпис, як рахується)
FUNNEL = (
    ("home", "Главная", "pv", "/"),
    ("home_dates", "Даты (главная): поиск с датами", "f", "home_dates"),
    ("apt", "Страница апартамента", "pv_prefix", "/apartment/"),
    ("dates", "Даты (апартамент): запрошена цена", "f", "dates"),
    ("price", "Получена цена", "f", "price"),
    ("form", "Открыта форма (фокус на имени/телефоне)", "f", "form"),
    ("submit", "Форма отправлена", "f", "submit"),
    ("pay", "Ушёл на оплату (счётчик сервера)", "server", "pay"),
    ("confirmed", "Страница подтверждения", "pv", "/booking-confirmed"),
)
SIDE_STEPS = (
    ("coupon_try", "Проверили купон"),
    ("coupon_ok", "Купон подошёл"),
    ("group_open", "Открыли «ещё апартаменты»"),
    ("group_quote", "Получили цену группы"),
    ("group_submit", "Отправили групповую бронь"),
)

_PEOPLE = "s.bot=0 AND s.interacted=1"


def since_day(days: int) -> str:
    return day_of(time.time() - (max(1, days) - 1) * 86400)


def overview(con, days: int) -> dict:
    d0 = since_day(days)
    r = con.execute(
        f"SELECT COUNT(*) total, SUM(bot) bots, SUM(CASE WHEN {_PEOPLE} THEN 1 ELSE 0 END) people, "
        f"SUM(CASE WHEN {_PEOPLE} AND s.device='m' THEN 1 ELSE 0 END) mobile, "
        f"SUM(CASE WHEN {_PEOPLE} THEN s.pages ELSE 0 END) pages "
        "FROM sessions s WHERE s.day >= ?", (d0,)).fetchone()
    return {k: (r[k] or 0) for k in ("total", "bots", "people", "mobile", "pages")}


DEVICE_GAP_MS = 30 * 60 * 1000


def devices_estimate(con, days: int) -> int:
    """ОЦІНКА числа пристроїв серед «людей». Сесія живе у вкладці (sessionStorage), тож одна людина
    з двома вкладками чи після перерви — дві «людини». Склеюємо сесії з тією ж шириною вікна, мовою
    браузера й джерелом, якщо нова почалась не пізніше 30 хв після кінця попередньої. Без cookies
    точніше не буває: двоє гостей з однаковим телефоном і джерелом зіллються в один пристрій."""
    last: dict = {}
    n = 0
    for r in con.execute(
            "SELECT s.width, s.lang, COALESCE(NULLIF(s.utm_source,''), NULLIF(s.ref_param,''), "
            "NULLIF(s.ref,''), '') src, s.first_ts, s.last_ts FROM sessions s "
            f"WHERE s.day >= ? AND {_PEOPLE} ORDER BY s.first_ts", (since_day(days),)):
        key = (r["width"], r["lang"], r["src"])
        if key not in last or r["first_ts"] - last[key] > DEVICE_GAP_MS:
            n += 1
        last[key] = max(last.get(key, 0), r["last_ts"])
    return n


def visitors_by_day(con, days: int) -> list:
    d0 = since_day(days)
    rows = {r["day"]: dict(r) for r in con.execute(
        f"SELECT s.day day, SUM(CASE WHEN {_PEOPLE} THEN 1 ELSE 0 END) people, "
        "SUM(CASE WHEN s.bot=0 AND s.interacted=0 THEN 1 ELSE 0 END) passive, "
        "SUM(s.bot) bots FROM sessions s WHERE s.day >= ? GROUP BY s.day", (d0,))}
    out = []
    for i in range(days - 1, -1, -1):
        d = day_of(time.time() - i * 86400)
        r = rows.get(d) or {}
        out.append({"day": d, "people": r.get("people") or 0, "passive": r.get("passive") or 0,
                    "bots": r.get("bots") or 0})
    return out


def sources(con, days: int, limit: int = 15) -> list:
    """Джерело: utm_source, інакше домен реферера, інакше «прямий»."""
    return [dict(r) for r in con.execute(
        "SELECT COALESCE(NULLIF(s.utm_source,''), NULLIF(s.ref_param,''), NULLIF(s.ref,''), "
        "'(прямой / закладка)') src, COALESCE(NULLIF(s.utm_medium,''), '') medium, "
        f"COUNT(*) n FROM sessions s WHERE s.day >= ? AND {_PEOPLE} "
        "GROUP BY src, medium ORDER BY n DESC LIMIT ?", (since_day(days), limit))]


def top_pages(con, days: int, limit: int = 15) -> list:
    return [dict(r) for r in con.execute(
        "SELECT e.path, COUNT(*) views, COUNT(DISTINCT e.sid) sessions FROM events e "
        f"JOIN sessions s ON s.sid=e.sid WHERE e.day >= ? AND e.kind='pv' AND {_PEOPLE} "
        "GROUP BY e.path ORDER BY views DESC LIMIT ?", (since_day(days), limit))]


def funnel(con, days: int) -> dict:
    """Воронка по «людях» (без ботів і без сесій без жодної дії) — ті самі числа, що в плитці «Людей»."""
    d0 = since_day(days)
    steps = []
    prev = None
    first = None
    for key, label, how, arg in FUNNEL:
        if how == "pv":
            n = con.execute("SELECT COUNT(DISTINCT e.sid) FROM events e JOIN sessions s ON s.sid=e.sid "
                            "WHERE e.day>=? AND e.kind='pv' AND e.path=? AND " + _PEOPLE, (d0, arg)).fetchone()[0]
        elif how == "pv_prefix":
            n = con.execute("SELECT COUNT(DISTINCT e.sid) FROM events e JOIN sessions s ON s.sid=e.sid "
                            "WHERE e.day>=? AND e.kind='pv' AND e.path LIKE ? AND " + _PEOPLE,
                            (d0, arg + "%")).fetchone()[0]
        elif how == "f":
            n = con.execute("SELECT COUNT(DISTINCT e.sid) FROM events e JOIN sessions s ON s.sid=e.sid "
                            "WHERE e.day>=? AND e.kind='f' AND e.str1=? AND " + _PEOPLE, (d0, arg)).fetchone()[0]
        else:  # server: переходи на оплату, без сесії
            n = con.execute("SELECT COUNT(*) FROM events WHERE day>=? AND kind='srv' AND str1=?",
                            (d0, arg)).fetchone()[0]
        n = n or 0
        first = n if first is None else first
        # шаг більший за попередній (зайшли одразу на апартамент) — втрати немає, «—»
        loss = None if prev in (None, 0) or n > prev else round(100 * (prev - n) / prev)
        steps.append({"key": key, "label": label, "n": n, "loss_pct": loss,
                      "of_first_pct": round(100 * n / first) if first else None})
        prev = n
    side = []
    for key, label in SIDE_STEPS:
        n = con.execute("SELECT COUNT(DISTINCT e.sid) FROM events e JOIN sessions s ON s.sid=e.sid "
                        "WHERE e.day>=? AND e.kind='f' AND e.str1=? AND " + _PEOPLE, (d0, key)).fetchone()[0]
        side.append({"key": key, "label": label, "n": n or 0})
    return {"steps": steps, "side": side}


def click_problems(con, days: int, kind: str, limit: int = 15) -> list:
    """Топ rage- (kind='rg'), dead- (kind='dd') або тапів по декору без реакції (kind='dx')
    за сторінкою й елементом. rule — версія правила dead (1 — старі записи, NULL у базі: dead і
    по декору; 2 — з 30.10, лише по цілі, що обіцяє дію); рядки двох правил не змішуються."""
    return [dict(r) for r in con.execute(
        "SELECT e.path, e.sel, MAX(e.txt) txt, e.device, COUNT(*) n, COUNT(DISTINCT e.sid) sessions, "
        "CAST(COALESCE(e.num1, 1) AS INTEGER) rule "
        "FROM events e JOIN sessions s ON s.sid=e.sid WHERE e.day>=? AND e.kind=? AND s.bot=0 "
        "GROUP BY e.path, e.sel, e.device, rule ORDER BY sessions DESC, n DESC LIMIT ?",
        (since_day(days), kind, limit))]


def fields(con, days: int) -> dict:
    """Поля форм: скільки сесій торкались, середній час, частка заповнених, де кинули."""
    d0 = since_day(days)
    used = [dict(r) for r in con.execute(
        "SELECT e.sel form, e.str1 field, COUNT(DISTINCT e.sid) sessions, COUNT(*) focuses, "
        "CAST(AVG(e.n) AS INTEGER) avg_ms, ROUND(100.0*AVG(e.num1)) filled_pct "
        "FROM events e JOIN sessions s ON s.sid=e.sid WHERE e.day>=? AND e.kind='fd' AND s.bot=0 "
        "GROUP BY e.sel, e.str1 ORDER BY sessions DESC", (d0,))]
    abandoned = [dict(r) for r in con.execute(
        "SELECT e.sel form, e.str1 field, COUNT(DISTINCT e.sid) sessions "
        "FROM events e JOIN sessions s ON s.sid=e.sid WHERE e.day>=? AND e.kind='ab' AND s.bot=0 "
        "GROUP BY e.sel, e.str1 ORDER BY sessions DESC", (d0,))]
    return {"used": used, "abandoned": abandoned}


def errors(con, days: int, limit: int = 15) -> dict:
    d0 = since_day(days)
    js = [dict(r) for r in con.execute(
        "SELECT e.txt msg, e.sel src, e.n line, COUNT(*) n, COUNT(DISTINCT e.sid) sessions, "
        "MAX(e.path) path FROM events e WHERE e.day>=? AND e.kind='je' "
        "GROUP BY e.txt, e.sel, e.n ORDER BY sessions DESC LIMIT ?", (d0, limit))]
    api = [dict(r) for r in con.execute(
        "SELECT e.str1 api, e.n status, COUNT(*) n, CAST(AVG(e.num1) AS INTEGER) avg_ms "
        "FROM events e WHERE e.day>=? AND e.kind='ap' GROUP BY api, status "
        "ORDER BY (e.n >= 400) DESC, n DESC LIMIT 40", (d0,))]
    return {"js": js, "api": api}


def _p75(values: list):
    v = sorted(x for x in values if x is not None)
    return v[min(len(v) - 1, int(len(v) * 0.75))] if v else None


def vitals(con, days: int) -> list:
    """p75 LCP / INP / CLS за сторінкою й пристроєм (як рахує Google).
    Без замірів, де сторінка ховалась до LCP (n=1): LCP фонової вкладки — час до її відкриття."""
    groups: dict = {}
    for r in con.execute("SELECT path, device, num1, num2, num3 FROM events "
                         "WHERE day>=? AND kind='vt' AND COALESCE(n, 0)=0", (since_day(days),)):
        g = groups.setdefault((_page_group(r["path"]), r["device"] or "?"), ([], [], []))
        g[0].append(r["num1"])
        g[1].append(r["num2"])
        g[2].append(r["num3"])
    out = []
    for (page, dev), (lcp, inp, cls) in sorted(groups.items()):
        out.append({"page": page, "device": dev, "n": len(lcp), "lcp": _p75(lcp),
                    "inp": _p75(inp), "cls": _p75(cls)})
    return out


def _page_group(path: str | None) -> str:
    if path and path.startswith("/apartment/"):
        return "/apartment/*"
    return path or "?"


def heat_pages(con, days: int) -> list:
    """Сторінки, по яких є кліки, — для вибору в тепловій карті."""
    return [dict(r) for r in con.execute(
        "SELECT path, COUNT(*) n FROM events WHERE day>=? AND kind IN ('c','rg','dd') "
        "GROUP BY path ORDER BY n DESC LIMIT 30", (since_day(days),))]


def clicks(con, days: int, path: str, device: str, limit: int = 5000) -> list:
    return [[r["x"], r["y"], r["kind"]] for r in con.execute(
        "SELECT e.x, e.y, e.kind FROM events e JOIN sessions s ON s.sid=e.sid "
        "WHERE e.day>=? AND e.kind IN ('c','rg','dd') AND e.path=? AND e.device=? AND s.bot=0 "
        "ORDER BY e.id DESC LIMIT ?", (since_day(days), path, device, limit))]


def scroll_map(con, days: int, path: str, device: str) -> dict:
    """Яка частка переглядів дійшла до кожних 10 % сторінки."""
    rows = [r[0] for r in con.execute(
        "SELECT MAX(e.n) FROM events e JOIN sessions s ON s.sid=e.sid "
        "WHERE e.day>=? AND e.kind='sc' AND e.path=? AND e.device=? AND s.bot=0 GROUP BY e.pv",
        (since_day(days), path, device))]
    total = len(rows)
    bands = [{"pct": b, "reached": round(100 * sum(1 for m in rows if (m or 0) >= b) / total) if total else 0}
             for b in range(0, 101, 10)]
    return {"views": total, "bands": bands}


def scroll_summary(con, days: int) -> list:
    out = []
    for r in con.execute(
            "SELECT path, device, COUNT(*) views, CAST(AVG(m) AS INTEGER) avg_depth, "
            "ROUND(100.0*SUM(CASE WHEN m>=75 THEN 1 ELSE 0 END)/COUNT(*)) reach75 FROM "
            "(SELECT e.pv, e.path path, e.device device, MAX(e.n) m FROM events e JOIN sessions s "
            " ON s.sid=e.sid WHERE e.day>=? AND e.kind='sc' AND s.bot=0 GROUP BY e.pv) "
            "GROUP BY path, device ORDER BY views DESC LIMIT 20", (since_day(days),)):
        out.append(dict(r))
    return out


def recordings(con, days: int, limit: int = 100) -> list:
    return [dict(r) for r in con.execute(
        "SELECT r.sid, MIN(r.ts) start, MAX(r.ts) last, COUNT(DISTINCT r.pv) pages, "
        "SUM(r.size) bytes, s.device, s.landing, "
        "COALESCE(NULLIF(s.utm_source,''), NULLIF(s.ref,''), '') src, "
        "(SELECT COUNT(*) FROM events e WHERE e.sid=r.sid AND e.kind='rg') rage, "
        "(SELECT COUNT(*) FROM events e WHERE e.sid=r.sid AND e.kind='f' AND e.str1='submit') submit "
        "FROM recordings r JOIN sessions s ON s.sid=r.sid WHERE r.day>=? AND s.bot=0 "
        "GROUP BY r.sid ORDER BY start DESC LIMIT ?", (since_day(days), limit))]
