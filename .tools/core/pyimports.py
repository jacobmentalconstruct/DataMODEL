"""Python import facts: what each file imports, which imports are the project's own
files, which are stdlib or third-party, and what the project declares it depends on.

Resolution is static and honest about it: dynamic imports (importlib, __import__) are
counted, not followed.
"""
from __future__ import annotations

import ast
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

try:  # Python 3.11+
    import tomllib
except ImportError:  # pragma: no cover - 3.10
    tomllib = None

STDLIB = frozenset(getattr(sys, "stdlib_module_names", ())) | {"__future__"}

# Import name -> distribution name, for the common cases where they differ.
DISTRIBUTIONS = {
    "sklearn": "scikit-learn", "skimage": "scikit-image", "cv2": "opencv-python", "PIL": "pillow",
    "yaml": "pyyaml", "bs4": "beautifulsoup4", "dateutil": "python-dateutil", "dotenv": "python-dotenv",
    "docx": "python-docx", "pptx": "python-pptx", "fitz": "pymupdf", "Crypto": "pycryptodome",
    "attr": "attrs", "jwt": "pyjwt", "magic": "python-magic", "serial": "pyserial", "zmq": "pyzmq",
    "win32api": "pywin32", "win32con": "pywin32", "igraph": "python-igraph", "faiss": "faiss-cpu",
    "llama_index": "llama-index", "sentence_transformers": "sentence-transformers",
    "llama_cpp": "llama-cpp-python", "OpenGL": "pyopengl", "google": "google-api-python-client",
    "wx": "wxpython", "gi": "pygobject", "Levenshtein": "python-levenshtein", "multipart": "python-multipart",
}

CATEGORIES = {
    "ai/llm": {"ollama", "openai", "anthropic", "langchain", "langchain_core", "langchain_community",
               "langgraph", "llama_index", "transformers", "sentence_transformers", "torch", "tensorflow",
               "keras", "jax", "vllm", "llama_cpp", "gpt4all", "huggingface_hub", "tiktoken", "tokenizers",
               "accelerate", "peft", "diffusers", "mlx", "onnxruntime", "instructor", "guidance", "dspy",
               "autogen", "crewai", "smolagents", "mcp", "litellm", "outlines"},
    "vectors": {"chromadb", "faiss", "qdrant_client", "lancedb", "pinecone", "weaviate", "hnswlib",
                "annoy", "pymilvus", "usearch"},
    "data": {"pandas", "numpy", "polars", "pyarrow", "scipy", "sklearn", "statsmodels", "duckdb", "dask",
             "xarray", "sqlalchemy", "sqlite3", "sqlite_utils", "h5py", "openpyxl"},
    "graph": {"networkx", "igraph", "rdflib", "neo4j", "py2neo", "pyvis", "graphviz", "torch_geometric",
              "dgl", "kuzu", "rustworkx", "graph_tool"},
    "text": {"nltk", "spacy", "gensim", "textblob", "regex", "rapidfuzz", "markdown", "mistune", "bs4",
             "lxml", "pypdf", "fitz", "docx", "unstructured", "ftfy", "langdetect"},
    "viz": {"matplotlib", "seaborn", "plotly", "altair", "bokeh", "holoviews"},
    "web": {"flask", "fastapi", "django", "starlette", "uvicorn", "requests", "httpx", "aiohttp", "jinja2",
            "streamlit", "gradio", "websockets", "pydantic", "panel", "dash"},
    "ui": {"tkinter", "PySide6", "PySide2", "PyQt5", "PyQt6", "wx", "kivy", "customtkinter", "textual",
           "rich", "dearpygui", "flet", "nicegui", "pygame"},
}

_MAGIC = re.compile(r"^\s*[%!]")


@dataclass(frozen=True)
class Import:
    line: int
    module: str  # as written ("" for `from . import x`)
    names: tuple[str, ...]  # imported names for `from` imports, else ()
    level: int  # 0 absolute, >0 relative


def python_source(relative: str, text: str) -> str:
    """Python text of a .py file, or the code cells of a notebook (magics blanked)."""
    if not relative.endswith(".ipynb"):
        return text
    try:
        cells = json.loads(text)["cells"]
    except (ValueError, KeyError, TypeError):
        return ""
    code = []
    for cell in cells:
        if cell.get("cell_type") == "code":
            source = cell.get("source", "")
            source = "".join(source) if isinstance(source, list) else str(source)
            code.extend("" if _MAGIC.match(line) else line for line in source.split("\n"))
    return "\n".join(code)


def parse(source: str) -> ast.Module | None:
    try:
        return ast.parse(source)
    except (SyntaxError, ValueError):
        return None


def imports_of(tree: ast.Module) -> tuple[list[Import], int]:
    """All import statements (at any depth), plus a count of dynamic import sites."""
    found: list[Import] = []
    dynamic = 0
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.extend(Import(node.lineno, alias.name, (), 0) for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            found.append(Import(node.lineno, node.module or "", tuple(a.name for a in node.names), node.level))
        elif isinstance(node, ast.Call):
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            if name in {"import_module", "__import__"}:
                dynamic += 1
    return found, dynamic


def module_key(relative: str) -> str:
    """core/cli.py -> core.cli ; pkg/__init__.py -> pkg"""
    stem = relative[: -len(Path(relative).suffix)] if Path(relative).suffix else relative
    parts = stem.split("/")
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


class ModuleIndex:
    """Resolve imports to project files, tolerating src/ layouts and sys.path tricks by
    matching dotted suffixes (`core.cli` finds `.tools/core/cli.py`)."""

    def __init__(self, relatives: list[str]) -> None:
        self.by_key: dict[str, str] = {}
        for relative in relatives:
            if relative.endswith(".py"):
                self.by_key.setdefault(module_key(relative), relative)

    def _lookup(self, dotted: str) -> str | None:
        if not dotted:
            return None
        if dotted in self.by_key:
            return self.by_key[dotted]
        tail = "." + dotted
        candidates = [key for key in self.by_key if key.endswith(tail)]
        return self.by_key[min(candidates, key=len)] if candidates else None

    def resolve(self, importer: str, item: Import) -> list[str]:
        """Project files an import refers to; empty when it is external."""
        if item.level:
            package = module_key(importer).split(".")
            if not importer.endswith("__init__.py"):
                package = package[:-1]
            package = package[: len(package) - (item.level - 1)] if item.level > 1 else package
            base = ".".join(package + ([item.module] if item.module else []))
            candidates = [f"{base}.{name}" for name in item.names] + [base]
            hits = [self.by_key.get(candidate) for candidate in candidates]
        else:
            candidates = [f"{item.module}.{name}" for name in item.names] + [item.module]
            hits = [self._lookup(candidate) for candidate in candidates]
        files = [hit for hit in hits if hit and hit != importer]
        return list(dict.fromkeys(files))


def top_level(item: Import) -> str:
    return item.module.split(".")[0] if not item.level else ""


def distribution(top: str) -> str:
    return normalize(DISTRIBUTIONS.get(top, top))


def normalize(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def category_of(top: str) -> str | None:
    for category, members in CATEGORIES.items():
        if top in members:
            return category
    return None


_REQUIREMENT = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)")


def _requirement_names(lines: list[str]) -> list[str]:
    names = []
    for line in lines:
        line = line.split("#", 1)[0].strip()
        if not line or line.startswith("-"):
            continue
        match = _REQUIREMENT.match(line)
        if match:
            names.append(normalize(match.group(1)))
    return names


def declared(target_root: Path) -> tuple[dict[str, list[str]], list[str]]:
    """Declared dependencies by manifest file (target-relative), plus limitations."""
    found: dict[str, list[str]] = {}
    limitations: list[str] = []
    for path in sorted(target_root.glob("requirements*.txt")) + sorted(target_root.glob("requirements/*.txt")):
        found[path.relative_to(target_root).as_posix()] = _requirement_names(
            path.read_text(encoding="utf-8", errors="replace").splitlines())
    pyproject = target_root / "pyproject.toml"
    if pyproject.is_file():
        if tomllib is None:
            limitations.append("pyproject.toml not parsed (needs Python 3.11+)")
        else:
            try:
                document = tomllib.loads(pyproject.read_text(encoding="utf-8"))
            except (tomllib.TOMLDecodeError, OSError) as exc:
                limitations.append(f"pyproject.toml unreadable: {exc}")
            else:
                project = document.get("project", {})
                names = _requirement_names(list(project.get("dependencies", [])))
                for extra in project.get("optional-dependencies", {}).values():
                    names += _requirement_names(list(extra))
                poetry = document.get("tool", {}).get("poetry", {})
                names += [normalize(n) for n in poetry.get("dependencies", {}) if n.lower() != "python"]
                found["pyproject.toml"] = sorted(set(names))
    package_json = target_root / "package.json"
    if package_json.is_file():
        try:
            document = json.loads(package_json.read_text(encoding="utf-8"))
            found["package.json"] = sorted(
                {*document.get("dependencies", {}), *document.get("devDependencies", {})})
        except (ValueError, OSError) as exc:
            limitations.append(f"package.json unreadable: {exc}")
    return found, limitations
