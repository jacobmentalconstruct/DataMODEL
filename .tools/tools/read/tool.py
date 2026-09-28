from __future__ import annotations

import difflib
from pathlib import Path

from core.fswalk import human_size, read_text
from core.symbols import extract, find, notebook_view, suffix_of
from core.tool_runtime import MechanicalContext, run_tool

_MAX_LINE_CHARS = 2000


def _render(context: MechanicalContext, path: Path, arguments: dict, budget: int) -> tuple[str, bool]:
    relative = context.target_relative(path)
    if path.is_dir():
        return f"# {relative}/: is a directory (use ls)\n", False
    if not path.is_file():
        return f"# {relative}: not found\n", False
    text = read_text(path)
    if text is None:
        return f"# {relative}: binary file ({human_size(path.stat().st_size)}), not shown\n", False

    label = relative
    if suffix_of(relative) == ".ipynb" and not arguments.get("raw"):
        view = notebook_view(text)
        if view is not None:
            text, label = view, relative + " [notebook view; raw=true for JSON]"

    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    total = len(lines)
    start = int(arguments.get("offset", 1))
    limit = int(arguments.get("limit", 2000))
    wanted = arguments.get("symbol")
    if wanted:
        symbols = extract(relative, text)
        matches = find(symbols, wanted)
        if not matches:
            close = difflib.get_close_matches(wanted, [s.name for s in symbols], n=5, cutoff=0.5)
            hint = f"; close: {', '.join(close)}" if close else "; see outline"
            return f"# {relative}: no symbol {wanted!r}{hint}\n", False
        if len(matches) > 1:
            listed = ", ".join(f"{s.name} (L{s.line})" for s in matches)
            return f"# {relative}: {wanted!r} is ambiguous: {listed}\n", False
        symbol = matches[0]
        start, limit, label = symbol.line, symbol.end - symbol.line + 1, f"{relative}:{symbol.name}"
    numbers = arguments.get("numbers", True) is not False
    end = min(total, start - 1 + limit)

    body: list[str] = []
    used = 0
    stopped_at = None
    for number in range(start, end + 1):
        line = lines[number - 1]
        if len(line) > _MAX_LINE_CHARS:
            line = line[:_MAX_LINE_CHARS] + f"…[+{len(line) - _MAX_LINE_CHARS} chars]"
        rendered = f"{number}\t{line}" if numbers else line
        used += len(rendered) + 1
        if used > budget and body:
            stopped_at = number
            break
        body.append(rendered)

    shown_end = (stopped_at - 1) if stopped_at else end
    if total == 0:
        header = f"# {label} (empty)"
    elif start == 1 and shown_end == total:
        header = f"# {label} ({total} lines)"
    elif start > total:
        header = f"# {label} ({total} lines; offset {start} is past the end)"
    else:
        header = f"# {label} (lines {start}-{shown_end} of {total})"
    footer = ""
    truncated = bool(stopped_at) or (not wanted and shown_end < total and start <= total)
    if truncated:
        footer = f"\n# … more below; continue with offset={shown_end + 1}"
    return header + "\n" + "\n".join(body) + footer + "\n", truncated


def run(arguments: dict, context: MechanicalContext) -> dict:
    raw_paths = list(arguments.get("paths") or [])
    if "path" in arguments:
        raw_paths.insert(0, arguments["path"])
    if not raw_paths:
        return {"ok": False, "error": "path or paths is required"}
    budget = int(arguments.get("max_bytes", 256_000))
    chunks: list[str] = []
    truncated = False
    for raw in raw_paths:
        remaining = budget - sum(len(chunk) for chunk in chunks)
        if remaining <= 0:
            chunks.append(f"# {context.target_relative(raw)}: skipped, output budget exhausted\n")
            truncated = True
            continue
        chunk, cut = _render(context, Path(raw), arguments, remaining)
        chunks.append(chunk)
        truncated = truncated or cut
    return {
        "ok": True,
        "text": "".join(chunks).rstrip("\n"),
        "files": len(raw_paths),
        "truncated": truncated,
    }


if __name__ == "__main__":
    raise SystemExit(run_tool(run))
