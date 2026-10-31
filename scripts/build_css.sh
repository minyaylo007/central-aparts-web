#!/usr/bin/env bash
# Перезбирає CSS першого екрана (22.10): static/vendor/bootstrap-lite.css,
# static/vendor/flatpickr-<версія>.min.css і static/critical/<група>.css (home, apartment, other).
#
# Запускати після КОЖНОЇ правки static/styles.css, шаблонів або класів у main.js —
# tests/test_critical_css.py ловить застарілий static/critical/*.css за відбитком джерел.
#
#   scripts/build_css.sh            # у рамці прогону з CLAUDE.md
#
# Сторінки рендерить тестовий клієнт Flask на підмінному бек-офісі (без мережі й порту),
# браузер — chromium Playwright сайту (e2e/browsers-env.cjs). Живих броней не створює.
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
PY="${PYTHON:-$HOME/venvs/central-aparts-site/bin/python}"
PAGES="$(mktemp -d "${TMPDIR:-/tmp}/css-pages.XXXXXX")"
trap 'rm -rf "$PAGES"' EXIT

"$PY" "$REPO/scripts/dump_pages.py" "$PAGES"
node "$REPO/e2e/tools/build-css.mjs" "$PAGES"
