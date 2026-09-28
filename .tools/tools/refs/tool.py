from __future__ import annotations

import ast
import re

from core.fswalk import Glob, read_text, scope, walk
from core.pyimports import parse
from core.symbols import clip, notebook_view, suffix_of
from core.tool_runtime import MechanicalContext, run_tool

_MAX_FILE_BYTES = 2_000_000
_SECTIONS = ("def", "import", "call", "use", "text")


def _python_hits(tree: ast.Module, qualified: str, name: str) -> dict[str, set[int]]:
    hits: dict[str, set[int]] = {section: set() for section in _SECTIONS[:4]}
    called = {id(node.func) for node in ast.walk(tree) if isinstance(node, ast.Call)}

    def visit_defs(body: list[ast.stmt], prefix: str) -> None:
        for node in body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                qualname = prefix + node.name
                if node.name == name and (qualified == name or qualname.endswith(qualified)):
                    hits["def"].add(min([node.lineno] + [d.lineno for d in node.decorator_list]))
                visit_defs(node.body, qualname + ".")
            elif isinstance(node, (ast.Assign, ast.AnnAssign)) and not prefix:
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                if any(isinstance(t, ast.Name) and t.id == name for t in targets):
                    hits["def"].add(node.lineno)

    visit_defs(tree.body, "")
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            module = getattr(node, "module", None) or ""
            if any(name in (alias.name.split(".")[-1], alias.asname) for alias in node.names) \
                    or module.split(".")[-1] == name:
                hits["import"].add(node.lineno)
        elif isinstance(node, ast.Name) and node.id == name:
            hits["call" if id(node) in called else "use"].add(node.lineno)
        elif isinstance(node, ast.Attribute) and node.attr == name:
            hits["call" if id(node) in called else "use"].add(node.lineno)
    hits["use"] -= hits["call"] | hits["def"]
    hits["call"] -= hits["def"]
    return hits


def run(arguments: dict, context: MechanicalContext) -> dict:
    qualified = arguments["name"].strip()
    name = qualified.split(".")[-1]
    if not re.fullmatch(r"[A-Za-z_$][\w$]*", name):
        return {"ok": False, "error": "name must be an identifier (optionally Class.method)"}
    start = scope(context, arguments.get("path"))
    if not start.exists():
        return {"ok": False, "error": f"path not found: {context.target_relative(start)}"}
    file_glob = Glob(arguments["glob"]) if arguments.get("glob") else None
    include_text = arguments.get("text", True) is not False
    limit = int(arguments.get("limit", 150))
    word = re.compile(r"(?<![\w$])" + re.escape(name) + r"(?![\w$])")
    base = "" if start == context.target_root or not start.is_dir() else context.target_relative(start) + "/"

    rows: dict[str, list[str]] = {section: [] for section in _SECTIONS}
    unparsed = 0
    for entry in walk(context, start, include_ignored=bool(arguments.get("all"))):
        if entry.is_dir or entry.is_symlink:
            continue
        if file_glob and not file_glob.matches(entry.relative[len(base):]):
            continue
        try:
            if entry.path.stat().st_size > _MAX_FILE_BYTES:
                continue
            text = read_text(entry.path)
        except OSError:
            continue
        if text is None or name not in text:
            continue
        suffix = suffix_of(entry.relative)
        if suffix == ".ipynb":
            text = notebook_view(text) or text
        lines = text.split("\n")
        tree = parse(text) if suffix in (".py", ".pyw", ".pyi") else None
        if tree is not None:
            for section, numbers in _python_hits(tree, qualified, name).items():
                rows[section].extend(f"{entry.relative}:{n} {clip(lines[n - 1])}" for n in sorted(numbers))
            continue
        if suffix in (".py", ".pyw", ".pyi"):
            unparsed += 1
        if include_text:
            rows["text"].extend(
                f"{entry.relative}:{n} {clip(line)}"
                for n, line in enumerate(lines, 1) if word.search(line)
            )

    counts = {section: len(rows[section]) for section in _SECTIONS if rows[section]}
    summary = ", ".join(f"{count} {section}" for section, count in counts.items()) or "no references"
    out = [f"refs {qualified!r}: {summary}"]
    shown = 0
    truncated = False
    for section in _SECTIONS:
        if not rows[section]:
            continue
        out.append(f"{section}:")
        for row in rows[section]:
            if shown >= limit:
                truncated = True
                break
            out.append("  " + row)
            shown += 1
    if truncated:
        out.append(f"# limit {limit} reached; narrow path/glob or raise limit")
    if unparsed:
        out.append(f"# {unparsed} Python files had syntax errors; searched as text")
    return {"ok": True, "text": "\n".join(out), "counts": counts, "truncated": truncated}


if __name__ == "__main__":
    raise SystemExit(run_tool(run))
