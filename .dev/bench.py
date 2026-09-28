"""Reproducible measurements for the skeleton's own development (see .dev/PLAN.md).

    python .dev/bench.py [files=20000]

Copies the current .tools/ (without its private state) into a scratch project, then
measures: code size, `changes`/`mark` time and state size on a synthetic tree, and state
growth from repeated large reads. Prints a Markdown report; the scratch folder is removed.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / ".tools"
PRIVATE = {"state", "logs", "instance.json", "__pycache__"}


def code_size() -> tuple[int, list[tuple[int, str]]]:
    rows = []
    for path in sorted(TOOLS.rglob("*.py")):
        if "tests" in path.parts or "__pycache__" in path.parts:
            continue
        lines = len(path.read_text(encoding="utf-8").splitlines())
        rows.append((lines, path.relative_to(ROOT).as_posix()))
    return sum(n for n, _ in rows), sorted(rows, reverse=True)


def make_target(scratch: Path, files: int) -> Path:
    target = scratch / "target"
    shutil.copytree(TOOLS, target / ".tools", ignore=lambda d, names: [n for n in names if n in PRIVATE])
    suffixes = (".py", ".md", ".txt", ".json")
    for index in range(files):
        folder = target / "data" / f"d{index // 200:03d}"
        folder.mkdir(parents=True, exist_ok=True)
        name = f"f{index:05d}{suffixes[index % len(suffixes)]}"
        (folder / name).write_text(f"# file {index}\n" + "x = 1\n" * 40, encoding="utf-8")
    return target


def helpers(target: Path, *args: str) -> tuple[float, str]:
    started = time.perf_counter()
    result = subprocess.run([sys.executable, str(target / ".tools/bin/helpers.py"), *args],
                            capture_output=True, text=True, encoding="utf-8", cwd=target)
    elapsed = time.perf_counter() - started
    if result.returncode != 0:
        raise SystemExit(f"helpers {' '.join(args)} failed:\n{result.stdout}\n{result.stderr}")
    return elapsed, result.stdout


def state_bytes(target: Path) -> int:
    return sum(p.stat().st_size for p in (target / ".tools/state").glob("workbench.sqlite3*"))


def main(files: int) -> None:
    total, modules = code_size()
    print(f"# Bench ({time.strftime('%Y-%m-%d')}, Python {sys.version.split()[0]}, {files} files)\n")
    print(f"code: {total} lines in .tools (tests excluded); largest:")
    for lines, name in modules[:6]:
        print(f"  {lines:5d} {name}")
    with tempfile.TemporaryDirectory() as scratch:
        target = make_target(Path(scratch), files)
        timings = {}
        timings["changes (no baseline)"], _ = helpers(target, "changes")
        timings["mark (first)"], _ = helpers(target, "changes", "mark=true")
        timings["changes (idle)"], _ = helpers(target, "changes")
        for index in range(0, files, 100):  # edit 1% of the files
            path = target / "data" / f"d{index // 200:03d}"
            for candidate in path.glob(f"f{index:05d}.*"):
                candidate.write_text(candidate.read_text(encoding="utf-8") + "y = 2\n", encoding="utf-8")
        timings["changes (1% edited)"], _ = helpers(target, "changes", "limit=1")
        timings["mark (second)"], _ = helpers(target, "changes", "mark=true")
        after_marks = state_bytes(target)
        print("\ntimings:")
        for label, seconds in timings.items():
            print(f"  {seconds:6.2f}s {label}")
        print(f"state after 2 marks: {after_marks / 1e6:.1f} MB ({after_marks / files / 1024:.2f} KB/file)")

        big = target / "big.txt"
        big.write_text("".join(f"line {n} " + "z" * 60 + "\n" for n in range(4500)), encoding="utf-8")
        before = state_bytes(target)
        for _ in range(5):
            helpers(target, "read", "path=big.txt", "limit=5000", "max_bytes=400000")
        grown = state_bytes(target) - before
        print(f"state growth from 5 reads of a {big.stat().st_size // 1024} KB file: {grown // 1024} KB")


if __name__ == "__main__":
    arguments = dict(arg.split("=", 1) for arg in sys.argv[1:])
    main(int(arguments.get("files", 20000)))
