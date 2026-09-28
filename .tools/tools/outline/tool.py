from __future__ import annotations

from core.fswalk import Glob, read_text, scope, walk
from core.symbols import extract, notebook_view, suffix_of
from core.tool_runtime import MechanicalContext, run_tool

_MAX_FILE_BYTES = 2_000_000
_MAX_SYMBOLS_PER_FILE = 150


def run(arguments: dict, context: MechanicalContext) -> dict:
    start = scope(context, arguments.get("path"))
    if not start.exists():
        return {"ok": False, "error": f"path not found: {context.target_relative(start)}"}
    file_glob = Glob(arguments["glob"]) if arguments.get("glob") else None
    want_symbols = arguments.get("symbols", True) is not False
    limit = int(arguments.get("limit", 300))
    depth = arguments.get("depth")
    base = "" if start == context.target_root or not start.is_dir() else context.target_relative(start) + "/"

    out: list[str] = []
    files = symbol_total = binary = 0
    truncated = False
    for entry in walk(context, start, include_ignored=bool(arguments.get("all")),
                      max_depth=int(depth) if depth is not None else None):
        if entry.is_dir or entry.is_symlink:
            continue
        if file_glob and not file_glob.matches(entry.relative[len(base):]):
            continue
        if files >= limit:
            truncated = True
            break
        try:
            if entry.path.stat().st_size > _MAX_FILE_BYTES:
                out.append(f"{entry.relative} (large, not parsed)")
                files += 1
                continue
            text = read_text(entry.path)
        except OSError:
            continue
        if text is None:
            binary += 1
            continue
        files += 1
        if suffix_of(entry.relative) == ".ipynb":
            text = notebook_view(text) or text
        count = text.count("\n") + (0 if text.endswith("\n") or not text else 1)
        out.append(f"{entry.relative} ({count} lines)")
        if not want_symbols:
            continue
        symbols = extract(entry.relative, text)
        symbol_total += len(symbols)
        for symbol in symbols[:_MAX_SYMBOLS_PER_FILE]:
            out.append(f"{'  ' * (symbol.level + 1)}{symbol.line} {symbol.label}")
        if len(symbols) > _MAX_SYMBOLS_PER_FILE:
            out.append(f"  … {len(symbols) - _MAX_SYMBOLS_PER_FILE} more symbols")

    if truncated:
        out.append(f"# file limit {limit} reached; narrow path/glob or raise limit")
    if binary:
        out.append(f"# {binary} binary files omitted")
    if not out:
        out.append("(no text files)")
    return {"ok": True, "text": "\n".join(out), "files": files, "symbols": symbol_total,
            "truncated": truncated}


if __name__ == "__main__":
    raise SystemExit(run_tool(run))
