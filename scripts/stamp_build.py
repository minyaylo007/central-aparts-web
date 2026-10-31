#!/usr/bin/env python3
"""Stamp the build with the git commit it was made from → BUILD_INFO.json у корені
репозиторію (git-ignored, generated). Файл лежить поруч з `app.py`, тому їде і в
пакет Lambda (`infra/template.yaml`, CodeUri: ../), і в теку викату на коробці;
`/health` віддає його вміст. Це єдиний спосіб сказати, ЯКИЙ КОМІТ відповідає на
цьому домені і чи не відстав він від origin/master.

Виконувати ПІСЛЯ `git pull` і ПЕРЕД перезапуском юніта:
    python scripts/stamp_build.py

Образець — aparts/scripts/stamp_build.py (бек-офіс). Відмінність: тут гілка
master, тому непушені коміти рахуються від `origin/master`, не від `origin/main`.
"""
import json
import os
import subprocess
import sys
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "BUILD_INFO.json")


def _git(*args) -> str:
    try:
        return subprocess.check_output(["git", *args], cwd=ROOT, text=True, stderr=subprocess.DEVNULL,
                                       timeout=20).strip()
    except Exception:
        return ""


def main() -> int:
    sha = _git("rev-parse", "HEAD")
    info = {
        "git_sha": sha,
        "git_short": sha[:7],
        "git_branch": _git("rev-parse", "--abbrev-ref", "HEAD"),
        "git_subject": _git("log", "-1", "--pretty=%s")[:120],
        "git_dirty": bool(_git("status", "--porcelain")),
        "unpushed": bool(_git("log", "--oneline", "origin/master..HEAD")),
        "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "built_by": os.environ.get("COMPUTERNAME") or os.environ.get("HOSTNAME") or "",
    }
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(info, f, ensure_ascii=False, indent=1)
    print(json.dumps(info, ensure_ascii=False))
    if info["git_dirty"] or info["unpushed"]:
        # викат із незакоміченого/непушеного дерева робить прод невідтворюваним — кажемо голосно
        print("WARNING: working tree is dirty or HEAD is not pushed — commit + push before deploying.",
              file=sys.stderr)
        return 2 if os.environ.get("STAMP_STRICT") == "1" else 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
