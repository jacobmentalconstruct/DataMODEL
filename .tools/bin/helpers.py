from __future__ import annotations

import json
import sys
from pathlib import Path

sys.dont_write_bytecode = True
# Tool output is UTF-8 text; a Windows console/pipe default (cp1252) would fail on it.
for _stream in (sys.stdin, sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

INSTANCE_ROOT = Path(__file__).resolve().parents[1]
if str(INSTANCE_ROOT) not in sys.path:
    sys.path.insert(0, str(INSTANCE_ROOT))

from core.constants import python_support  # noqa: E402



def _refuse_known_incompatible_python() -> None:
    """Refuse only an interpreter measured to break, and say nothing about the rest.

    An unverified interpreter is not refused: it is disclosed through `status`. Refusing
    versions merely because they are unfamiliar would assert something never measured,
    which is the failure this product exists to avoid.
    """
    support, detail = python_support()
    if support != "incompatible":
        return
    print(
        json.dumps(
            {
                "ok": False,
                "error": {
                    "code": "incompatible_python",
                    "message": detail,
                    "detail": {
                        "running": "%d.%d.%d" % sys.version_info[:3],
                        "executable": sys.executable,
                    },
                },
            }
        )
    )
    raise SystemExit(1)


_refuse_known_incompatible_python()

from core.cli import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main(INSTANCE_ROOT, sys.argv[1:]))
