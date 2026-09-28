from __future__ import annotations

import difflib
import os
from pathlib import Path

from core.tool_runtime import MechanicalContext, run_tool


def _ranges(numbers: list[int]) -> str:
    spans: list[list[int]] = []
    for number in numbers:
        if spans and spans[-1][1] + 1 == number:
            spans[-1][1] = number
        else:
            spans.append([number, number])
    return ", ".join(str(low) if low == high else f"{low}-{high}" for low, high in spans)


def _changed_lines(before: str, after: str) -> list[int]:
    matcher = difflib.SequenceMatcher(a=before.split("\n"), b=after.split("\n"), autojunk=False)
    changed: list[int] = []
    for tag, _, _, low, high in matcher.get_opcodes():
        if tag == "equal":
            continue
        if high > low:
            changed.extend(range(low + 1, high + 1))
        else:
            changed.append(max(1, low))  # pure deletion: point at the join
    return sorted(set(changed))


def run(arguments: dict, context: MechanicalContext) -> dict:
    path = Path(arguments["path"])
    relative = context.target_relative(path)
    edits = list(arguments.get("edits") or [])
    if "old" in arguments:
        edits.insert(0, {"old": arguments["old"], "new": arguments.get("new", ""),
                         "all": arguments.get("all", False)})
    if not edits:
        return {"ok": False, "error": "provide old/new or edits[]"}
    if not path.is_file():
        return {"ok": False, "error": f"not a file: {relative} (use write_file to create)"}

    raw = path.read_bytes()
    bom = raw.startswith(b"\xef\xbb\xbf")
    try:
        original = raw[3 if bom else 0 :].decode("utf-8")
    except UnicodeDecodeError:
        return {"ok": False, "error": f"{relative} is not valid UTF-8; refusing to edit"}
    crlf = original.count("\r\n")
    newline = "\r\n" if crlf and crlf * 2 >= original.count("\n") else "\n"
    before = original.replace("\r\n", "\n") if crlf else original

    text = before
    replacements = 0
    for index, edit in enumerate(edits, start=1):
        old = edit["old"].replace("\r\n", "\n")
        new = edit.get("new", "").replace("\r\n", "\n")
        label = f"edit {index}" if len(edits) > 1 else "old"
        if old == new:
            return {"ok": False, "error": f"{label}: old and new are identical"}
        count = text.count(old)
        if count == 0:
            hint = ""
            first = old.strip().split("\n")[0].strip()
            if first and first in text:
                line = text[: text.index(first)].count("\n") + 1
                hint = f"; its first line appears at line {line} (check whitespace/following lines)"
            return {"ok": False, "error": f"{label}: text not found in {relative}{hint}; file unchanged"}
        if count > 1 and not edit.get("all"):
            return {"ok": False, "error": f"{label}: matches {count} times; add context or set all=true; file unchanged"}
        text = text.replace(old, new) if edit.get("all") else text.replace(old, new, 1)
        replacements += count if edit.get("all") else 1

    output = text.replace("\n", "\r\n") if newline == "\r\n" else text
    encoded = (b"\xef\xbb\xbf" if bom else b"") + output.encode("utf-8")
    temporary = path.with_name(path.name + ".edit-tmp")
    temporary.write_bytes(encoded)
    os.replace(temporary, path)

    changed = _changed_lines(before, text)
    total = text.count("\n") + (0 if text.endswith("\n") else 1)
    summary = f"edited {relative}: {replacements} replacement{'s' if replacements != 1 else ''}"
    if changed:
        summary += f"; changed lines {_ranges(changed)}"
    summary += f"; now {total} lines"
    return {
        "ok": True,
        "text": summary,
        "handle": f"path:{relative}",
        "replacements": replacements,
    }


if __name__ == "__main__":
    raise SystemExit(run_tool(run))
