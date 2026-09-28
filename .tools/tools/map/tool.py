from __future__ import annotations

import json
import re
from collections import Counter, defaultdict

from core.fswalk import PathFilter, human_size, read_text, scope, walk
from core.pyimports import (
    STDLIB, Import, ModuleIndex, category_of, declared, imports_of, parse, python_source, top_level,
)
from core.symbols import notebook_view, suffix_of
from core.tool_runtime import MechanicalContext, run_tool

_MAX_TEXT_BYTES = 2_000_000
LANGUAGES = {
    ".py": "Python", ".pyi": "Python", ".ipynb": "Notebook", ".html": "HTML", ".htm": "HTML",
    ".jinja": "HTML", ".j2": "HTML", ".css": "CSS", ".scss": "CSS", ".js": "JavaScript",
    ".mjs": "JavaScript", ".jsx": "JavaScript", ".ts": "TypeScript", ".tsx": "TypeScript",
    ".md": "Markdown", ".rst": "reST", ".txt": "Text", ".json": "JSON", ".yaml": "YAML",
    ".yml": "YAML", ".toml": "TOML", ".sql": "SQL", ".sh": "Shell", ".ps1": "PowerShell",
    ".bat": "Batch", ".go": "Go", ".rs": "Rust", ".java": "Java", ".cs": "C#", ".c": "C",
    ".h": "C", ".cpp": "C++", ".rb": "Ruby", ".php": "PHP", ".r": "R", ".jl": "Julia",
}
DATA_KINDS = {
    "table": {".csv", ".tsv", ".parquet", ".feather", ".arrow", ".xlsx", ".xls", ".jsonl", ".ndjson"},
    "database": {".sqlite", ".sqlite3", ".db", ".duckdb"},
    "array": {".npy", ".npz", ".h5", ".hdf5", ".pkl", ".pickle", ".joblib", ".zarr"},
    "graph": {".graphml", ".gexf", ".gml", ".dot", ".gv", ".ttl", ".rdf", ".owl", ".nt", ".nq", ".jsonld", ".cypher"},
    "model": {".gguf", ".safetensors", ".pt", ".pth", ".onnx", ".ckpt", ".bin", ".tflite"},
    "vector": {".faiss", ".index", ".ann", ".lance"},
    "media": {".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".mp3", ".wav", ".mp4", ".pdf"},
}
CONFIG_NAMES = {
    "pyproject.toml", "setup.py", "setup.cfg", "requirements.txt", "package.json", "Dockerfile",
    "docker-compose.yml", "compose.yaml", "Makefile", ".env", ".env.example", ".mcp.json", "Modelfile",
    "environment.yml", "tox.ini", "pytest.ini", ".gitignore", "uv.lock", "poetry.lock", "Pipfile",
}
_TEST_FILE = re.compile(r"(^|/)(test_[^/]*\.py|[^/]*_test\.py|[^/]*\.(spec|test)\.[jt]sx?)$")
_MAIN = re.compile(r"^if\s+__name__\s*==\s*['\"]__main__['\"]", re.MULTILINE)


def _kind(suffix: str) -> str | None:
    return next((kind for kind, suffixes in DATA_KINDS.items() if suffix in suffixes), None)


def run(arguments: dict, context: MechanicalContext) -> dict:
    start = scope(context, arguments.get("path"))
    if not start.is_dir():
        return {"ok": False, "error": f"not a directory: {context.target_relative(start)}"}
    include_ignored = bool(arguments.get("all"))

    files = 0
    total_size = 0
    languages: dict[str, list[int]] = defaultdict(lambda: [0, 0])  # files, lines
    data: dict[str, list[int]] = defaultdict(lambda: [0, 0])  # files, bytes
    data_dirs: Counter = Counter()
    top_dirs: Counter = Counter()
    sizes: list[tuple[int, str]] = []
    entry_points: list[str] = []
    tests: list[str] = []
    configs: list[str] = []
    docs: list[str] = []
    libraries: dict[str, set[str]] = defaultdict(set)
    third_party: Counter = Counter()
    python_files: list[str] = []
    base = "" if start == context.target_root else context.target_relative(start) + "/"

    for entry in walk(context, start, include_ignored=include_ignored):
        if entry.is_dir or entry.is_symlink:
            continue
        local = entry.relative[len(base):]
        name = local.rsplit("/", 1)[-1]
        suffix = suffix_of(local)
        try:
            size = entry.path.stat().st_size
        except OSError:
            continue
        files += 1
        total_size += size
        sizes.append((size, entry.relative))
        top_dirs[local.split("/", 1)[0] + "/" if "/" in local else "."] += 1
        if name in CONFIG_NAMES or name.startswith("requirements") and suffix == ".txt":
            configs.append(entry.relative)
        if name.lower().startswith("readme"):
            docs.append(entry.relative)
        if _TEST_FILE.search(local):
            tests.append(entry.relative)
        kind = _kind(suffix)
        if kind:
            data[kind][0] += 1
            data[kind][1] += size
            data_dirs[entry.relative.rsplit("/", 1)[0] if "/" in entry.relative else "."] += 1
        language = LANGUAGES.get(suffix)
        if not language or size > _MAX_TEXT_BYTES:
            continue
        text = read_text(entry.path)
        if text is None:
            continue
        if suffix == ".ipynb":
            text = notebook_view(text) or text
        languages[language][0] += 1
        languages[language][1] += text.count("\n") + (0 if text.endswith("\n") or not text else 1)
        if suffix in (".py", ".ipynb"):
            python_files.append(entry.relative)
            source = python_source(entry.relative, read_text(entry.path) or "") if suffix == ".ipynb" else text
            if suffix == ".py" and _MAIN.search(source):
                entry_points.append(entry.relative)
            tree = parse(source)
            if tree is not None:
                for item in imports_of(tree)[0]:
                    top = top_level(item)
                    category = category_of(top)
                    if category:
                        libraries[category].add(top)
                    if top and top not in STDLIB:
                        third_party[top] += 1

    out = [f"{context.target_root.name}{'/' + base.rstrip('/') if base else ''}: "
           f"{files} files, {human_size(total_size)}"
           + ("; git repo" if (context.target_root / ".git").exists() else "")]
    if languages:
        ranked = sorted(languages.items(), key=lambda item: -item[1][1])
        out.append("languages: " + ", ".join(f"{lang} {n}f/{lines}L" for lang, (n, lines) in ranked))
    if top_dirs:
        out.append("layout: " + ", ".join(f"{d} {n}" for d, n in top_dirs.most_common(12)))
    points = entry_points[:8]
    pyproject = context.target_root / "pyproject.toml"
    if pyproject.is_file():
        scripts = re.findall(r"^\s*([\w.-]+)\s*=\s*\"([\w.]+:[\w.]+)\"", pyproject.read_text(encoding="utf-8",
                             errors="replace"), re.MULTILINE)
        points += [f"{k} = {v}" for k, v in scripts[:6]]
    package_json = context.target_root / "package.json"
    if package_json.is_file():
        try:
            points += [f"npm {k}" for k in json.loads(package_json.read_text(encoding="utf-8")).get("scripts", {})][:6]
        except ValueError:
            pass
    if points:
        out.append("entry points: " + ", ".join(points) + (" …" if len(entry_points) > 8 else ""))
    if tests:
        out.append(f"tests ({len(tests)}): " + ", ".join(tests[:6]) + (" …" if len(tests) > 6 else ""))
    if data:
        out.append("data: " + ", ".join(f"{kind} {n} ({human_size(b)})" for kind, (n, b) in
                                        sorted(data.items(), key=lambda item: -item[1][1]))
                   + " in " + ", ".join(d + "/" for d, _ in data_dirs.most_common(4)))
    if libraries:
        out.append("stack: " + "; ".join(f"{cat}: {', '.join(sorted(tops))}" for cat, tops in
                                         sorted(libraries.items())))
    index = ModuleIndex(python_files)
    other = [top for top, _ in third_party.most_common()
             if not category_of(top) and not index.resolve("", Import(0, top, (), 0))]
    if other:
        out.append("other imports: " + ", ".join(other[:15]) + (" …" if len(other) > 15 else ""))
    manifests, _ = declared(context.target_root)
    if manifests:
        out.append("declared deps: " + "; ".join(f"{src} {len(names)}" for src, names in manifests.items()))
    if configs:
        out.append("config: " + ", ".join(configs[:12]))
    if docs:
        out.append("docs: " + ", ".join(docs[:6]))
    if sizes:
        out.append("largest: " + ", ".join(f"{rel} {human_size(size)}" for size, rel in
                                            sorted(sizes, reverse=True)[:5]))
    if not include_ignored:
        path_filter = PathFilter(context.target_root)
        ignored = []
        for child in sorted(start.iterdir()):
            if context.is_excluded(child):
                continue
            relative = context.target_relative(child)
            if path_filter.ignored(relative, child.is_dir()):
                ignored.append(relative + ("/" if child.is_dir() else ""))
        if ignored:
            out.append("not counted (ignored): " + ", ".join(ignored[:12]) + "; all=true includes")
    return {"ok": True, "text": "\n".join(out), "files": files}


if __name__ == "__main__":
    raise SystemExit(run_tool(run))
