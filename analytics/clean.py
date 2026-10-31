"""Строга схема маяка /t і очистка від ПД.

Усе, що приходить від браузера, — недовірені дані. Тут вирішується, що з них
може лягти в базу: відомі види подій, відомі поля, межі довжин і чисел.
Невідоме поле не зберігається (значення поля форми, ім'я, телефон — усе це
«невідоме» за побудовою), рядки додатково чистяться від телефонів, пошти й
дат. IP, user-agent, повний реферер і query тут не з'являються взагалі.
"""
import re

SID_RE = re.compile(r"[a-z0-9]{8,32}")
PATH_RE = re.compile(r"/[A-Za-z0-9/_\-.~%]{0,200}")
HOST_RE = re.compile(r"[a-z0-9][a-z0-9.\-]{0,99}")
IDENT_RE = re.compile(r"[A-Za-z][A-Za-z0-9_\-]{0,39}")
API_RE = re.compile(r"/api/[A-Za-z0-9/_\-]{1,80}")
UUID_RE = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")

# телефон: 9+ цифр з пробілами, дужками, дефісами, крапками між ними
_PHONE_RE = re.compile(r"\+?\(?\d(?:[\s\-().]*\d){8,}")
_EMAIL_RE = re.compile(r"[\w.+\-]+@[\w\-]+\.[\w.\-]+")
_DATE_RE = re.compile(r"\b\d{1,4}[./\-]\d{1,2}[./\-]\d{1,4}\b")
_LONGNUM_RE = re.compile(r"\d{5,}")

FUNNEL_STEPS = ("home_dates", "dates", "price", "form", "submit", "coupon_try", "coupon_ok",
                "group_open", "group_quote", "group_submit")
KINDS = ("pv", "c", "rg", "dd", "dx", "sc", "f", "fd", "ab", "je", "ap", "vt")
UTM_KEYS = ("utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "ref")

MAX_EVENTS = 60


def scrub(text, limit: int) -> str:
    """Вільний текст (напис кнопки, текст помилки): без телефонів, пошти, дат і
    довгих чисел, пробіли стиснуті, обрізано до limit."""
    if not isinstance(text, str):
        return ""
    s = _EMAIL_RE.sub("[email]", text)
    s = _PHONE_RE.sub("[phone]", s)
    s = _DATE_RE.sub("[date]", s)
    s = _LONGNUM_RE.sub("[n]", s)
    return " ".join(s.split())[:limit]


def scrub_rec(node):
    """Запис rrweb: телефони й пошта в рядках замінюються зірочками, значення полів —
    теж. Поля форми й форму броні маскує вже браузер (maskAllInputs, maskTextSelector) —
    це друга лінія, на випадок тексту поза маскою. Структуру не чіпаємо."""
    if isinstance(node, str):
        if len(node) < 9:
            return node
        s = _EMAIL_RE.sub("***", node)
        return _PHONE_RE.sub("***", s)
    if isinstance(node, list):
        return [scrub_rec(v) for v in node]
    if isinstance(node, dict):
        out = {k: scrub_rec(v) for k, v in node.items()}
        # значення полів — навіть якщо маска браузера не спрацювала: вузол знімка
        # input/textarea/select (attributes.value) і подія введення (source 5, text)
        attrs = out.get("attributes")
        if (out.get("tagName") in ("input", "textarea", "select", "option") and isinstance(attrs, dict)
                and isinstance(attrs.get("value"), str)):
            attrs["value"] = "*" * min(len(attrs["value"]), 20)
        if out.get("source") == 5 and isinstance(out.get("text"), str):
            out["text"] = "*" * min(len(out["text"]), 20)
        return out
    return node


def clean_path(raw):
    """Шлях сторінки без query й фрагмента; сегмент з довгим числом (не UUID) —
    «:n», щоб у шлях не просочився телефон."""
    if not isinstance(raw, str):
        return None
    p = raw.split("?", 1)[0].split("#", 1)[0][:200]
    if not PATH_RE.fullmatch(p):
        return None
    segs = []
    for seg in p.split("/"):
        if UUID_RE.fullmatch(seg) or not re.search(r"\d{5,}", seg):
            segs.append(seg)
        else:
            segs.append(":n")
    return "/".join(segs)


def _num(v, lo, hi, *, integer=False):
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    if v != v or v < lo or v > hi:   # NaN і межі
        return None
    return int(v) if integer else round(float(v), 4)


def _ident(v):
    return v if isinstance(v, str) and IDENT_RE.fullmatch(v) else None


def _sel(v):
    """Селектор: лише символи CSS-селектора, без пробільних хвостів і чисел."""
    if not isinstance(v, str):
        return ""
    v = re.sub(r"[^A-Za-z0-9_\-#.> \[\]=:]", "", v)[:120]
    return _LONGNUM_RE.sub("[n]", v)


def clean_event(e: dict):
    """Одна подія → словник колонок таблиці events або None. Невідомі поля
    відкидаються мовчки; обов'язкове поле зі сміттям — відкидається подія."""
    if not isinstance(e, dict):
        return None
    k = e.get("k")
    if k not in KINDS:
        return None
    path = clean_path(e.get("u"))
    if path is None:
        return None
    out = {"kind": k, "path": path}
    if k == "pv":
        w = _num(e.get("w"), 100, 10000, integer=True)
        if w is None:
            return None
        out["w"] = w
        out["vh"] = _num(e.get("h"), 100, 10000, integer=True)
        lang = e.get("l")
        out["lang"] = lang.lower() if isinstance(lang, str) and re.fullmatch(r"[A-Za-z]{2}", lang) else ""
        sl = e.get("sl")
        out["site_lang"] = sl if sl in ("uk", "en") else ""
        r = e.get("r")
        out["ref"] = r.lower() if isinstance(r, str) and HOST_RE.fullmatch(r.lower()) else ""
        q = e.get("q") if isinstance(e.get("q"), dict) else {}
        out["utm"] = {key: scrub(q.get(key), 60) for key in UTM_KEYS if isinstance(q.get(key), str)}
        out["wd"] = 1 if e.get("wd") == 1 else 0
        return out
    if k in ("c", "rg", "dd", "dx"):
        x, y = _num(e.get("x"), 0, 1), _num(e.get("y"), 0, 60)
        if x is None or y is None:
            return None
        out.update(x=x, y=y, sel=_sel(e.get("s")), role=_ident(e.get("r")) or "",
                   txt=scrub(e.get("t"), 40),
                   n=_num(e.get("n"), 1, 100, integer=True) if k == "rg" else None)
        # dd/dx: версія правила мертвого кліку (num1). Старі записи — NULL = правило 1: dead на
        # будь-який тап, і по декору теж. Правило 2 (30.10): dd — лише по цілі, що обіцяє дію;
        # тап по декору без реакції — dx. Див. docs/PATTERNS.md §8a.
        if k in ("dd", "dx") and e.get("dv") == 2:
            out["num1"] = 2
        return out
    if k == "sc":
        m = _num(e.get("m"), 0, 100, integer=True)
        if m is None:
            return None
        # us — гість сам прокручував (а не лише відкрив коротку сторінку)
        out.update(n=m, num1=_num(e.get("hr"), 0, 60), num2=1 if e.get("us") == 1 else 0)
        return out
    if k == "f":
        st = e.get("st")
        if st not in FUNNEL_STEPS:
            return None
        out["str1"] = st
        return out
    if k in ("fd", "ab"):
        f = _ident(e.get("f"))
        if f is None:
            return None
        out.update(str1=f, sel=_ident(e.get("fm")) or "")
        if k == "fd":
            out.update(n=_num(e.get("ms"), 0, 3_600_000, integer=True),
                       num1=1 if e.get("fl") == 1 else 0)
        return out
    if k == "je":
        msg = scrub(e.get("m"), 200)
        if not msg:
            return None
        src = e.get("src")
        src = src.rsplit("/", 1)[-1].split("?", 1)[0] if isinstance(src, str) else ""
        out.update(txt=msg, sel=_sel(src)[:60], n=_num(e.get("ln"), 0, 10_000_000, integer=True))
        return out
    if k == "ap":
        a = e.get("a")
        a = a.split("?", 1)[0] if isinstance(a, str) else ""
        if not API_RE.fullmatch(a):
            return None
        # num2=1 — вкладка ховалась під час запиту (analytics.js, bg: 1): часу нема (num1
        # NULL, AVG його пропускає). У фоні браузер морозить вкладку, і відповідь, що прийшла
        # за 0,1 с, обробляється, коли гість повернеться: 22.10 — 97892 мс у /api/quote, а
        # сервер відповів менш ніж за 0,5 с.
        out.update(str1=clean_path(a) or "", n=_num(e.get("st"), 0, 999, integer=True),
                   num1=None if e.get("bg") == 1 else _num(e.get("ms"), 0, 600_000, integer=True),
                   num2=1 if e.get("bg") == 1 else None)
        return out
    if k == "vt":
        out.update(num1=_num(e.get("lcp"), 0, 600_000, integer=True),
                   num2=_num(e.get("inp"), 0, 600_000, integer=True),
                   num3=_num(e.get("cls"), 0, 100),
                   # n=1 — сторінка ховалась до LCP (фонова вкладка): замір не про швидкість сайту
                   n=1 if e.get("hb") == 1 else 0)
        if out["num1"] is None and out["num2"] is None and out["num3"] is None:
            return None
        return out
    return None


def clean_batch(body):
    """Конверт {v, s, p, e:[…]} або {v, s, p, rec:{q, u, e:[…]}} → (sid, pv, events, rec).
    Невірний конверт — None: весь запит мовчки відкидається."""
    if not isinstance(body, dict) or body.get("v") != 1:
        return None
    sid, pv = body.get("s"), body.get("p")
    if not (isinstance(sid, str) and SID_RE.fullmatch(sid)
            and isinstance(pv, str) and SID_RE.fullmatch(pv)):
        return None
    events = []
    raw = body.get("e")
    if isinstance(raw, list):
        for e in raw[:MAX_EVENTS]:
            ce = clean_event(e)
            if ce:
                events.append(ce)
    rec = None
    r = body.get("rec")
    if isinstance(r, dict):
        seq = _num(r.get("q"), 0, 100_000, integer=True)
        path = clean_path(r.get("u"))
        rev = r.get("e")
        if (seq is not None and path is not None and isinstance(rev, list) and rev
                and all(isinstance(x, dict) and isinstance(x.get("type"), int)
                        and isinstance(x.get("timestamp"), (int, float)) for x in rev)):
            rec = {"seq": seq, "path": path, "events": scrub_rec(rev)}
    if not events and rec is None:
        return None
    return sid, pv, events, rec


_BOT_RE = re.compile(
    r"bot|crawl|spider|slurp|headless|phantom|lighthouse|pingdom|uptime|monitor|curl|wget|"
    r"python|java/|go-http|httpclient|okhttp|axios|node-fetch|undici|facebookexternalhit|"
    r"preview|scanner|semrush|ahrefs|yandex|bingpreview|petal|gptbot|claude|perplexity",
    re.I)


def is_bot_ua(ua: str | None) -> bool:
    return not ua or len(ua) < 20 or bool(_BOT_RE.search(ua))


def device_of(width: int | None) -> str:
    return "m" if width and width < 768 else "d"
