from __future__ import annotations

from core.fswalk import Entry, Glob, human_size, scope, walk
from core.tool_runtime import MechanicalContext, run_tool

_COUNT_CAP = 100_000


def _line(entry: Entry, sizes: bool, hidden: dict[str, int]) -> str:
    if entry.is_dir:
        text = entry.relative + ("@/" if entry.is_symlink else "/")
        if entry.relative in hidden:
            text += f" (+{hidden[entry.relative]} files)"
        return text
    text = entry.relative + ("@" if entry.is_symlink else "")
    if sizes:
        try:
            text += "  " + human_size(entry.path.stat().st_size)
        except OSError:
            text += "  ?"
    return text


def run(arguments: dict, context: MechanicalContext) -> dict:
    start = scope(context, arguments.get("path"))
    if not start.is_dir():
        return {"ok": False, "error": f"not a directory: {context.target_relative(start)}"}
    file_glob = Glob(arguments["glob"]) if arguments.get("glob") else None
    kind = arguments.get("kind", "file" if file_glob else "any")
    depth = arguments.get("depth")
    max_depth = int(depth) if depth is not None else (None if file_glob else 2)
    limit = int(arguments.get("limit", 500))
    include_ignored = bool(arguments.get("all"))
    base = "" if start == context.target_root else context.target_relative(start) + "/"

    selected: list[Entry] = []
    hidden: dict[str, int] = {}
    cut_dirs: list[Entry] = []
    for entry in walk(context, start, include_ignored=include_ignored, max_depth=max_depth):
        if entry.is_dir and max_depth is not None and entry.depth == max_depth:
            cut_dirs.append(entry)
        if kind == "file" and entry.is_dir or kind == "dir" and not entry.is_dir:
            continue
        if file_glob and not file_glob.matches(entry.relative[len(base):]):
            continue
        selected.append(entry)

    for directory in cut_dirs:
        count = 0
        for inner in walk(context, directory.path, include_ignored=include_ignored):
            if not inner.is_dir:
                count += 1
                if count >= _COUNT_CAP:
                    break
        if count:
            hidden[directory.relative] = count

    if arguments.get("sort") == "mtime":
        def modified(entry: Entry) -> float:
            try:
                return entry.path.stat().st_mtime
            except OSError:
                return 0.0

        selected.sort(key=modified, reverse=True)

    truncated = len(selected) > limit
    shown = selected[:limit]
    sizes = bool(arguments.get("sizes"))
    lines = [_line(entry, sizes, hidden) for entry in shown]
    if truncated:
        lines.append(f"# … {len(selected) - limit} more; raise limit or narrow path/glob")
    if not lines:
        lines.append("(no entries)")
    return {
        "ok": True,
        "text": "\n".join(lines),
        "returned": len(shown),
        "truncated": truncated,
    }


if __name__ == "__main__":
    raise SystemExit(run_tool(run))
