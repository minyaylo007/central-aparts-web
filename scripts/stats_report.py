#!/usr/bin/env python3
"""Зведення своєї статистики гостей у markdown за N днів — ті самі агрегати, що в панелі /stats.

Запуск (на коробці):
    .venv/bin/python scripts/stats_report.py --days 7 > <тека звітів>/site-stats-$(date +%F).md
База — --db або ANALYTICS_DB (як у site.env). Читає лише агрегати: персональних даних у базі немає.
"""
import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import Config  # noqa: E402


def _dev(d) -> str:
    return {"m": "телефон", "d": "компьютер"}.get(d, "?")


def _pct(v) -> str:
    return "—" if v is None else f"{int(v)} %"


def _table(head: list, rows: list) -> list:
    if not rows:
        return ["_нет данных_", ""]
    out = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    out += ["| " + " | ".join(str(c) for c in r) + " |" for r in rows]
    return out + [""]


def _cell(s) -> str:
    return str(s or "").replace("|", "/").replace("\n", " ")


def build(days: int) -> str:
    from analytics import queries, store
    con = store.connect()
    try:
        ov = queries.overview(con, days)
        by_day = queries.visitors_by_day(con, days)
        fun = queries.funnel(con, days)
        L = [f"# Статистика сайта за {days} дн. ({queries.since_day(days)} — {time.strftime('%Y-%m-%d')})", ""]
        L += ["«Люди» — сессии с действием (клик, прокрутка, поле, шаг брони), без ботов.",
              "«Устройств (оценка)» — те же сессии, склеенные по ширине окна, языку браузера и источнику, "
              "если между ними меньше 30 мин: новая вкладка даёт новую сессию. Это оценка, не точный счёт.", ""]
        L += _table(["Людей", "Устройств (оценка)", "С телефона", "Просмотров", "Ушли без действий", "Ботов"],
                    [[ov["people"], queries.devices_estimate(con, days), ov["mobile"], ov["pages"],
                      ov["total"] - ov["people"] - ov["bots"], ov["bots"]]])
        L += ["## По дням", ""]
        L += _table(["День", "Людей", "Без действий", "Ботов"],
                    [[r["day"], r["people"], r["passive"], r["bots"]] for r in by_day if r["people"] or r["passive"] or r["bots"]])
        L += ["## Откуда пришли", ""]
        L += _table(["Источник", "Канал", "Людей"], [[_cell(s["src"]), _cell(s["medium"]), s["n"]] for s in queries.sources(con, days)])
        L += ["## Страницы", ""]
        L += _table(["Страница", "Просмотров", "Сессий"], [[f"`{p['path']}`", p["views"], p["sessions"]] for p in queries.top_pages(con, days)])
        L += ["## Воронка брони", ""]
        L += _table(["Шаг", "Сессий", "От первого", "Потеря на шаге"],
                    [[s["label"], s["n"], _pct(s["of_first_pct"]), _pct(s["loss_pct"])] for s in fun["steps"]])
        L += _table(["Рядом с воронкой", "Сессий"], [[s["label"], s["n"]] for s in fun["side"]])
        L += ["## Злые клики (3+ за секунду)", ""]
        L += _table(["Страница", "Устройство", "Элемент", "Текст", "Сессий", "Раз"],
                    [[f"`{r['path']}`", _dev(r["device"]), f"`{_cell(r['sel'])}`", _cell(r["txt"]), r["sessions"], r["n"]]
                     for r in queries.click_problems(con, days, "rg")])
        # правило dead поменялось 30.10 (analytics/clean.py, dd/dx): строки старого и нового не складывать
        L += ["## Мёртвые клики (нет реакции)", "",
              "Правило «новое» (с 30.10) — только по тому, что обещает действие: кнопка, ссылка, день календаря, "
              "курсор-рука. «Старое» — записи до 30.10: тогда сюда шли и тапы по декору, честно сравнивать нельзя.", ""]
        L += _table(["Страница", "Устройство", "Элемент", "Текст", "Правило", "Сессий", "Раз"],
                    [[f"`{r['path']}`", _dev(r["device"]), f"`{_cell(r['sel'])}`", _cell(r["txt"]),
                      "новое" if r["rule"] == 2 else "старое", r["sessions"], r["n"]]
                     for r in queries.click_problems(con, days, "dd")])
        L += ["## Тапы по декору без реакции", "", "Не ошибка сайта: подписи, фон, дни недели календаря. С 30.10.", ""]
        L += _table(["Страница", "Устройство", "Элемент", "Сессий", "Раз"],
                    [[f"`{r['path']}`", _dev(r["device"]), f"`{_cell(r['sel'])}`", r["sessions"], r["n"]]
                     for r in queries.click_problems(con, days, "dx")])
        f = queries.fields(con, days)
        L += ["## Поля форм", ""]
        L += _table(["Форма", "Поле", "Сессий", "Ср. время, с", "Заполнено"],
                    [[r["form"], r["field"], r["sessions"], round((r["avg_ms"] or 0) / 1000, 1), _pct(r["filled_pct"])] for r in f["used"]])
        L += ["## Где бросили форму", ""]
        L += _table(["Форма", "Последнее поле", "Сессий"], [[r["form"], r["field"], r["sessions"]] for r in f["abandoned"]])
        e = queries.errors(con, days)
        L += ["## Ошибки JavaScript", ""]
        L += _table(["Ошибка", "Где", "Сессий"], [[_cell(r["msg"]), f"`{r['src']}:{r['line']}`", r["sessions"]] for r in e["js"]])
        L += ["## Ответы /api/*", ""]
        L += _table(["Запрос", "Код", "Раз", "Ср. время, мс"], [[f"`{r['api']}`", r["status"], r["n"], r["avg_ms"]] for r in e["api"]])
        L += ["## Скорость (p75)", ""]
        L += _table(["Страница", "Устройство", "Замеров", "LCP, мс", "INP, мс", "CLS"],
                    [[f"`{v['page']}`", _dev(v["device"]), v["n"], v["lcp"], v["inp"], v["cls"]] for v in queries.vitals(con, days)])
        L += ["## Прокрутка", ""]
        L += _table(["Страница", "Устройство", "Просмотров", "Ср. глубина", "До 75 %"],
                    [[f"`{s['path']}`", _dev(s["device"]), s["views"], _pct(s["avg_depth"]), _pct(s["reach75"])] for s in queries.scroll_summary(con, days)])
        recs = queries.recordings(con, days)
        L += ["## Записи сессий", "", f"Записей: {len(recs)}; смотреть — в панели /stats.", ""]
    finally:
        con.close()
    return "\n".join(L)


def main() -> int:
    ap = argparse.ArgumentParser(description="Markdown-сводка своей статистики за N дней")
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--db", default=os.environ.get("ANALYTICS_DB", ""))
    a = ap.parse_args()
    if not a.db or not os.path.exists(a.db):
        print("нет базы: задайте --db или ANALYTICS_DB", file=sys.stderr)
        return 2
    Config.ANALYTICS_DB = a.db
    print(build(max(1, min(365, a.days))))
    return 0


if __name__ == "__main__":
    sys.exit(main())
