#!/usr/bin/env bash
# Локальні ворота сайту — та сама рамка, що в CI (.github/workflows/ci.yml): ruff check,
# повний pytest (tests/ e2e/test_fake_transport.py) і повна смока SMOKE_BACKEND=fake.
# Червоне — не зливати.
#
# Запуск: scripts/ci-local.sh  (з будь-якої теки; бере worktree, де лежить сам)
# Підсумок — останній рядок журналу і код виходу (0 — зелене, 1 — червоне).
#
# Без змінних середовища все запускається напряму. Машина може обгорнути кроки своєю рамкою:
#   CI_LOCAL_VENV        venv з requirements-dev.txt (типово ~/venvs/central-aparts-site)
#   CI_LOCAL_LOG_DIR     куди писати журнал (типово <repo>/tmp/ci-local)
#   CI_LOCAL_TMP         TMPDIR для кроків (типово <repo>/tmp)
#   CI_LOCAL_FRAME       префікс кожного кроку (обмеження пам'яті, таймаут тощо)
#   CI_LOCAL_SMOKE_WRAP  префікс смоки (черга важких прогонів); код 124 від нього =
#   CI_LOCAL_SMOKE_RETRY_WRAP  «не вклалась у швидку смугу» → повтор із цим префіксом
#   SMOKE_MAX_WORKERS    стеля воркерів Playwright (типово 4)
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV="${CI_LOCAL_VENV:-$HOME/venvs/central-aparts-site}"
PY="$VENV/bin/python"
RUFF="$VENV/bin/ruff"
FRAME_TMP="${CI_LOCAL_TMP:-$ROOT/tmp}"
SMOKE_PORT=8010
LOG_DIR="${CI_LOCAL_LOG_DIR:-$ROOT/tmp/ci-local}"
LOG="$LOG_DIR/site-ci-local-$(date +%Y-%m-%d-%H%M%S).log"
read -r -a FRAME <<< "${CI_LOCAL_FRAME:-}"
read -r -a SMOKE_WRAP <<< "${CI_LOCAL_SMOKE_WRAP:-}"
read -r -a SMOKE_RETRY_WRAP <<< "${CI_LOCAL_SMOKE_RETRY_WRAP:-}"

mkdir -p "$LOG_DIR" "$FRAME_TMP"
exec > >(tee -a "$LOG") 2>&1

frame() {
  TMPDIR="$FRAME_TMP" "${FRAME[@]}" "$@"
}

cd "$ROOT"
echo "== ci-local: $(git rev-parse --short HEAD) ($(git rev-parse --abbrev-ref HEAD)), $(date '+%F %T')"
echo "== журнал: $LOG"

ruff_res="не запускався"; pytest_res="не запускався"; smoke_res="не запускався"; red=0

echo; echo "== 1/3 ruff check"
if frame "$RUFF" check .; then ruff_res="ок"; else ruff_res="ПОМИЛКИ"; red=1; fi

echo; echo "== 2/3 pytest tests/ e2e/test_fake_transport.py"
out="$FRAME_TMP/ci-local-pytest.$$"
frame "$PY" -m pytest tests/ e2e/test_fake_transport.py -q -p no:cacheprovider --color=no | tee "$out"
rc=${PIPESTATUS[0]}
pytest_res="$(grep -E '^[=]* ?[0-9]+ (passed|failed)' "$out" | tail -1 | sed -E 's/^=+ //; s/ =+$//')"
rm -f "$out"
[ -n "$pytest_res" ] || pytest_res="без підсумку"
[ "$rc" -eq 0 ] || { red=1; pytest_res="ЧЕРВОНЕ ($pytest_res, код $rc)"; }

echo; echo "== 3/3 смока Playwright, SMOKE_BACKEND=fake"
cd "$ROOT/e2e"
if [ ! -d node_modules ]; then
  echo "-- npm ci (у worktree нема node_modules)"
  frame npm ci --no-audit --no-fund || { red=1; smoke_res="npm ci впав"; }
fi
if [ "$smoke_res" = "не запускався" ]; then
  if ss -ltn "sport = :$SMOKE_PORT" | grep -q LISTEN; then
    # чужий чи забутий gunicorn — не гасимо (лише за PID і лише свій), повідомляємо
    red=1; smoke_res="порт $SMOKE_PORT зайнятий — смоку не запускали (ss -ltnp)"
  else
    export SMOKE_BACKEND=fake SMOKE_PYTHON="$PY" FORCE_COLOR=0 NO_COLOR=1
    # стеля воркерів: вісім chromium не вміщаються в 2 ГБ пам'яті
    export SMOKE_MAX_WORKERS="${SMOKE_MAX_WORKERS:-4}"
    out="$FRAME_TMP/ci-local-smoke.$$"
    # HEAVY_CPUS може виставити обгортка смоки, тому — одинарні лапки
    SMOKE_CMD='w=${HEAVY_CPUS:-$SMOKE_MAX_WORKERS}; [ "$w" -le "$SMOKE_MAX_WORKERS" ] || w=$SMOKE_MAX_WORKERS; exec npx playwright test --workers="$w"'
    frame "${SMOKE_WRAP[@]}" bash -c "$SMOKE_CMD" | tee "$out"
    rc=${PIPESTATUS[0]}
    if [ "$rc" -eq 124 ] && [ "${#SMOKE_RETRY_WRAP[@]}" -gt 0 ]; then
      echo "-- код 124: смока не вклалась у швидку смугу, повтор через CI_LOCAL_SMOKE_RETRY_WRAP"
      frame "${SMOKE_RETRY_WRAP[@]}" bash -c "$SMOKE_CMD" | tee "$out"
      rc=${PIPESTATUS[0]}
    fi
    passed="$(grep -oE '^ *[0-9]+ passed' "$out" | tail -1 | tr -d ' ' | sed 's/passed/ passed/')"
    failed="$(grep -oE '^ *[0-9]+ (failed|flaky|skipped)' "$out" | tr -s ' ' | sed 's/^ //' | paste -sd, -)"
    rm -f "$out"
    smoke_res="${passed:-0 passed}${failed:+, $failed}"
    [ "$rc" -eq 0 ] || { red=1; smoke_res="ЧЕРВОНЕ ($smoke_res, код $rc)"; }
  fi
fi

echo
if [ "$red" -eq 0 ]; then verdict="ЗЕЛЕНЕ"; else verdict="ЧЕРВОНЕ"; fi
echo "ci-local $verdict: ruff $ruff_res; pytest $pytest_res; смока $smoke_res; коміт $(git -C "$ROOT" rev-parse --short HEAD); журнал $LOG"
exit "$red"
