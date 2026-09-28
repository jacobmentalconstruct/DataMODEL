from __future__ import annotations

import bisect
import re

from core.fswalk import Glob, read_text, scope, walk
from core.tool_runtime import MechanicalContext, run_tool

_MAX_FILE_BYTES = 5_000_000
_MAX_LINE_CHARS = 300
_MAX_SPAN_LINES = 20


def _clip(line: str) -> str:
    return line if len(line) <= _MAX_LINE_CHARS else line[:_MAX_LINE_CHARS] + "…"


def _matching_lines(regex: re.Pattern, content: str, lines: list[str], multiline: bool) -> list[int]:
    """Zero-based indices of lines touched by a match, ascending and unique."""
    if not multiline:
        return [index for index, line in enumerate(lines) if regex.search(line)]
    starts = [0]
    for index, char in enumerate(content):
        if char == "\n":
            starts.append(index + 1)
    touched: set[int] = set()
    for match in regex.finditer(content):
        first = bisect.bisect_right(starts, match.start()) - 1
        last = bisect.bisect_right(starts, max(match.start(), match.end() - 1)) - 1
        touched.update(range(first, min(last, first + _MAX_SPAN_LINES - 1) + 1))
    return sorted(index for index in touched if index < len(lines))


def run(arguments: dict, context: MechanicalContext) -> dict:
    pattern = arguments["pattern"]
    flags = re.IGNORECASE if arguments.get("ignore_case") else 0
    multiline = bool(arguments.get("multiline"))
    if multiline:
        flags |= re.DOTALL | re.MULTILINE
    source = re.escape(pattern) if arguments.get("literal") else pattern
    try:
        regex = re.compile(source, flags)
    except re.error as exc:
        return {"ok": False, "error": f"invalid regex ({exc}); pass literal=true for plain text"}

    mode = arguments.get("mode", "content")
    context_lines = int(arguments.get("context", 0))
    limit = int(arguments.get("limit", 200))
    file_glob = Glob(arguments["glob"]) if arguments.get("glob") else None
    start = scope(context, arguments.get("path"))
    if not start.exists():
        return {"ok": False, "error": f"path not found: {context.target_relative(start)}"}

    out: list[str] = []
    entries = 0
    total_matches = 0
    files_matched = 0
    searched = 0
    skipped_binary = skipped_large = read_errors = 0
    truncated = False

    for entry in walk(context, start, include_ignored=bool(arguments.get("all"))):
        if entry.is_dir or entry.is_symlink:
            continue
        if file_glob and not file_glob.matches(entry.relative):
            continue
        try:
            if entry.path.stat().st_size > _MAX_FILE_BYTES:
                skipped_large += 1
                continue
            text = read_text(entry.path)
        except OSError:
            read_errors += 1
            continue
        if text is None:
            skipped_binary += 1
            continue
        searched += 1
        lines = text.split("\n")
        hits = _matching_lines(regex, text, lines, multiline)
        if not hits:
            continue
        files_matched += 1
        total_matches += len(hits)
        relative = entry.relative

        if mode == "files":
            out.append(relative)
            entries += 1
        elif mode == "count":
            out.append(f"{relative}:{len(hits)}")
            entries += 1
        else:
            hit_set = set(hits)
            shown: list[int] = []
            for index in hits:
                low = max(0, index - context_lines)
                high = min(len(lines) - 1, index + context_lines)
                for line_index in range(low, high + 1):
                    if not shown or line_index > shown[-1]:
                        shown.append(line_index)
            previous = None
            for line_index in shown:
                if entries >= limit and line_index in hit_set:
                    truncated = True
                    break
                if context_lines and previous is not None and line_index != previous + 1:
                    out.append("--")
                separator = ":" if line_index in hit_set else "-"
                out.append(f"{relative}{separator}{line_index + 1}{separator}{_clip(lines[line_index])}")
                if line_index in hit_set:
                    entries += 1
                previous = line_index
            if context_lines and not truncated:
                out.append("--")
        if entries >= limit:
            truncated = True
            break

    if out and out[-1] == "--":
        out.pop()
    limitations = []
    if skipped_binary:
        limitations.append(f"{skipped_binary} binary files skipped")
    if skipped_large:
        limitations.append(f"{skipped_large} files over {_MAX_FILE_BYTES} bytes skipped")
    if read_errors:
        limitations.append(f"{read_errors} unreadable files")
    if truncated:
        limitations.append(f"limit {limit} reached; more matches may exist")
    if not out:
        out.append(f"no matches ({searched} files searched)")
    notes = [note for note in limitations if "binary" not in note]
    if notes:
        out.append("# " + "; ".join(notes))
    return {
        "ok": True,
        "text": "\n".join(out),
        "matches": total_matches,
        "files_matched": files_matched,
        "files_searched": searched,
        "truncated": truncated,
        "limitations": limitations,
    }


if __name__ == "__main__":
    raise SystemExit(run_tool(run))
