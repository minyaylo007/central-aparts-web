"""Нижні межі безпеки залежностей: ні requirements.txt, ні requirements.lock не пускають
certifi, urllib3 і Werkzeug нижче версій із виправленнями (скан 31.10.2026: прод-venv
стояв на certifi 2026.7.22 — GHSA-6q48-6c7w-9gw2, підміна TLS до бек-офісу).

Без мережі: лише розбір файлів. requirements.txt — діапазони (CI, Lambda), lock — точні
версії, які ставить викат (deploy/README.md, «Як оновлюється»)."""
import re
from pathlib import Path

import pytest
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name
from packaging.version import Version

ROOT = Path(__file__).resolve().parent.parent

# Підняти можна; опустити — лише разом із поясненням, чому версія вже не вразлива.
SAFE_FLOORS = {
    "certifi": Version("2026.10.19"),
    "urllib3": Version("2.8"),
    "werkzeug": Version("3.1.9"),
}


def parse_requirements(text: str) -> dict[str, Requirement]:
    """requirements.txt → {канонічне ім'я: Requirement}; коментарі й -r/-c пропускаються."""
    reqs = {}
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or line.startswith("-"):
            continue
        req = Requirement(line)
        reqs[canonicalize_name(req.name)] = req
    return reqs


def parse_lock(text: str) -> dict[str, Version]:
    """requirements.lock (pip-compile --generate-hashes) → {ім'я: прибита версія}."""
    pins = {}
    for line in text.splitlines():
        m = re.match(r"^([A-Za-z0-9][A-Za-z0-9._-]*)==([^\s\\;]+)", line)
        if m:
            pins[canonicalize_name(m.group(1))] = Version(m.group(2))
    return pins


def floor_violations(reqs: dict[str, Requirement]) -> list[str]:
    """Пакети з SAFE_FLOORS, яким специфікатор дозволяє версію нижче межі (або межі нема)."""
    bad = []
    for name, floor in SAFE_FLOORS.items():
        req = reqs.get(name)
        if req is None:
            bad.append(f"{name}: нема в файлі")
            continue
        lower = [Version(s.version) for s in req.specifier
                 if s.operator in (">=", "==", "~=", "===")]
        lower += [Version(s.version) for s in req.specifier if s.operator == ">"]
        if not lower or max(lower) < floor:
            bad.append(f"{name}: {req.specifier or 'без меж'} < {floor}")
    return bad


def lock_violations(pins: dict[str, Version]) -> list[str]:
    return [f"{name}: {pins.get(name)} < {floor}" for name, floor in SAFE_FLOORS.items()
            if name not in pins or pins[name] < floor]


@pytest.mark.critical
def test_requirements_txt_keeps_safe_floors():
    reqs = parse_requirements((ROOT / "requirements.txt").read_text(encoding="utf-8"))
    assert floor_violations(reqs) == []


@pytest.mark.critical
def test_lock_pins_safe_versions():
    pins = parse_lock((ROOT / "requirements.lock").read_text(encoding="utf-8"))
    assert lock_violations(pins) == []


def test_lock_satisfies_requirements_txt():
    """Lock не відстав від requirements.txt: кожна пряма залежність прибита у версії,
    яку дозволяє діапазон. Змінив requirements.txt — перегенеруй lock (deploy/README.md)."""
    reqs = parse_requirements((ROOT / "requirements.txt").read_text(encoding="utf-8"))
    pins = parse_lock((ROOT / "requirements.lock").read_text(encoding="utf-8"))
    stale = [f"{name}: {pins.get(name)} не в {req.specifier}" for name, req in reqs.items()
             if name not in pins or not req.specifier.contains(pins[name], prereleases=True)]
    assert stale == []


def test_lock_has_hash_for_every_pin():
    """--require-hashes у команді викату впаде на пакеті без хеша — ловимо тут, не на проді."""
    text = (ROOT / "requirements.lock").read_text(encoding="utf-8")
    blocks = re.split(r"\n(?=[A-Za-z0-9])", text)
    unhashed = [b.split()[0] for b in blocks
                if re.match(r"^[A-Za-z0-9].*==", b) and "--hash=sha256:" not in b]
    assert unhashed == []


@pytest.mark.parametrize("text, expected", [
    # Межу прибрали або опустили — тест мусить це побачити.
    ("certifi\nurllib3>=2.8\nWerkzeug>=3.1.9\n", ["certifi: без меж < 2026.10.19"]),
    ("certifi>=2026.7.22\nurllib3>=2.8\nWerkzeug>=3.1.9\n",
     ["certifi: >=2026.7.22 < 2026.10.19"]),
    ("certifi>=2026.10.19\nurllib3>=2.0,<3\nWerkzeug>=3.1.9\n",
     ["urllib3: <3,>=2.0 < 2.8"]),
    ("certifi>=2026.10.19\nurllib3>=2.8\n", ["werkzeug: нема в файлі"]),
    ("certifi>=2026.10.19\nurllib3==2.8.0\nwerkzeug>=3.2\n", []),
])
def test_floor_check_catches_lowered_bounds(text, expected):
    assert floor_violations(parse_requirements(text)) == expected


def test_lock_check_catches_old_pin():
    pins = parse_lock("certifi==2026.7.22 \\\n    --hash=sha256:00\n"
                      "urllib3==2.8.0 \\\nwerkzeug==3.1.9 \\\n")
    assert lock_violations(pins) == ["certifi: 2026.7.22 < 2026.10.19"]
