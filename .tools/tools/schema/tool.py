from __future__ import annotations

import csv
import json
import re
import sqlite3
import xml.etree.ElementTree as ET
from pathlib import Path

from core.fswalk import human_size, scope, walk
from core.symbols import clip, suffix_of
from core.tool_runtime import MechanicalContext, run_tool

SQLITE = {".sqlite", ".sqlite3", ".db", ".duckdb"}
TABLES = {".csv", ".tsv"}
JSONS = {".json", ".jsonl", ".ndjson", ".geojson", ".jsonld"}
GRAPHS = {".graphml", ".gexf"}
ARROW = {".parquet", ".feather", ".arrow"}
_FULL_COUNT_BYTES = 50_000_000
_MAX_KEYS = 40
_INT = re.compile(r"^[+-]?\d+$")
_FLOAT = re.compile(r"^[+-]?(\d+\.\d*|\.\d+|\d+)([eE][+-]?\d+)?$|^[+-]?(nan|inf)$", re.IGNORECASE)
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}([ T]\d{2}:\d{2}(:\d{2})?)?")
_BOOL = {"true", "false", "yes", "no", "t", "f"}


# --------------------------------------------------------------------------- sqlite


def _sqlite(path: Path) -> list[str]:
    connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro&immutable=1", uri=True)
    try:
        objects = connection.execute(
            "SELECT type, name FROM sqlite_master WHERE type IN ('table','view') "
            "AND name NOT LIKE 'sqlite_%' ORDER BY type, name").fetchall()
        tables = sum(1 for kind, _ in objects if kind == "table")
        views = len(objects) - tables
        lines = [f"sqlite, {tables} table{'' if tables == 1 else 's'}, {views} view{'' if views == 1 else 's'}"]
        for kind, name in objects:
            quoted = '"' + name.replace('"', '""') + '"'
            columns = connection.execute(f"PRAGMA table_info({quoted})").fetchall()
            foreign = {row[3]: f"{row[2]}.{row[4]}" for row in connection.execute(f"PRAGMA foreign_key_list({quoted})")}
            rendered = []
            for _, column, ctype, notnull, _, pk in columns:
                text = f"{column} {ctype or 'ANY'}"
                if pk:
                    text += " PK"
                if notnull and not pk:
                    text += " NOT NULL"
                if column in foreign:
                    text += f" -> {foreign[column]}"
                rendered.append(text)
            count = connection.execute(f"SELECT COUNT(*) FROM {quoted}").fetchone()[0]
            lines.append(f"  {name}{' (view)' if kind == 'view' else ''} [{count} rows]: " + ", ".join(rendered))
            for index in connection.execute(f"PRAGMA index_list({quoted})").fetchall():
                if index[1].startswith("sqlite_autoindex"):
                    continue
                cols = [row[2] for row in connection.execute(f"PRAGMA index_info(\"{index[1]}\")")]
                lines.append(f"    index {index[1]}({', '.join(cols)}){' unique' if index[2] else ''}")
        return lines
    finally:
        connection.close()


# --------------------------------------------------------------------------- tables


def _value_type(value: str) -> str | None:
    value = value.strip()
    if not value or value.lower() in {"na", "nan", "null", "none", "n/a"}:
        return None
    if value.lower() in _BOOL:
        return "bool"
    if _INT.match(value):
        return "int"
    if _FLOAT.match(value):
        return "float"
    if _DATE.match(value):
        return "date"
    return "str"


def _merge_type(current: str | None, new: str) -> str:
    if current is None or current == new:
        return new
    if {current, new} <= {"int", "float"}:
        return "float"
    return "str"


def _table(path: Path, sample: int) -> list[str]:
    size = path.stat().st_size
    with path.open("r", encoding="utf-8", errors="replace", newline="") as stream:
        head = stream.read(65536)
        stream.seek(0)
        if path.suffix.lower() == ".tsv":
            dialect: type[csv.Dialect] | csv.Dialect = csv.excel_tab
        else:
            try:
                dialect = csv.Sniffer().sniff(head, delimiters=",;\t|")
            except csv.Error:
                dialect = csv.excel
        reader = csv.reader(stream, dialect)
        header = next(reader, [])
        types: list[str | None] = [None] * len(header)
        nulls = [0] * len(header)
        examples: list[str] = [""] * len(header)
        distinct: list[set] = [set() for _ in header]
        rows = 0
        for row in reader:
            rows += 1
            if rows <= sample:
                for index in range(len(header)):
                    value = row[index] if index < len(row) else ""
                    kind = _value_type(value)
                    if kind is None:
                        nulls[index] += 1
                        continue
                    types[index] = _merge_type(types[index], kind)
                    if not examples[index]:
                        examples[index] = value.strip()
                    if len(distinct[index]) <= 20:
                        distinct[index].add(value.strip())
            elif size > _FULL_COUNT_BYTES:
                break
    estimated = size > _FULL_COUNT_BYTES and rows > sample
    if estimated:  # newline count; quoted multi-line fields make it approximate
        with path.open("rb") as raw:
            rows = sum(chunk.count(b"\n") for chunk in iter(lambda: raw.read(1 << 20), b"")) - 1
    sampled = min(rows, sample)
    lines = [f"{'tsv' if dialect is csv.excel_tab else 'csv'}, {'~' if estimated else ''}{rows} rows x {len(header)} cols"
             + (f" (types from first {sampled})" if rows > sampled else "")]
    for index, name in enumerate(header):
        kind = types[index] or "empty"
        text = f"  {name or '(blank)'} {kind}"
        if nulls[index]:
            text += f" nulls {nulls[index]}/{sampled}"
        if sampled >= 20 and 0 < len(distinct[index]) <= 10 and kind in ("str", "int", "bool"):
            text += f" {len(distinct[index])} distinct"
        if examples[index]:
            text += f"  e.g. {clip(examples[index])[:40]}"
        lines.append(text)
    return lines


# --------------------------------------------------------------------------- json


def _shape(value: object) -> dict:
    """A mergeable shape: {"t": type, "k": {key: (shape, seen)}, "n": items, "i": item shape}."""
    if isinstance(value, dict):
        return {"t": "object", "c": 1, "k": {k: _shape(v) for k, v in list(value.items())[:_MAX_KEYS * 4]}}
    if isinstance(value, list):
        item = None
        for element in value[:200]:
            item = _merge(item, _shape(element))
        return {"t": "list", "c": 1, "n": len(value), "i": item}
    kind = {bool: "bool", int: "int", float: "float", str: "str", type(None): "null"}.get(type(value), "?")
    return {"t": kind, "c": 1}


def _merge(a: dict | None, b: dict) -> dict:
    if a is None:
        return b
    if a["t"] != b["t"]:
        kinds = set(a["t"].split("|")) | set(b["t"].split("|"))
        if kinds == {"int", "float"}:
            return {"t": "float", "c": a["c"] + b["c"]}
        return {"t": "|".join(sorted(kinds)), "c": a["c"] + b["c"]}
    merged = {"t": a["t"], "c": a["c"] + b["c"]}
    if a["t"] == "object":
        keys = dict(a["k"])
        for key, shape in b["k"].items():
            keys[key] = _merge(keys.get(key), shape)
        merged["k"] = keys
    if a["t"] == "list":
        merged["n"] = max(a["n"], b["n"])
        merged["i"] = b["i"] if a["i"] is None else (a["i"] if b["i"] is None else _merge(a["i"], b["i"]))
    return merged


def _render_shape(shape: dict | None, indent: int, lines: list[str], depth: int = 0) -> str:
    """Inline label for scalars; objects expand into child lines."""
    if shape is None:
        return "empty"
    if shape["t"] == "list":
        inner = _render_shape(shape["i"], indent, lines, depth)
        return f"list[{shape['n']}] of {inner}" if shape.get("n") is not None else f"list of {inner}"
    if shape["t"] != "object":
        return shape["t"]
    if depth >= 6:
        return f"object({len(shape['k'])} keys)"
    children: list[str] = []
    for index, (key, child) in enumerate(shape["k"].items()):
        if index >= _MAX_KEYS:
            children.append(" " * (indent + 2) + f"… {len(shape['k']) - _MAX_KEYS} more keys")
            break
        optional = "?" if child["c"] < shape["c"] else ""
        nested: list[str] = []
        label = _render_shape(child, indent + 2, nested, depth + 1)
        children.append(" " * (indent + 2) + f"{key}{optional}: {label}")
        children.extend(nested)
    lines.extend(children)
    return "object"


def _json(path: Path, sample: int) -> list[str]:
    text = path.read_text(encoding="utf-8", errors="replace")
    if path.suffix.lower() in (".jsonl", ".ndjson"):
        shape = None
        count = 0
        bad = 0
        for line in text.splitlines():
            if not line.strip():
                continue
            count += 1
            if count <= sample:
                try:
                    shape = _merge(shape, _shape(json.loads(line)))
                except ValueError:
                    bad += 1
        lines: list[str] = []
        label = _render_shape(shape, 0, lines)
        head = f"jsonl, {count} records of {label}" + (f" (shape from first {sample})" if count > sample else "")
        return [head + (f"; {bad} unparseable lines" if bad else "")] + lines
    document = json.loads(text)
    lines = []
    label = _render_shape(_shape(document), 0, lines)
    return [f"json {label}"] + lines


# --------------------------------------------------------------------------- graphs / arrow


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _graph(path: Path) -> list[str]:
    nodes = edges = 0
    directed = "?"
    keys: list[str] = []
    for _, element in ET.iterparse(path, events=("end",)):
        tag = _local(element.tag)
        if tag == "node":
            nodes += 1
        elif tag == "edge":
            edges += 1
        elif tag == "key":  # graphml
            keys.append(f"{element.get('for', 'all')}.{element.get('attr.name') or element.get('id')}"
                        f":{element.get('attr.type', '?')}")
        elif tag == "attribute":  # gexf
            keys.append(f"{element.get('title') or element.get('id')}:{element.get('type', '?')}")
        elif tag == "graph":
            directed = element.get("edgedefault") or element.get("defaultedgetype") or directed
        if tag in ("node", "edge"):
            element.clear()
    lines = [f"{path.suffix.lstrip('.').lower()}, {nodes} nodes, {edges} edges, {directed}"]
    if keys:
        lines.append("  attributes: " + ", ".join(keys[:_MAX_KEYS]))
    return lines


def _arrow(path: Path) -> list[str]:
    try:
        import pyarrow.parquet as pq  # optional
        import pyarrow.feather as feather
    except ImportError:
        return [f"{path.suffix.lstrip('.')}: not inspected (install pyarrow)"]
    if path.suffix.lower() == ".parquet":
        metadata = pq.read_metadata(path)
        schema = pq.read_schema(path)
        head = f"parquet, {metadata.num_rows} rows x {len(schema)} cols"
    else:
        table = feather.read_table(path, memory_map=True)
        schema = table.schema
        head = f"{path.suffix.lstrip('.')}, {table.num_rows} rows x {len(schema)} cols"
    return [head] + [f"  {field.name} {field.type}{'' if field.nullable else ' NOT NULL'}" for field in schema]


# --------------------------------------------------------------------------- run


def _inspect(path: Path, sample: int) -> list[str] | None:
    suffix = suffix_of(path.name)
    if suffix in SQLITE:
        with path.open("rb") as stream:
            if stream.read(16) != b"SQLite format 3\x00":
                return [f"{suffix.lstrip('.')}: not a SQLite database"]
        return _sqlite(path)
    if suffix in TABLES:
        return _table(path, sample)
    if suffix in JSONS:
        return _json(path, sample)
    if suffix in GRAPHS:
        return _graph(path)
    if suffix in ARROW:
        return _arrow(path)
    return None


def run(arguments: dict, context: MechanicalContext) -> dict:
    start = scope(context, arguments.get("path"))
    if not start.exists():
        return {"ok": False, "error": f"path not found: {context.target_relative(start)}"}
    sample = int(arguments.get("sample", 1000))
    if start.is_file():
        candidates = [start]
    else:
        candidates = [entry.path for entry in walk(context, start, include_ignored=bool(arguments.get("all")))
                      if not entry.is_dir and suffix_of(entry.relative) in SQLITE | TABLES | JSONS | GRAPHS | ARROW]
    out: list[str] = []
    inspected = 0
    for path in candidates[:50]:
        relative = context.target_relative(path)
        try:
            lines = _inspect(path, sample)
        except (OSError, ValueError, sqlite3.Error, ET.ParseError, csv.Error) as exc:
            lines = [f"unreadable: {type(exc).__name__}: {clip(str(exc))}"]
        if lines is None:
            if start.is_file():
                return {"ok": False, "error": f"{relative}: unsupported data format"}
            continue
        inspected += 1
        out.append(f"{relative} ({human_size(path.stat().st_size)}) {lines[0]}")
        out.extend(lines[1:])
    if len(candidates) > 50:
        out.append(f"# {len(candidates) - 50} more data files; narrow path")
    if not out:
        out.append("(no data files: sqlite/db, csv/tsv, json/jsonl, graphml/gexf, parquet/feather)")
    return {"ok": True, "text": "\n".join(out), "files": inspected}


if __name__ == "__main__":
    raise SystemExit(run_tool(run))
