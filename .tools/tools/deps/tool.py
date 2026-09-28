from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

from core.fswalk import read_text, scope, walk
from core.pyimports import (
    STDLIB, ModuleIndex, declared, distribution, imports_of, parse, python_source, top_level,
)
from core.symbols import suffix_of
from core.tool_runtime import MechanicalContext, run_tool

_MAX_FILE_BYTES = 2_000_000
_HTML_REF = re.compile(r"""\b(?:src|href)\s*=\s*["']([^"'#?]+)""", re.IGNORECASE)
_JS_REF = re.compile(r"""(?:\bfrom\s+|\bimport\s*\(?\s*|\brequire\s*\(\s*)["'](\.{1,2}/[^"']+)["']""")
_JS_EXTENSIONS = ("", ".js", ".mjs", ".ts", ".jsx", ".tsx", "/index.js", "/index.ts")


def _cycles(graph: dict[str, list[str]]) -> list[list[str]]:
    """Strongly connected components with more than one member (Tarjan, iterative)."""
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    stack: list[str] = []
    on_stack: set[str] = set()
    found: list[list[str]] = []
    counter = 0
    for root in sorted(graph):
        if root in index:
            continue
        work = [(root, iter(graph.get(root, [])))]
        index[root] = low[root] = counter
        counter += 1
        stack.append(root)
        on_stack.add(root)
        while work:
            node, children = work[-1]
            advanced = False
            for child in children:
                if child not in index:
                    index[child] = low[child] = counter
                    counter += 1
                    stack.append(child)
                    on_stack.add(child)
                    work.append((child, iter(graph.get(child, []))))
                    advanced = True
                    break
                if child in on_stack:
                    low[node] = min(low[node], index[child])
            if advanced:
                continue
            work.pop()
            if work:
                low[work[-1][0]] = min(low[work[-1][0]], low[node])
            if low[node] == index[node]:
                component = []
                while True:
                    member = stack.pop()
                    on_stack.discard(member)
                    component.append(member)
                    if member == node:
                        break
                if len(component) > 1:
                    found.append(sorted(component))
    return found


def _web_target(context: MechanicalContext, source: Path, reference: str, javascript: bool) -> tuple[str, bool] | None:
    if re.match(r"^([a-z][a-z0-9+.-]*:|//|\{|\$)", reference, re.IGNORECASE) or not reference.strip():
        return None  # external URL, protocol, or template expression
    base = context.target_root if reference.startswith("/") else source.parent
    candidate = (base / reference.lstrip("/")).resolve()
    options = [Path(str(candidate) + ext) for ext in _JS_EXTENSIONS] if javascript else [candidate]
    for option in options:
        try:
            relative = context.target_relative(option)
        except ValueError:
            return None
        if option.is_file():
            return relative, True
    try:
        return context.target_relative(candidate), False
    except ValueError:
        return None


def run(arguments: dict, context: MechanicalContext) -> dict:
    start = scope(context, arguments.get("path"))
    if not start.is_dir():
        return {"ok": False, "error": f"not a directory: {context.target_relative(start)}"}
    limit = int(arguments.get("limit", 300))
    focus = context.target_relative(arguments["module"]) if arguments.get("module") else None

    python: dict[str, str] = {}
    web: list[tuple[str, Path]] = []
    for entry in walk(context, start, include_ignored=bool(arguments.get("all"))):
        if entry.is_dir or entry.is_symlink:
            continue
        suffix = suffix_of(entry.relative)
        if suffix not in (".py", ".ipynb", ".html", ".htm", ".js", ".mjs", ".ts", ".jsx", ".tsx"):
            continue
        try:
            if entry.path.stat().st_size > _MAX_FILE_BYTES:
                continue
            text = read_text(entry.path)
        except OSError:
            continue
        if text is None:
            continue
        if suffix in (".py", ".ipynb"):
            python[entry.relative] = python_source(entry.relative, text)
        else:
            web.append((entry.relative, entry.path))

    index = ModuleIndex(list(python))
    graph: dict[str, list[str]] = {}
    external: dict[str, set[str]] = {}
    stdlib: set[str] = set()
    dynamic = unparsed = 0
    for relative, source in python.items():
        tree = parse(source)
        if tree is None:
            unparsed += 1
            continue
        found, dynamic_sites = imports_of(tree)
        dynamic += dynamic_sites
        targets: list[str] = []
        for item in found:
            files = index.resolve(relative, item)
            if files:
                targets.extend(files)
            elif item.level == 0:
                top = top_level(item)
                if top in STDLIB:
                    stdlib.add(top)
                elif top:
                    external.setdefault(top, set()).add(relative)
        graph[relative] = sorted(set(targets))
    importers: dict[str, list[str]] = {}
    for source_file, targets in graph.items():
        for target in targets:
            importers.setdefault(target, []).append(source_file)

    web_edges: dict[str, list[str]] = {}
    missing: list[str] = []
    for relative, path in web:
        text = read_text(path) or ""
        javascript = not relative.lower().endswith((".html", ".htm"))
        pattern = _JS_REF if javascript else _HTML_REF
        for reference in pattern.findall(text):
            resolved = _web_target(context, path, reference, javascript)
            if resolved is None:
                continue
            target, exists = resolved
            if exists:
                web_edges.setdefault(relative, []).append(target)
            else:
                missing.append(f"{relative} -> {target}")

    out: list[str] = []
    if focus:
        out.append(focus)
        out.append("imports: " + (", ".join(graph.get(focus, [])) or "(no project files)"))
        out.append("imported by: " + (", ".join(sorted(importers.get(focus, []))) or "(nothing)"))
        own = sorted(top for top, files in external.items() if focus in files)
        if own:
            out.append("external: " + ", ".join(own))
        if focus in web_edges:
            out.append("links: " + ", ".join(sorted(set(web_edges[focus]))))
        return {"ok": True, "text": "\n".join(out), "edges": len(graph.get(focus, [])),
                "cycles": 0, "truncated": False}

    edge_count = sum(len(targets) for targets in graph.values())
    cycles = _cycles(graph)
    out.append(f"python: {len(python)} files, {edge_count} internal edges, "
               f"{len(cycles)} cycle{'' if len(cycles) == 1 else 's'}")
    lines = [f"{source} -> {', '.join(targets)}" for source, targets in sorted(graph.items()) if targets]
    lines += [f"{source} -> {', '.join(sorted(set(targets)))}" for source, targets in sorted(web_edges.items())]
    truncated = len(lines) > limit
    out.extend(lines[:limit])
    if truncated:
        out.append(f"# … {len(lines) - limit} more edge lines; raise limit or narrow path")
    for component in cycles:
        out.append("cycle: " + " <-> ".join(component))
    orphans = sorted(f for f in graph if not graph[f] and f not in importers)
    if orphans and len(python) > 1:
        out.append(f"standalone ({len(orphans)}): " + ", ".join(orphans[:20]) + (" …" if len(orphans) > 20 else ""))

    if external:
        counted = Counter({top: len(files) for top, files in external.items()})
        out.append("external: " + ", ".join(f"{top} {n}" for top, n in counted.most_common()))
    if stdlib:
        out.append(f"stdlib ({len(stdlib)}): " + ", ".join(sorted(stdlib)))
    manifests, limitations = declared(context.target_root)
    if manifests:
        wanted = {distribution(top): top for top in external}
        declared_names = {name for names in manifests.values() for name in names}
        out.append("declared: " + "; ".join(f"{source} ({len(names)})" for source, names in manifests.items()))
        undeclared = sorted(top for dist, top in wanted.items() if dist not in declared_names)
        unused = sorted(name for name in declared_names if name not in wanted)
        if undeclared:
            out.append("imported, not declared: " + ", ".join(undeclared))
        if unused:
            out.append("declared, not imported: " + ", ".join(unused) + " (may be plugins/tools/renamed)")
    if missing:
        out.append(f"missing local links ({len(missing)}): " + "; ".join(missing[:20]))
    notes = limitations[:]
    if dynamic:
        notes.append(f"{dynamic} dynamic import sites not followed")
    if unparsed:
        notes.append(f"{unparsed} Python files with syntax errors skipped")
    if notes:
        out.append("# " + "; ".join(notes))
    return {"ok": True, "text": "\n".join(out), "edges": edge_count, "cycles": len(cycles),
            "truncated": truncated}


if __name__ == "__main__":
    raise SystemExit(run_tool(run))
