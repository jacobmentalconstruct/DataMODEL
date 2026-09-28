"""End-to-end tests: each test runs against a throwaway target with a fresh instance copy.

Run from anywhere:  python .tools/tests/test_helpers.py
"""
from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[1]
_SKIP = shutil.ignore_patterns("state", "logs", "instance.json", "__pycache__", "tests")


class Target(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name) / "project"
        self.instance = self.root / ".tools"
        shutil.copytree(SOURCE, self.instance, ignore=_SKIP)
        self.write("src/pkg/models.py", (
            '"""Models."""\nLIMIT = 3\n\nclass Store:\n    def add(self, item):\n'
            "        return item  # TODO\n\ndef helper(a, b=2):\n    return a + b\n"
        ))
        self.write("src/app.ts", "export class App {\n  start(port: number): void {\n  }\n}\n")
        self.write("docs/guide.md", "# Guide\n\n## Setup\n\n```\n# not a heading\n```\n## Use\n")
        (self.root / "docs/win.txt").write_bytes(b"one\r\nTODO two\r\nthree\r\n")
        self.write(".gitignore", "build/\n*.log\n")
        self.write("build/out.txt", "TODO ignored\n")
        self.write("node_modules/x/i.js", "TODO vendored\n")
        (self.root / "blob.bin").write_bytes(b"\x00\x01TODO")
        self.assertEqual(self.cli("init").returncode, 0)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def write(self, relative: str, text: str) -> None:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="")

    def cli(self, *argv: str, stdin: str | None = None) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(self.instance / "bin" / "helpers.py"), *argv],
            input=stdin, capture_output=True, text=True, encoding="utf-8",
        )

    def out(self, *argv: str, stdin: str | None = None) -> str:
        result = self.cli(*argv, stdin=stdin)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result.stdout.strip()

    def fails(self, *argv: str) -> str:
        result = self.cli(*argv)
        self.assertNotEqual(result.returncode, 0, result.stdout)
        return result.stdout.strip()

    @contextmanager
    def core(self):
        """Import this copy's `core` package in-process (for fault injection); yields
        (core package, loaded instance context)."""
        sys.path.insert(0, str(self.instance))
        try:
            import core
            from core.instance import load

            yield core, load(self.instance)
        finally:
            sys.path.remove(str(self.instance))
            for name in [n for n in sys.modules if n == "core" or n.startswith("core.")]:
                del sys.modules[name]


class ReadTests(Target):
    def test_range_numbers_and_continuation(self) -> None:
        text = self.out("read", "path=src/pkg/models.py", "offset=4", "limit=2")
        self.assertEqual(text.splitlines()[0], "# src/pkg/models.py (lines 4-5 of 9)")
        self.assertIn("4\tclass Store:", text)
        self.assertIn("continue with offset=6", text)

    def test_many_files_crlf_binary_directory(self) -> None:
        text = self.out("read", "--args", json.dumps({"paths": ["docs/win.txt", "blob.bin", "src"]}))
        self.assertIn("2\tTODO two", text)
        self.assertNotIn("\r", text)
        self.assertIn("blob.bin: binary file", text)
        self.assertIn("src/: is a directory", text)

    def test_containment(self) -> None:
        self.assertIn("containment_refusal", self.fails("read", "path=../outside.txt"))
        self.assertIn("private subtree", self.fails("read", "path=.tools/instance.json"))
        self.assertIn("containment_refusal", self.fails("read", "--args", '{"paths":["ok.md","../x"]}'))


class SearchTests(Target):
    def test_respects_gitignore_and_vendor_dirs(self) -> None:
        files = self.out("grep", "pattern=TODO", "mode=files").splitlines()
        self.assertEqual(files, ["docs/win.txt", "src/pkg/models.py"])
        everything = self.out("grep", "pattern=TODO", "mode=files", "all=true").splitlines()
        self.assertIn("build/out.txt", everything)
        self.assertIn("node_modules/x/i.js", everything)

    def test_regex_glob_context(self) -> None:
        text = self.out("grep", r"pattern=def \w+", "glob=*.py", "context=1")
        self.assertIn("src/pkg/models.py:5:    def add(self, item):", text)
        self.assertIn("src/pkg/models.py-6-", text)
        self.assertNotIn("--", text)  # touching context windows merge
        split = self.out("grep", "pattern=class|return a", "glob=*.py", "context=0")
        self.assertNotIn("--", split)  # no separators without context

    def test_literal_count_and_bad_regex(self) -> None:
        self.assertIn("invalid regex", self.fails("grep", "pattern=("))
        self.assertEqual(self.out("grep", "pattern=a + b", "literal=true", "mode=count"),
                         "src/pkg/models.py:1")

    def test_numeric_looking_pattern_stays_a_string(self) -> None:
        self.assertIn("no matches", self.out("grep", "pattern=12345"))


class ListAndOutlineTests(Target):
    def test_ls_depth_counts_hidden(self) -> None:
        text = self.out("ls", "depth=1")
        self.assertIn("src/ (+2 files)", text)
        self.assertNotIn("build", text)
        self.assertNotIn(".tools", text)

    def test_ls_glob(self) -> None:
        self.assertEqual(self.out("ls", "glob=**/*.{py,ts}").splitlines(),
                         ["src/app.ts", "src/pkg/models.py"])

    def test_outline(self) -> None:
        text = self.out("outline", "path=src")
        self.assertIn("src/pkg/models.py (9 lines)", text)
        self.assertIn("    5 def add(self, item)", text)
        self.assertIn("  8 def helper(a, b=2)", text)
        self.assertIn("LIMIT =", text)
        self.assertIn("export class App", text)
        guide = self.out("outline", "path=docs/guide.md")
        self.assertIn("3 ## Setup", guide)
        self.assertNotIn("not a heading", guide)


class EditTests(Target):
    def test_unique_ambiguous_all(self) -> None:
        self.assertIn("changed lines 9", self.out("edit", "path=src/pkg/models.py",
                                                  "old=return a + b", "new=return a * b"))
        self.write("dup.txt", "x\nx\n")
        self.assertIn("matches 2 times", self.fails("edit", "path=dup.txt", "old=x", "new=y"))
        self.assertIn("2 replacements", self.out("edit", "path=dup.txt", "old=x", "new=y", "all=true"))

    def test_atomic_multi_edit(self) -> None:
        before = (self.root / "src/app.ts").read_bytes()
        edits = {"path": "src/app.ts", "edits": [{"old": "App", "new": "Main"}, {"old": "nope", "new": "x"}]}
        self.assertIn("edit 2: text not found", self.fails("edit", "--args", json.dumps(edits)))
        self.assertEqual((self.root / "src/app.ts").read_bytes(), before)

    def test_crlf_preserved(self) -> None:
        self.out("edit", "path=docs/win.txt", "old=TODO two", "new=done\nadded")
        self.assertEqual((self.root / "docs/win.txt").read_bytes(), b"one\r\ndone\r\nadded\r\nthree\r\n")

    def test_authority_gate(self) -> None:
        self.assertIn("authority_denied",
                      self.fails("edit", "path=src/app.ts", "old=App", "new=X", "--authority", "observe"))

    def test_write_from_stdin(self) -> None:
        self.assertIn("created", self.out("write", "path=n/a.md", "content=@-", stdin="hi\n"))
        self.assertIn("overwrite is false", self.fails("write", "path=n/a.md", "content=x"))
        self.assertIn("overwritten", self.out("write", "path=n/a.md", "content=x", "overwrite=true"))


class GovernanceTests(Target):
    def test_receipts_recorded_and_envelope_available(self) -> None:
        self.out("read", "path=docs/guide.md")
        receipts = json.loads(self.out("run", "receipts.list", "limit=1"))
        self.assertEqual(receipts[0]["tool_id"], "read")
        envelope = json.loads(self.out("read", "path=docs/guide.md", "--json"))
        self.assertTrue(envelope["durably_governed"])
        artifact = json.loads(self.out("run", "artifacts.read", "artifact_id=" + envelope["artifact_id"]))
        self.assertNotIn("stdout", artifact["body"]["process"])  # not stored twice
        self.assertIn("result", artifact["body"]["envelope"])

    def test_governed_mutation_flow(self) -> None:
        self.out("run", "substrate.refresh")
        self.out("run", "awareness.refresh")
        preview = json.loads(self.out("run", "mutation.preview_write", "path=g.md", "content=gov", "--json"))
        preview_id = preview["preview"]["preview_id"]
        self.assertIn("authority_denied", self.fails(
            "run", "mutation.approve", "preview_id=" + preview_id, "--authority", "observe"))
        approval = json.loads(self.out("run", "mutation.approve", "preview_id=" + preview_id, "--json"))
        self.out("run", "mutation.apply", "approval_id=" + approval["approval"]["approval_id"])
        self.assertEqual((self.root / "g.md").read_text(), "gov")


class ChangesTests(Target):
    def test_baseline_diff_and_acknowledge(self) -> None:
        self.assertIn("no snapshot yet", self.out("changes"))
        self.out("changes", "mark=true")
        self.assertTrue(self.out("changes").splitlines()[0].endswith(": 0"))
        self.write("src/pkg/models.py", "changed\n")
        self.write("src/new.py", "x = 1\n")
        (self.root / "docs/guide.md").unlink()
        os.utime(self.root / "src/app.ts")  # touched, same content: not a change
        report = self.out("changes").splitlines()
        self.assertIn("M src/pkg/models.py  (-", "\n".join(report))
        self.assertIn("A src/new.py", report)
        self.assertIn("D docs/guide.md", report)
        self.assertFalse(any("app.ts" in line for line in report))
        self.out("changes", "mark=true")
        self.assertTrue(self.out("changes").splitlines()[0].endswith(": 0"))


    def test_untracked_subtrees_are_disclosed(self) -> None:
        self.out("changes", "mark=true")
        quiet = self.out("changes").splitlines()
        self.assertTrue(quiet[0].endswith(": 0"))
        self.assertIn("node_modules/", quiet[-1])  # named even when nothing moved
        self.assertTrue(quiet[-1].startswith("# contents untracked:"))
        self.write("node_modules/newpkg/index.js", "x\n")  # direct child added: folder time moves
        report = self.out("changes").splitlines()
        self.assertIn(", 1 uncertain", report[0])
        self.assertIn("? node_modules/  (folder changed; contents untracked)", report)
        self.assertIn("mark=true to acknowledge", "\n".join(report))
        respecting = self.out("changes", "gitignore=true")
        self.assertNotIn("node_modules", respecting)

    def _rows(self) -> dict:
        import sqlite3

        con = sqlite3.connect(self.instance / "state" / "workbench.sqlite3")
        try:
            return {t: con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                    for t in ("observations", "relations", "claims", "epistemic_evidence")}
        finally:
            con.close()

    def test_unchanged_snapshots_do_not_grow(self) -> None:
        for _ in range(3):  # fill the retention window (last 3 snapshot inventories)
            self.out("changes", "mark=true")
        settled = self._rows()
        for _ in range(3):
            self.out("changes", "mark=true")
        self.assertEqual(self._rows(), settled)
        self.write("src/app.ts", "export class App {}\n")  # a real change is still recorded
        self.assertIn("M src/app.ts", self.out("changes"))
        self.out("changes", "mark=true")
        self.assertGreater(self._rows()["observations"], settled["observations"])
        self.assertTrue(self.out("changes").splitlines()[0].endswith(": 0"))
        self.assertIn("freshness=current", self.out("awareness.current"))

    def test_provenance_survives_deduplicated_storage(self) -> None:
        self.out("changes", "mark=true")
        rows = self._rows()
        self.assertLess(rows["relations"], 10)  # links are derived on read, not stored
        claims = json.loads(self.out("substrate.claims"))
        text_claim = next(c for c in claims if c["claim_type"] == "target_has_text_files")
        trace = json.loads(self.out("substrate.trace", "handle=" + text_claim["claim_id"]))
        predicates = {r["predicate"] for r in trace["relations"]}
        self.assertTrue({"derived_from_set", "derived_from", "concerns", "supported_by"} <= predicates)
        kinds = {n["type"] for n in trace["nodes"]}
        self.assertTrue({"claim", "observation", "resource", "evidence"} <= kinds)
        derived = next(r for r in trace["relations"] if r["predicate"] == "supported_by")
        relation = json.loads(self.out("substrate.relation", "relation_id=relation:" + derived["relation_id"]))
        self.assertEqual(relation["object_id"], derived["object_id"])
        observation = json.loads(self.out("substrate.observation", "observation_id=" + derived["subject_id"]))
        self.assertTrue(observation["data"]["path"])  # rebuilt from its evidence
        resource = json.loads(self.out("substrate.resource", "handle=path:src/app.ts"))
        version = json.loads(self.out("substrate.trace", "handle=" + resource["latest_version_id"]))
        self.assertEqual({r["predicate"] for r in version["relations"]}, {"version_of", "supported_by"})
        current = self.out("awareness.current")
        item_id = current.split("[")[1].split("]")[0]
        drill = json.loads(self.out("awareness.drill", "item_id=" + item_id))
        self.assertTrue(drill["nodes"])

    def test_same_size_edit_is_detected(self) -> None:
        self.out("changes", "mark=true")
        path = self.root / "docs/guide.md"
        before = path.stat()
        path.write_text(path.read_text(encoding="utf-8").replace("Guide", "Gyide"), encoding="utf-8", newline="")
        os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns + 1_000_000_000))
        self.assertIn("M docs/guide.md  (+0B)", self.out("changes"))

    def test_snapshot_ignore_tracks_folder_only(self) -> None:
        for i in range(30):
            self.write(f"corpus/part/doc{i}.txt", f"doc {i}\n")
        (self.instance / "snapshot-ignore").write_text("corpus/\n", encoding="utf-8")
        self.out("changes", "mark=true")
        self.assertLess(self._rows()["observations"], 40)  # 30 corpus files not recorded
        self.write("corpus/new.txt", "x\n")
        report = self.out("changes")
        self.assertIn("? corpus/  (folder changed; contents untracked)", report)
        self.assertIn("corpus/", report.splitlines()[-2])

    def test_gitignore_flag(self) -> None:
        self.out("changes", "mark=true")
        self.write("build/new.txt", "generated\n")      # ignored dir (.gitignore: build/)
        self.write("debug.log", "noise\n")              # ignored pattern (*.log)
        self.write("src/real.py", "x = 1\n")
        (self.root / "build/out.txt").unlink()           # removal inside ignored dir
        seen_all = self.out("changes")
        self.assertIn("A debug.log", seen_all)
        self.assertIn("A src/real.py", seen_all)
        respecting = self.out("changes", "gitignore=true").splitlines()
        self.assertIn("respecting .gitignore", respecting[0])
        self.assertIn("A src/real.py", respecting)
        self.assertFalse(any("build/" in line or ".log" in line for line in respecting[1:]))
        self.assertEqual(self.out("changes", "gitignore=false"), seen_all)


class AwarenessToolTests(Target):
    def setUp(self) -> None:
        super().setUp()
        self.write("lib/kit/__init__.py", "from .graph import build\n")
        self.write("lib/kit/graph.py", (
            "import networkx as nx\nfrom .embed import vec\n\n\nclass Store:\n"
            "    def add(self, x):\n        return vec(x)\n\n\ndef build(rows):\n"
            "    s = Store()\n    for r in rows:\n        s.add(r)\n    return s\n"))
        self.write("lib/kit/embed.py", "import ollama\nfrom . import graph\n\n\ndef vec(x):\n    return ollama\n")
        self.write("main.py", "import json\nfrom kit import build\n\nif __name__ == '__main__':\n    build([])\n")
        self.write("requirements.txt", "networkx\nscikit-learn\n")
        self.write("web/index.html", '<title>T</title><main id="app"></main><script src="a.js"></script>'
                                     '<img src="gone.png">\n')
        self.write("web/a.js", "import { b } from './b';\n")
        self.write("web/b.js", "export const b = 1;\n")
        notebook = {"cells": [
            {"cell_type": "markdown", "source": ["# Look\n"]},
            {"cell_type": "code", "source": ["%time\n", "from kit.graph import build\n", "def f(x):\n", "    return x"],
             "outputs": [{"output_type": "stream", "text": ["hello\n"]}]},
        ], "metadata": {}, "nbformat": 4, "nbformat_minor": 5}
        self.write("nb/look.ipynb", json.dumps(notebook))

    def test_read_symbol(self) -> None:
        text = self.out("read", "path=lib/kit/graph.py", "symbol=add")
        self.assertTrue(text.startswith("# lib/kit/graph.py:Store.add (lines 6-7 of 14)"))
        self.assertIn("close: build", self.out("read", "path=lib/kit/graph.py", "symbol=buld"))
        section = self.out("read", "path=docs/guide.md", "symbol=Setup")
        self.assertTrue(section.startswith("# docs/guide.md:Setup (lines 3-7 of 8)"))  # fenced '#' is not a heading

    def test_notebook_view(self) -> None:
        text = self.out("read", "path=nb/look.ipynb")
        self.assertIn("# %% [2] code", text)
        self.assertIn("#    hello", text)
        self.assertNotIn('"cell_type"', text)
        self.assertIn('"cell_type"', self.out("read", "path=nb/look.ipynb", "raw=true"))
        self.assertIn("def f(x):", self.out("read", "path=nb/look.ipynb", "symbol=f"))

    def test_refs(self) -> None:
        text = self.out("refs", "name=build", "text=false")
        self.assertIn("def:\n  lib/kit/graph.py:10 def build(rows):", text)
        self.assertIn("main.py:2 from kit import build", text)
        self.assertIn("call:\n  main.py:5", text)
        self.assertIn("lib/kit/graph.py:13 s.add(r)", self.out("refs", "name=Store.add"))

    def test_deps(self) -> None:
        text = self.out("deps")
        self.assertIn("main.py -> lib/kit/__init__.py", text)
        self.assertIn("nb/look.ipynb -> lib/kit/graph.py", text)
        self.assertIn("cycle: lib/kit/__init__.py <-> lib/kit/embed.py <-> lib/kit/graph.py", text)
        self.assertIn("imported, not declared: ollama", text)
        self.assertIn("declared, not imported: scikit-learn", text)
        self.assertIn("web/a.js -> web/b.js", text)
        self.assertIn("web/index.html -> web/gone.png", text)
        focus = self.out("deps", "module=lib/kit/graph.py")
        self.assertIn("imported by: lib/kit/__init__.py, lib/kit/embed.py, nb/look.ipynb", focus)

    def test_map(self) -> None:
        text = self.out("map")
        self.assertIn("entry points: main.py", text)
        self.assertIn("ai/llm: ollama", text)
        self.assertIn("graph: networkx", text)
        self.assertNotIn("other imports: kit", text)
        self.assertIn("not counted (ignored): build/", text)


class SchemaTests(Target):
    def test_data_formats(self) -> None:
        import sqlite3

        (self.root / "data").mkdir()
        con = sqlite3.connect(self.root / "data/kb.db")
        con.executescript(
            "CREATE TABLE orgs (id INTEGER PRIMARY KEY, name TEXT NOT NULL);"
            "CREATE TABLE docs (id INTEGER PRIMARY KEY, org_id INTEGER REFERENCES orgs(id), title TEXT);"
            "CREATE INDEX docs_org ON docs(org_id); INSERT INTO orgs VALUES (1,'a'),(2,'b');")
        con.commit()
        con.close()
        self.write("data/items.csv", "id,label,weight,note\n" + "".join(
            f"{i},{'cat' if i % 2 else 'dog'},{i / 2},{'' if i % 3 else 'x'}\n" for i in range(1, 31)))
        self.write("data/cfg.json", json.dumps({"name": "g", "nodes": [{"id": 1}, {"id": 2, "meta": {"k": "v"}}]}))
        self.write("data/ev.jsonl", "\n".join(json.dumps({"t": i, **({"x": 1.5} if i % 2 else {})}) for i in range(6)))
        self.write("data/g.graphml", '<graphml xmlns="http://graphml.graphdrawing.org/xmlns"><key id="d0" for="node" '
                   'attr.name="label" attr.type="string"/><graph edgedefault="undirected"><node id="a"/>'
                   '<node id="b"/><edge source="a" target="b"/></graph></graphml>')
        text = self.out("schema", "path=data")
        self.assertIn("sqlite, 2 tables, 0 views", text)
        self.assertIn("docs [0 rows]: id INTEGER PK, org_id INTEGER -> orgs.id, title TEXT", text)
        self.assertIn("index docs_org(org_id)", text)
        self.assertIn("csv, 30 rows x 4 cols", text)
        self.assertIn("  label str 2 distinct  e.g. cat", text)
        self.assertIn("  weight float", text)
        self.assertIn("  note str nulls 20/30", text)
        self.assertIn("    meta?: object", text)
        self.assertIn("jsonl, 6 records of object", text)
        self.assertIn("  x?: float", text)
        self.assertIn("graphml, 2 nodes, 1 edges, undirected", text)
        self.assertIn("unsupported", self.fails("schema", "path=docs/guide.md"))


class _FakeOllama:
    """Minimal stand-in for the Ollama HTTP API on a free local port."""

    def __enter__(self):
        import http.server
        import threading

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args) -> None:
                pass

            def _send(self, body: dict, code: int = 200) -> None:
                data = json.dumps(body).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self) -> None:
                self._send({"models": [{"name": "tiny:1b", "size": 1000, "details": {"parameter_size": "1B"}}]})

            def do_POST(self) -> None:
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                if body.get("model") == "missing":
                    return self._send({"error": "model 'missing' not found"}, 404)
                if self.path == "/api/embed":
                    vectors = {"a": [1.0, 0.0], "b": [0.0, 1.0]}
                    return self._send({"embeddings": [vectors.get(t, [1.0, 1.0]) for t in body["input"]]})
                if self.path == "/api/chat":
                    return self._send({"message": {"content": f"chat:{len(body['messages'])}"}, "eval_count": 2})
                self._send({"response": f"echo:{body.get('prompt')}:{body.get('system')}",
                            "thinking": "hmm" if body.get("think") else "", "eval_count": 3,
                            "eval_duration": 1_000_000_000, "total_duration": 2_000_000_000})

        self.server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self._previous = os.environ.get("OLLAMA_HOST")
        os.environ["OLLAMA_HOST"] = f"127.0.0.1:{self.server.server_address[1]}"
        return self

    def __exit__(self, *exc) -> None:
        self.server.shutdown()
        self.server.server_close()
        if self._previous is None:
            os.environ.pop("OLLAMA_HOST", None)
        else:
            os.environ["OLLAMA_HOST"] = self._previous


class OllamaTests(Target):
    def test_run_sweep_chat_embed(self) -> None:
        with _FakeOllama():
            self.assertIn("tiny:1b", self.out("ollama", "action=models"))
            single = self.out("ollama", "model=tiny:1b", "prompt=hi", "system=be brief")
            self.assertTrue(single.startswith("echo:hi:be brief"))
            self.assertIn("# tiny:1b · 0+3 tok · 3.0 tok/s · 2.0s", single)
            self.write("p.txt", "from file")
            sweep = self.out("ollama", "--args", json.dumps({"model": ["a:1", "b:1"], "prompts": ["x"],
                                                              "prompt_file": "p.txt"}))
            self.assertEqual(sweep.count("## "), 4)
            self.assertIn("echo:from file", sweep)
            self.assertIn("thinking: 3 chars hidden", self.out("ollama", "model=t", "prompt=q", "think=true"))
            chat = self.out("ollama", "--args", json.dumps({"model": "t", "system": "s",
                                                             "messages": [{"role": "user", "content": "u"}]}))
            self.assertTrue(chat.startswith("chat:2"))
            embed = self.out("ollama", "action=embed", "model=e", "--args", '{"prompts":["a","b"]}')
            self.assertIn("e: 2 inputs x 2 dims\n  1: 1.00 0.00\n  2: 0.00 1.00", embed)
            self.assertIn("model 'missing' not found", self.fails("ollama", "model=missing", "prompt=x"))
            envelope = json.loads(self.out("ollama", "model=t", "prompt=keep", "--json"))
            self.assertEqual(envelope["result"]["runs"][0]["prompt"], "keep")  # full record receipted

    def test_needs_sandbox_and_contained_files(self) -> None:
        self.assertIn("authority_denied", self.fails("ollama", "action=models", "--authority", "observe"))
        self.assertIn("containment_refusal", self.fails("ollama", "model=t", "prompt_file=../x"))

    def test_unreachable(self) -> None:
        previous = os.environ.get("OLLAMA_HOST")
        os.environ["OLLAMA_HOST"] = "127.0.0.1:9"
        try:
            self.assertIn("not reachable", self.fails("ollama", "action=models", "timeout=2"))
        finally:
            if previous is None:
                os.environ.pop("OLLAMA_HOST", None)
            else:
                os.environ["OLLAMA_HOST"] = previous


class PackTests(Target):
    def test_pack_unpack_self_initialises(self) -> None:
        import zipfile

        self.out("journal.add", "title=private history", "--authority", "sandbox")
        self.write(".claude/settings.local.json", "{}\n")
        self.write(".claude/settings.json", "{}\n")
        self.write(".dev/PLAN.md", "skeleton's own plan\n")
        self.write("src/.dev/keep.txt", "only the top-level .dev is private\n")
        archive = Path(self._tmp.name) / "skeleton.zip"
        self.out("pack", str(archive))
        names = zipfile.ZipFile(archive).namelist()
        self.assertIn(".tools/bin/helpers.py", names)
        self.assertIn("src/pkg/models.py", names)
        self.assertIn(".claude/settings.json", names)  # shared settings travel; local ones don't
        self.assertIn("src/.dev/keep.txt", names)
        self.assertFalse([n for n in names if n.startswith((".tools/state", ".tools/instance.json",
                                                              ".claude/settings.local.json", ".dev/"))])
        clutter = Path(self._tmp.name) / "old-files"  # extract into an existing folder of files
        clutter.mkdir()
        (clutter / "notes.txt").write_text("old\n")
        zipfile.ZipFile(archive).extractall(clutter)
        result = subprocess.run([sys.executable, str(clutter / ".tools/bin/helpers.py"), "journal.list"],
                                capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(json.loads(result.stdout), [])  # fresh history, not the packed one
        first = json.loads((self.instance / "instance.json").read_text())["instance_uuid"]
        second = json.loads((clutter / ".tools/instance.json").read_text())["instance_uuid"]
        self.assertNotEqual(first, second)

    def test_archive_unpacks_itself_without_overwriting(self) -> None:
        import zipfile

        archive = Path(self._tmp.name) / "skel.zip"
        self.out("pack", str(archive))
        self.assertTrue((Path(self._tmp.name) / "skel-unpack.bat").is_file())
        self.assertIn("__main__.py", zipfile.ZipFile(archive).namelist())
        self.assertIn("outside the project", self.fails("pack", str(self.root / "self.zip")))
        target = Path(self._tmp.name) / "dest"
        target.mkdir()
        (target / "docs").mkdir()
        (target / "docs/guide.md").write_text("my existing guide\n")
        run = subprocess.run([sys.executable, str(archive), str(target)], capture_output=True, text=True)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        self.assertIn("kept 1 existing files unchanged: docs/guide.md", run.stdout)
        self.assertEqual((target / "docs/guide.md").read_text(), "my existing guide\n")
        self.assertTrue((target / ".tools/bin/helpers.py").is_file())
        self.assertFalse((target / "__main__.py").exists())


class McpTests(Target):
    def session(self, authority: str, *requests: dict, surface: str = "default") -> list[dict]:
        with self.core() as (_, context):
            from core import mcp

            lines = [json.dumps({"jsonrpc": "2.0", "id": i, **r}) for i, r in enumerate(requests)]
            output = io.StringIO()
            mcp.serve(context, authority=authority, surface=surface,
                      input_stream=io.StringIO("\n".join(lines) + "\n"), output_stream=output)
            return [json.loads(line) for line in output.getvalue().splitlines()]

    def names(self, authority: str, surface: str = "default") -> list[str]:
        (response,) = self.session(authority, {"method": "tools/list"}, surface=surface)
        return [tool["name"] for tool in response["result"]["tools"]]

    def test_listing_follows_authority(self) -> None:
        self.assertEqual(self.names("observe"), ["changes", "outline", "ls", "grep", "read", "run"])
        self.assertEqual(self.names("apply"), ["changes", "outline", "ls", "grep", "read", "edit", "write", "run"])
        self.assertEqual(self.names("apply", surface="minimal"), ["run"])

    def test_client_cannot_self_elevate(self) -> None:
        (response,) = self.session("observe", {"method": "tools/call", "params": {
            "name": "run", "arguments": {"op": "edit", "args": {
                "path": "src/app.ts", "old": "App", "new": "X", "_authority": "apply"}}}})
        self.assertTrue(response["result"]["isError"])
        self.assertIn("App", (self.root / "src/app.ts").read_text())

    def test_handshake_ping_batch(self) -> None:
        init, ping, batch = self.session(
            "observe",
            {"method": "initialize", "params": {"protocolVersion": "2025-03-26"}},
            {"method": "ping"},
            {"method": "tools/call", "params": {"name": "run", "arguments": {"calls": [
                {"op": "ls", "args": {"depth": 1}}, {"op": "help"}]}}},
        )
        self.assertEqual(init["result"]["protocolVersion"], "2025-03-26")
        self.assertEqual(ping["result"], {})
        text = batch["result"]["content"][0]["text"]
        self.assertIn("## 1. ls", text)
        self.assertIn("mutation.apply approval_id preview_id?  (needs apply)", text)
        self.assertNotIn("structuredContent", batch["result"])


class RegressionTests(Target):
    """T1 (.dev/PLAN.md): each test failed before its fix."""

    def test_path_vanishing_mid_walk_is_absent_not_a_crash(self) -> None:
        with self.core() as (core, context):
            from core import substrate

            real_lstat = substrate.os.lstat

            def lstat(path, *args, **kwargs):
                if str(path).replace("\\", "/").endswith("docs/guide.md"):
                    raise FileNotFoundError(2, "vanished", str(path))
                return real_lstat(path, *args, **kwargs)

            substrate.os.lstat = lstat
            try:
                report = substrate.changes(context)
                substrate.refresh(context)
            finally:
                substrate.os.lstat = real_lstat
        self.assertNotIn("docs/guide.md", report["added"])
        self.assertIn("docs/win.txt", report["added"])

    def test_changes_without_baseline_does_not_hash(self) -> None:
        with self.core() as (core, context):
            from core import substrate

            real_sha256, calls = substrate.hashlib.sha256, []

            def counting(*args, **kwargs):
                calls.append(1)
                return real_sha256(*args, **kwargs)

            substrate.hashlib.sha256 = counting
            try:
                report = substrate.changes(context)
            finally:
                substrate.hashlib.sha256 = real_sha256
        self.assertIn("no snapshot yet", report["text"])
        self.assertEqual(calls, [])

    def test_refs_reports_decorated_definition_at_its_def_line(self) -> None:
        self.write("deco.py", "import functools\n\n@functools.cache\n@staticmethod\ndef cached():\n    return 1\n")
        text = self.out("refs", "name=cached", "text=false")
        self.assertIn("def:\n  deco.py:5 def cached():", text)

    def test_read_flags_invalid_utf8(self) -> None:
        (self.root / "latin.txt").write_bytes(b"caf\xe9\n")
        self.assertIn("[not valid UTF-8", self.out("read", "path=latin.txt"))
        self.write("literal.txt", "a real � character\n")  # valid UTF-8: no notice
        self.assertNotIn("not valid UTF-8", self.out("read", "path=literal.txt"))

    def test_cli_accepts_op_equals_form(self) -> None:
        self.assertIn("authority held: apply", self.out("run", "op=help"))
        self.assertIn("read [observe]", self.out("run", "op=help", "op=read"))
        self.assertIn("1\t# Guide", self.out("run", "op=read", "path=docs/guide.md"))

    def test_unpack_repoints_mcp_command_when_python_is_missing(self) -> None:
        self.write(".mcp.json", json.dumps({"mcpServers": {"helpers": {
            "type": "stdio", "command": "python", "args": [".tools/bin/helpers.py", "mcp"]}}}))
        archive = Path(self._tmp.name) / "skel.zip"
        self.out("pack", str(archive))
        environment = {k: v for k, v in os.environ.items() if k.upper() != "PATH"}
        environment["PATH"] = ""  # neither python nor python3 can be found
        target = Path(self._tmp.name) / "bare"
        run = subprocess.run([sys.executable, str(archive), str(target)], capture_output=True,
                             text=True, env=environment)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        config = json.loads((target / ".mcp.json").read_text(encoding="utf-8"))
        self.assertEqual(config["mcpServers"]["helpers"]["command"], sys.executable)
        if shutil.which("python"):  # where `python` exists, the file is left as packed
            normal = Path(self._tmp.name) / "normal"
            subprocess.run([sys.executable, str(archive), str(normal)], capture_output=True, text=True)
            config = json.loads((normal / ".mcp.json").read_text(encoding="utf-8"))
            self.assertEqual(config["mcpServers"]["helpers"]["command"], "python")


class RetentionTests(Target):
    """T2 (.dev/PLAN.md decision 4): observe results are bounded; nothing cited is lost."""

    def flood(self, count: int) -> list[str]:
        """Record `count` observe-only calls with ~8 KB of incompressible result each."""
        with self.core() as (core, context):
            from core import runtime_records

            receipts = []
            for _ in range(count):
                receipt = runtime_records.begin_receipt(context, tool_id="read", client="test",
                                                        authority="observe")
                runtime_records.complete_receipt(context, receipt, status="success",
                                                 envelope={"blob": os.urandom(4000).hex()},
                                                 observation=True)
                receipts.append(receipt)
            return receipts

    def state(self) -> tuple[int, int, int]:
        """(observation artifacts, receipts, database bytes after a WAL checkpoint)."""
        import sqlite3

        path = self.instance / "state/workbench.sqlite3"
        connection = sqlite3.connect(path)
        try:
            connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            kept = connection.execute(
                "SELECT COUNT(*) FROM operational_artifacts WHERE kind = 'observation'").fetchone()[0]
            receipts = connection.execute("SELECT COUNT(*) FROM operation_receipts").fetchone()[0]
        finally:
            connection.close()
        return kept, receipts, path.stat().st_size

    def test_observe_results_are_bounded_and_state_stops_growing(self) -> None:
        self.flood(300)
        kept, receipts, size = self.state()
        self.assertLessEqual(kept, 250)
        self.assertGreaterEqual(receipts, 300)  # every receipt row survives
        self.flood(300)
        kept_again, receipts_again, size_again = self.state()
        self.assertLessEqual(kept_again, 250)
        self.assertGreaterEqual(receipts_again, 600)
        self.assertLess(size_again, size * 1.15, "state kept growing under read-only use")

    def test_writes_and_journal_linked_results_survive(self) -> None:
        cited = json.loads(self.out("read", "path=docs/guide.md", "--json"))
        linked = json.loads(self.out("read", "path=src/app.ts", "--json"))
        edit = json.loads(self.out("edit", "path=src/app.ts", "old=App", "new=Main", "--json"))
        entry = json.loads(self.out("run", "journal.add", "title=evidence"))["entry_id"]
        self.out("run", "journal.link", f"entry_id={entry}", f"target_id={cited['receipt_id']}")
        self.out("run", "journal.link", f"entry_id={entry}", f"target_id={linked['artifact_id']}")
        early = self.flood(1)[0]
        self.flood(300)
        for survivor in (cited, linked, edit):
            self.out("run", "artifacts.read", f"artifact_id={survivor['artifact_id']}")
        receipt = json.loads(self.out("run", "receipts.read", f"receipt_id={early}"))
        self.assertIsNone(receipt["artifact_id"])
        self.assertIn("pruned under retention", receipt["artifact"])


class DocsTests(unittest.TestCase):
    """Every op, tool or command the documents name must exist (.dev/PLAN.md S2)."""

    # Namespaces that ever held ops: a doc still naming a removed one is caught.
    NAMESPACES = {"receipts", "artifacts", "journal", "substrate", "awareness", "mutation"}
    COMMANDS = {"help", "init", "status", "mcp", "mcp-config", "pack", "run"}

    @classmethod
    def setUpClass(cls) -> None:
        sys.path.insert(0, str(SOURCE))
        try:
            from core import mcp, operations

            cls.ops = set(operations.OPERATIONS)
            cls.tools = {path.parent.name for path in (SOURCE / "tools").glob("*/manifest.json")}
            cls.mcp_text = mcp._RUN_DESCRIPTION + "\n" + mcp.INSTRUCTIONS
        finally:
            sys.path.remove(str(SOURCE))
            for name in [n for n in sys.modules if n == "core" or n.startswith("core.")]:
                del sys.modules[name]

    def unknown(self, text: str) -> set[str]:
        import re

        spans = re.findall(r"```.*?```", text, re.S) + re.findall(r"`([^`\n]+)`", text)
        known = self.ops | self.tools | self.COMMANDS
        bad: set[str] = set()
        for span in spans:
            for prefix, rest in re.findall(r"\b([a-z]+)\.([a-z_]+)\b", span):
                if prefix in self.NAMESPACES and f"{prefix}.{rest}" not in self.ops:
                    bad.add(f"{prefix}.{rest}")
            for name in re.findall(r'(?:\brun\b|\bhelpers\b|\bop=)\s*"?([a-z][a-z_.-]*)', span):
                if name != "op" and name not in known:
                    bad.add(name)
        return bad

    def test_extractor_catches_unknown_names(self) -> None:
        self.assertEqual(self.unknown("`run journal.bogus` and `substrate.nothing` and `op=nope`"),
                         {"journal.bogus", "substrate.nothing", "nope"})
        self.assertEqual(self.unknown("`run journal.add title=x` `helpers help` `run op=help`"), set())

    def test_documents_name_only_existing_ops(self) -> None:
        root = SOURCE.parent
        documents = [SOURCE / "README.md", root / "AGENTS.md", root / "README.md",
                     *sorted((root / ".framework").glob("*.md"))]
        for path in [p for p in documents if p.is_file()]:
            with self.subTest(document=path.name):
                self.assertEqual(self.unknown(path.read_text(encoding="utf-8")), set())
        with self.subTest(document="MCP instructions"):
            self.assertEqual(self.unknown("```" + self.mcp_text + "```"), set())


if __name__ == "__main__":
    unittest.main(verbosity=1)
