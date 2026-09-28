"""Shared filesystem mechanics for target-scoped tools: walking, ignore rules, globs, text.

Tools run as isolated child processes; this module is imported by them, never by the
control plane, so it must stay dependency-free and side-effect free.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from .tool_runtime import MechanicalContext

# Directories that are almost never what an agent wants to see. `all: true` disables this.
DEFAULT_IGNORED_DIRS = frozenset(
    {
        ".git", ".hg", ".svn", "node_modules", "__pycache__", ".venv", "venv",
        ".mypy_cache", ".pytest_cache", ".ruff_cache", ".tox", ".idea", ".vs",
        ".next", ".cache", ".gradle",
    }
)
BINARY_SNIFF_BYTES = 8192


# --------------------------------------------------------------------------- globs


def _expand_braces(pattern: str) -> list[str]:
    match = re.search(r"\{([^{}]*)\}", pattern)
    if not match:
        return [pattern]
    head, tail = pattern[: match.start()], pattern[match.end() :]
    expanded: list[str] = []
    for option in match.group(1).split(","):
        expanded.extend(_expand_braces(head + option + tail))
    return expanded


def _glob_to_regex(pattern: str) -> str:
    out: list[str] = []
    i = 0
    while i < len(pattern):
        char = pattern[i]
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif char == "*":
            out.append("[^/]*")
            i += 1
        elif char == "?":
            out.append("[^/]")
            i += 1
        elif char == "[":
            end = pattern.find("]", i + 1)
            if end == -1:
                out.append(re.escape(char))
                i += 1
            else:
                body = pattern[i + 1 : end]
                if body.startswith("!"):
                    body = "^" + body[1:]
                out.append("[" + body.replace("\\", "\\\\") + "]")
                i = end + 1
        else:
            out.append(re.escape(char))
            i += 1
    return "".join(out)


class Glob:
    """Path glob: `*.py`, `**/*.md`, `src/**/test_*.py`, `*.{ts,tsx}`.

    A pattern without `/` matches the basename at any depth; with `/` it matches the
    path relative to the search root.
    """

    def __init__(self, pattern: str) -> None:
        self.pattern = pattern
        self._rules: list[tuple[bool, re.Pattern]] = []
        for part in _expand_braces(pattern.strip()):
            part = part.lstrip("/")
            if part.startswith("./"):
                part = part[2:]
            self._rules.append(("/" in part, re.compile(_glob_to_regex(part) + r"\Z")))

    def matches(self, relative: str) -> bool:
        name = relative.rsplit("/", 1)[-1]
        return any(rule.match(relative if anchored else name) for anchored, rule in self._rules)


# --------------------------------------------------------------------------- ignores


@dataclass(frozen=True)
class _IgnoreRule:
    base: str  # target-relative directory holding the .gitignore ("" for root)
    regex: re.Pattern
    negate: bool
    dir_only: bool


def _parse_gitignore(path: Path, base: str) -> list[_IgnoreRule]:
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    rules: list[_IgnoreRule] = []
    for raw in lines:
        line = raw.rstrip()
        if not line or line.startswith("#"):
            continue
        negate = line.startswith("!")
        if negate:
            line = line[1:]
        line = line.replace("\\#", "#").replace("\\!", "!")
        dir_only = line.endswith("/")
        line = line.rstrip("/")
        if not line:
            continue
        anchored = "/" in line
        line = line.lstrip("/")
        body = _glob_to_regex(line)
        regex = re.compile((body if anchored else "(?:.*/)?" + body) + r"\Z")
        rules.append(_IgnoreRule(base, regex, negate, dir_only))
    return rules


class IgnoreRules:
    """A practical subset of .gitignore semantics, applied per directory as it is walked."""

    def __init__(self, enabled: bool, defaults: bool = True) -> None:
        self.enabled = enabled
        self.defaults = defaults  # also ignore DEFAULT_IGNORED_DIRS
        self._rules: list[_IgnoreRule] = []

    def load_directory(self, directory: Path, relative: str) -> None:
        if self.enabled:
            candidate = directory / ".gitignore"
            if candidate.is_file():
                self._rules.extend(_parse_gitignore(candidate, relative))

    def ignored(self, relative: str, is_dir: bool) -> bool:
        if not self.enabled:
            return False
        if self.defaults and is_dir and relative.rsplit("/", 1)[-1] in DEFAULT_IGNORED_DIRS:
            return True
        verdict = False
        for rule in self._rules:
            if rule.dir_only and not is_dir:
                continue
            if rule.base:
                prefix = rule.base + "/"
                if not relative.startswith(prefix):
                    continue
                local = relative[len(prefix) :]
            else:
                local = relative
            if rule.regex.match(local):
                verdict = not rule.negate
        return verdict


def parse_rule_file(path: Path) -> IgnoreRules:
    """Rules from one gitignore-syntax file, anchored at the target root, no defaults."""
    rules = IgnoreRules(True, defaults=False)
    rules._rules.extend(_parse_gitignore(path, ""))
    return rules


# --------------------------------------------------------------------------- walking


@dataclass(frozen=True)
class Entry:
    path: Path
    relative: str  # target-relative, posix
    is_dir: bool
    is_symlink: bool
    depth: int  # 1 = direct child of the walk start


def scope(context: MechanicalContext, raw: object) -> Path:
    """The control plane has already contained declared path arguments; default is root."""
    if raw is None:
        return context.target_root
    return Path(str(raw))


def _ancestor_rules(context: MechanicalContext, start: Path, rules: IgnoreRules) -> None:
    # Load .gitignore files from the target root down to the start directory, so a
    # scoped walk honours the same rules as a whole-target walk.
    current = context.target_root
    rules.load_directory(current, "")
    if start == current:
        return
    for part in start.relative_to(context.target_root).parts:
        current = current / part
        rules.load_directory(current, context.target_relative(current))


class PathFilter:
    """Decide ignore status for many arbitrary target-relative paths.

    Each directory's .gitignore is parsed at most once, ancestors before descendants, so
    a path is judged exactly as a whole-target walk would judge it.
    """

    def __init__(self, target_root: Path) -> None:
        self.target_root = target_root
        self._rules = IgnoreRules(True)
        self._loaded: set[str] = set()
        self._verdicts: dict[str, bool] = {}

    def _load(self, directory: str) -> None:
        if directory not in self._loaded:
            self._loaded.add(directory)
            self._rules.load_directory(self.target_root / directory if directory else self.target_root, directory)

    def ignored(self, relative: str, is_dir: bool) -> bool:
        parts = relative.split("/")
        self._load("")
        for index in range(1, len(parts) + 1):
            prefix = "/".join(parts[:index])
            last = index == len(parts)
            key = f"{prefix}{'/' if (is_dir or not last) else ''}"
            if key not in self._verdicts:
                self._verdicts[key] = self._rules.ignored(prefix, is_dir or not last)
            if self._verdicts[key]:
                return True
            if not last:
                self._load(prefix)
        return False


def walk(
    context: MechanicalContext,
    start: Path,
    *,
    include_ignored: bool = False,
    max_depth: int | None = None,
) -> Iterator[Entry]:
    """Depth-first, sorted, deterministic walk. Never follows symlinked directories."""
    rules = IgnoreRules(not include_ignored)
    _ancestor_rules(context, start, rules)

    def visit(directory: Path, depth: int) -> Iterator[Entry]:
        try:
            with os.scandir(directory) as iterator:
                children = sorted(iterator, key=lambda item: item.name)
        except OSError:
            return
        for child in children:
            path = Path(child.path)
            if context.is_excluded(path):
                continue
            try:
                is_symlink = child.is_symlink()
                is_dir = child.is_dir(follow_symlinks=False)
            except OSError:
                continue
            relative = context.target_relative(path)
            if rules.ignored(relative, is_dir):
                continue
            yield Entry(path, relative, is_dir, is_symlink, depth)
            if is_dir and not is_symlink and (max_depth is None or depth < max_depth):
                rules.load_directory(path, relative)
                yield from visit(path, depth + 1)

    if start.is_dir():
        yield from visit(start, 1)
    elif start.exists():
        yield Entry(start, context.target_relative(start), False, start.is_symlink(), 0)


# --------------------------------------------------------------------------- text


def read_text(path: Path) -> str | None:
    """Decode a file as UTF-8 text with newlines normalised to \\n, or None if binary."""
    raw = path.read_bytes()
    if b"\x00" in raw[:BINARY_SNIFF_BYTES]:
        return None
    if raw.startswith(b"\xef\xbb\xbf"):
        raw = raw[3:]
    content = raw.decode("utf-8", errors="replace")
    return content.replace("\r\n", "\n") if "\r" in content else content


def human_size(size: int) -> str:
    value = float(size)
    for unit in ("B", "K", "M", "G"):
        if value < 1024 or unit == "G":
            return f"{int(value)}{unit}" if unit == "B" else f"{value:.1f}{unit}"
        value /= 1024
    return f"{size}B"
