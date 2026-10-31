"""Відбір тестів для прогону за замовчуванням (`--quick`).

Повний набір іде 5 секунд, тож `--quick` тут не заради швидкості, а заради
фокуса: після правки видно одразу те, що ця правка могла зламати, і те, що
стереже гостя й гроші. Беремо об'єднання двох множин:

  * тести з маркером `critical` (pytest.ini);
  * усі тести з файлів `tests/`, змінених у цій гілці відносно бази
    (`origin/master`, інакше `master`) — разом з незакоміченими й новими.

Нема git або нема бази для порівняння — лишається саме `critical`, і про це
друкується рядок: мовчазне звуження набору гірше за повільний прогін.
"""
import subprocess
from pathlib import Path

ROOT = Path(__file__).parent.resolve()
_BASES = ("origin/master", "master")


def pytest_addoption(parser):
    parser.addoption(
        "--quick", action="store_true", default=False,
        help="прогін за замовчуванням: змінені в гілці тести + маркер critical",
    )


def _git(*args):
    """Рядки виводу git або None, якщо git не відповів."""
    try:
        out = subprocess.run(("git", "-C", str(ROOT)) + args, capture_output=True,
                             text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    return [line for line in out.stdout.splitlines() if line]


def _base_ref():
    for ref in _BASES:
        if _git("rev-parse", "--verify", "--quiet", ref):
            return ref
    return None


def _changed_files():
    """(множина змінених шляхів, пояснення для рядка звіту)."""
    base = _base_ref()
    if base is None:
        return set(), "бази для порівняння нема"
    changed = set()
    for args in (("diff", "--name-only", "--diff-filter=d", f"{base}...HEAD"),
                 ("diff", "--name-only", "--diff-filter=d", "HEAD"),
                 ("ls-files", "--others", "--exclude-standard")):
        lines = _git(*args)
        if lines is None:
            return set(), "git не відповів"
        changed.update(lines)
    return {(ROOT / p).resolve() for p in changed}, f"база {base}"


def pytest_collection_modifyitems(config, items):
    if not config.getoption("--quick"):
        return
    changed, why = _changed_files()
    selected, deselected = [], []
    for item in items:
        touched = Path(str(item.fspath)).resolve() in changed
        (selected if touched or item.get_closest_marker("critical")
         else deselected).append(item)
    if deselected:
        config.hook.pytest_deselected(items=deselected)
        items[:] = selected
    config._quick_note = (
        f"--quick: {len(selected)} з {len(selected) + len(deselected)} "
        f"(critical + змінені файли, {why})"
    )


def pytest_report_collectionfinish(config, items):
    note = getattr(config, "_quick_note", None)
    return [note] if note else []
