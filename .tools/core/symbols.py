"""What a "symbol" is, where it starts and ends, and how to find one by name.

Shared by outline (list), read (extract one) and refs (definitions), so every tool agrees.
Python is parsed exactly with `ast`; Markdown sections by heading level; notebooks are
projected to a percent-format text view; HTML and other languages use line patterns,
where a symbol ends just before the next symbol at the same or a shallower level.
"""
from __future__ import annotations

import ast
import json
import re
from dataclasses import dataclass

_MAX_SIGNATURE = 120


@dataclass(frozen=True)
class Symbol:
    line: int  # first line (1-based, decorators included)
    end: int  # last line, inclusive
    level: int  # nesting depth for display
    label: str  # compact signature/heading shown by outline
    name: str  # lookup name: qualified for Python (Class.method), text for headings


_REGEX_LANGUAGES: dict[tuple[str, ...], list[re.Pattern]] = {
    (".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs"): [
        re.compile(r"^\s*(export\s+)?(default\s+)?(async\s+)?function\b"),
        re.compile(r"^\s*(export\s+)?(default\s+)?(abstract\s+)?class\s+\w"),
        re.compile(r"^\s*(export\s+)?(declare\s+)?(interface|type|enum|namespace)\s+\w"),
        re.compile(r"^(export\s+)?(const|let|var)\s+\w+\s*(:[^=]+)?=\s*(async\s*)?(\([^)]*\)|\w+)\s*=>"),
        re.compile(r"^(export\s+)?(const|let|var)\s+\w+\s*=\s*(async\s+)?function\b"),
        re.compile(r"^\s{2,4}(public\s+|private\s+|protected\s+|static\s+|async\s+|get\s+|set\s+)*(?!if\b|for\b|while\b|switch\b|return\b|catch\b)\w+\s*\([^)]*\)\s*(:\s*[^{]+)?\{\s*$"),
    ],
    (".go",): [re.compile(r"^func\s"), re.compile(r"^type\s+\w+")],
    (".rs",): [re.compile(r"^\s*(pub(\([^)]*\))?\s+)?(async\s+)?(unsafe\s+)?(fn|struct|enum|trait|impl|mod|type)\b")],
    (".java", ".cs", ".kt", ".scala", ".swift"): [
        re.compile(r"^\s*((public|private|protected|internal|static|final|abstract|sealed|open|data|partial)\s+)*(class|interface|enum|record|struct|object|fun|func)\s+\w"),
        re.compile(r"^\s+((public|private|protected|internal|static|final|abstract|override|virtual|async)\s+)+[\w<>\[\],\s]+\s+\w+\s*\([^;]*$"),
    ],
    (".c", ".h", ".cc", ".cpp", ".hpp", ".cxx"): [
        re.compile(r"^(struct|class|enum|union|typedef|namespace)\b"),
        re.compile(r"^[A-Za-z_][\w\s\*&:<>,]*\b\w+\s*\([^;]*\)\s*(const\s*)?\{?\s*$"),
    ],
    (".rb",): [re.compile(r"^\s*(def|class|module)\s")],
    (".php",): [re.compile(r"^\s*((public|private|protected|static|abstract|final)\s+)*(function|class|interface|trait)\s")],
    (".sh", ".bash", ".zsh"): [re.compile(r"^\s*(function\s+\w+|\w+\s*\(\)\s*\{)")],
    (".ps1", ".psm1"): [re.compile(r"^\s*(function|filter|class)\s+[\w-]+", re.IGNORECASE)],
    (".sql",): [re.compile(r"^\s*create\s+(or\s+replace\s+)?(table|view|index|function|procedure|trigger)\b", re.IGNORECASE)],
    (".css", ".scss", ".less"): [re.compile(r"^(@media|@keyframes|:root|[.#][\w-]+[^;{]*\{)")],
}
_DECLARED_NAME = re.compile(
    r"\b(?:def|class|function|fn|func|interface|type|enum|struct|trait|impl|mod|module|"
    r"const|let|var|table|view|index|procedure|trigger)\s+([A-Za-z_$][\w$.]*)", re.IGNORECASE)
_CALL_NAME = re.compile(r"([A-Za-z_$][\w$]*)\s*\(")
_HTML_PATTERNS = [
    (re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE), 0, "title"),
    (re.compile(r"<h([1-6])[^>]*>(.*?)</h\1>", re.IGNORECASE), None, "h"),
    (re.compile(r"<(form|section|nav|main|header|footer|article|table|dialog|template)\b([^>]*)>",
                re.IGNORECASE), 1, "block"),
    (re.compile(r"<script\b[^>]*\bsrc=[\"']([^\"']+)[\"']", re.IGNORECASE), 1, "script"),
    (re.compile(r"<link\b[^>]*\bhref=[\"']([^\"']+\.css)[\"']", re.IGNORECASE), 1, "style"),
    (re.compile(r"<[a-z][\w-]*\b[^>]*\bid=[\"']([^\"']+)[\"']", re.IGNORECASE), 2, "id"),
]
_TAGS = re.compile(r"<[^>]+>")


def clip(text: str) -> str:
    text = " ".join(text.strip().rstrip("{").split())
    return text if len(text) <= _MAX_SIGNATURE else text[: _MAX_SIGNATURE - 1] + "…"


def suffix_of(relative: str) -> str:
    name = relative.rsplit("/", 1)[-1]
    return "." + name.rsplit(".", 1)[-1].lower() if "." in name else ""


# --------------------------------------------------------------------------- notebooks


def notebook_view(raw: str, output_lines: int = 5) -> str | None:
    """Project .ipynb JSON to percent-format text: cell sources plus short text outputs."""
    try:
        document = json.loads(raw)
        cells = document["cells"]
    except (ValueError, KeyError, TypeError):
        return None
    out: list[str] = []
    for index, cell in enumerate(cells, start=1):
        kind = cell.get("cell_type", "code")
        source = cell.get("source", "")
        source = "".join(source) if isinstance(source, list) else str(source)
        out.append(f"# %% [{index}] {kind}")
        out.extend(source.rstrip("\n").split("\n") if source.strip() else [])
        shown: list[str] = []
        kinds: list[str] = []
        for output in cell.get("outputs", []) or []:
            data = output.get("data") or {}
            text = output.get("text") or data.get("text/plain")
            kinds.extend(key for key in data if key != "text/plain")
            if output.get("output_type") == "error":
                shown.append(f"{output.get('ename')}: {output.get('evalue')}")
            elif text:
                shown.extend(("".join(text) if isinstance(text, list) else str(text)).rstrip("\n").split("\n"))
        if shown or kinds:
            extra = f" (+{', '.join(sorted(set(kinds)))})" if kinds else ""
            out.append(f"# -> output{extra}")
            out.extend("#    " + line for line in shown[:output_lines])
            if len(shown) > output_lines:
                out.append(f"#    … {len(shown) - output_lines} more lines")
    return "\n".join(out) + "\n"


# --------------------------------------------------------------------------- extraction


def _python(content: str) -> list[Symbol] | None:
    try:
        tree = ast.parse(content)
    except (SyntaxError, ValueError):
        return None
    symbols: list[Symbol] = []

    def signature(node: ast.AST) -> str:
        if isinstance(node, ast.ClassDef):
            bases = ", ".join(ast.unparse(base) for base in node.bases)
            return f"class {node.name}" + (f"({bases})" if bases else "")
        prefix = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
        returns = f" -> {ast.unparse(node.returns)}" if node.returns else ""
        return f"{prefix} {node.name}({ast.unparse(node.args)}){returns}"

    def visit(body: list[ast.stmt], level: int, prefix: str) -> None:
        for node in body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                start = min([node.lineno] + [d.lineno for d in node.decorator_list])
                name = prefix + node.name
                symbols.append(Symbol(start, node.end_lineno or start, level, clip(signature(node)), name))
                if isinstance(node, ast.ClassDef) and level < 2:
                    visit(node.body, level + 1, name + ".")
            elif level == 0 and isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                names = [t.id for t in targets if isinstance(t, ast.Name) and t.id.isupper()]
                if names:
                    symbols.append(Symbol(node.lineno, node.end_lineno or node.lineno, 0,
                                          ", ".join(names) + " =", names[0]))
            elif level == 0 and isinstance(node, ast.If) and "__main__" in ast.unparse(node.test):
                symbols.append(Symbol(node.lineno, node.end_lineno or node.lineno, 0,
                                      "if __name__ == '__main__'", "__main__"))

    visit(tree.body, 0, "")
    return symbols


def _close(found: list[tuple[int, int, str, str]], total: int) -> list[Symbol]:
    """Give line-pattern symbols an end: just before the next symbol at <= level."""
    symbols: list[Symbol] = []
    for index, (line, level, label, name) in enumerate(found):
        end = total
        for later_line, later_level, _, _ in found[index + 1:]:
            if later_level <= level:
                end = later_line - 1
                break
        symbols.append(Symbol(line, max(line, end), level, label, name))
    return symbols


def _markdown(lines: list[str]) -> list[tuple[int, int, str, str]]:
    found = []
    fenced = False
    for number, line in enumerate(lines, start=1):
        if line.lstrip().startswith(("```", "~~~")):
            fenced = not fenced
            continue
        match = None if fenced else re.match(r"^(#{1,6})\s+(.+)", line)
        if match:
            found.append((number, len(match.group(1)) - 1, clip(match.group(0)), match.group(2).strip()))
    return found


def _notebook(lines: list[str]) -> list[tuple[int, int, str, str]]:
    found = []
    kind = ""
    for number, line in enumerate(lines, start=1):
        cell = re.match(r"^# %% \[(\d+)\] (\w+)", line)
        if cell:
            kind = cell.group(2)
            found.append((number, 0, f"[{cell.group(1)}] {kind}", f"cell{cell.group(1)}"))
        elif kind == "markdown":
            heading = re.match(r"^(#{1,6})\s+(.+)", line)
            if heading:
                found.append((number, 1, clip(heading.group(0)), heading.group(2).strip()))
        elif kind == "code" and re.match(r"^(async\s+def|def|class)\s", line):
            found.append((number, 1, clip(line), _DECLARED_NAME.search(line).group(1)))
    return found


def _html(lines: list[str]) -> list[tuple[int, int, str, str]]:
    found = []
    for number, line in enumerate(lines, start=1):
        for pattern, level, kind in _HTML_PATTERNS:
            for match in pattern.finditer(line):
                if kind == "title":
                    text = _TAGS.sub("", match.group(1)).strip()
                    found.append((number, 0, f"<title> {clip(text)}", text))
                elif kind == "h":
                    text = _TAGS.sub("", match.group(2)).strip()
                    found.append((number, int(match.group(1)), f"<h{match.group(1)}> {clip(text)}", text))
                elif kind == "block":
                    found_id = re.search(r"\bid=[\"']([^\"']+)[\"']", match.group(2))
                    ident = found_id.group(1) if found_id else None
                    found.append((number, 1, f"<{match.group(1).lower()}" + (f"#{ident}>" if ident else ">"),
                                   ident or match.group(1).lower()))
                elif kind == "id":
                    if not any(entry[0] == number and match.group(1) in entry[2] for entry in found):
                        found.append((number, 2, f"#{match.group(1)}", match.group(1)))
                else:
                    found.append((number, 1, f"{kind} {match.group(1)}", match.group(1)))
    return found


def _keys(suffix: str, content: str, lines: list[str]) -> list[tuple[int, int, str, str]]:
    if suffix == ".json":
        try:
            document = json.loads(content)
        except ValueError:
            return []
        if not isinstance(document, dict):
            return [(1, 0, f"{type(document).__name__} of {len(document)}", "")]
        found = []
        for key in document:
            pattern = re.compile(r'^\s{0,4}"' + re.escape(str(key)) + r'"\s*:')
            line = next((n for n, text in enumerate(lines, 1) if pattern.match(text)), 1)
            found.append((line, 0, json.dumps(key), str(key)))
        return found
    rule = re.compile(r"^(\[\[?[^\]]+\]\]?|[A-Za-z0-9_.\-\"]+\s*=)") if suffix == ".toml" \
        else re.compile(r"^[A-Za-z0-9_.\-\"']+\s*:")
    found = []
    for number, line in enumerate(lines, 1):
        if rule.match(line):
            label = clip(line.split("=")[0] if suffix == ".toml" and "=" in line else line)
            found.append((number, 0, label, label.strip("[]\"' :=")))
    return found


def extract(relative: str, content: str) -> list[Symbol]:
    """Symbols of one file. For notebooks, `content` must already be the notebook view."""
    lines = content.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    total = len(lines)
    suffix = suffix_of(relative)
    if suffix in (".py", ".pyw", ".pyi"):
        parsed = _python(content)
        if parsed is not None:
            return parsed
        found = [(n, 0, clip(line), _DECLARED_NAME.search(line).group(1))
                 for n, line in enumerate(lines, 1) if re.match(r"^\s*(async\s+def|def|class)\s", line)]
        return _close(found, total)
    if suffix in (".md", ".markdown", ".mdx", ".rst"):
        return _close(_markdown(lines), total)
    if suffix == ".ipynb":
        return _close(_notebook(lines), total)
    if suffix in (".html", ".htm", ".jinja", ".j2", ".vue", ".svelte"):
        return _close(_html(lines), total)
    if suffix in (".json", ".toml", ".yaml", ".yml"):
        return _close(_keys(suffix, content, lines), total)
    for suffixes, patterns in _REGEX_LANGUAGES.items():
        if suffix in suffixes:
            found = []
            for number, line in enumerate(lines, 1):
                if any(pattern.match(line) for pattern in patterns):
                    indent = len(line) - len(line.lstrip())
                    named = _DECLARED_NAME.search(line) or _CALL_NAME.search(line)
                    found.append((number, 1 if indent else 0, clip(line), named.group(1) if named else ""))
            return _close(found, total)
    return []


def find(symbols: list[Symbol], query: str) -> list[Symbol]:
    """Exact qualified name, else unique trailing name (`method` finds `Class.method`),
    else case-insensitive heading text."""
    exact = [s for s in symbols if s.name == query]
    if exact:
        return exact
    tail = [s for s in symbols if s.name.endswith("." + query)]
    if tail:
        return tail
    folded = query.casefold().lstrip("# ")
    return [s for s in symbols if s.name.casefold() == folded]
