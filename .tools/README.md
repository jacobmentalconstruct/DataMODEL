# helpers: local project tools for agents

Governed, local tools for the project that contains this `.tools/` folder. Every call is
contained to the project, checked against its contract, and receipted in
`state/workbench.sqlite3`; what comes back is compact text.

## Setup

None. On first use, a fresh copy creates its own identity (`instance.json`) and history
(`state/`); `init` does the same explicitly.
The project's `.mcp.json` registers the MCP server (`helpers mcp-config` prints the entry).
Authority is fixed there, not by the caller: `observe` read-only, `sandbox` + journal
and `ollama`, `apply` + edit/write/approve.

## Pack / unpack

`pack.bat` (double-click) / `sh .tools/pack.sh` / `helpers pack [out.zip]` writes
`<Project>.zip` beside the project (leading dots dropped from the name) and two
launchers, `<Project>-unpack.bat` / `.sh`. It leaves out this copy's `instance.json`,
`state/`, `logs/`, `.git`, caches, `.claude/settings.local.json` and a top-level `.dev/`
(the skeleton's own plan), so every unpacked project starts with its own identity and an
empty history. Don't zip by hand: a copied `state/` would carry this
project's receipts, journal and snapshots into the next one.

The archive unpacks itself: `python <Project>.zip [folder]` (with no folder, a folder
picker opens, falling back to a typed prompt). Contents sit at the zip root and existing
files are never overwritten, so it can go straight into a folder of old files. Where
`python` is not on PATH (common on macOS/Linux), unpacking points the new `.mcp.json` at
`python3` or at the interpreter that ran it.

## Tools

| tool | use | args |
|---|---|---|
| `changes` | files added/removed/modified since the last snapshot | `mark gitignore limit` |
| `outline` | symbols + line numbers for a file or tree (py/js/ts/go/rs/java/cs/c/rb/php/sh/sql/md/json/yaml/toml) | `path glob depth symbols limit all` |
| `ls` | list/find paths; cut-off dirs show `(+N files)` | `path glob depth kind sort sizes limit all` |
| `grep` | regex search → `path:line:text` | `pattern path glob mode context ignore_case literal multiline limit all` |
| `read` | numbered lines, many files per call; `symbol=` extracts one def/class/method/section; notebooks as cell view; flags files that are not valid UTF-8 | `path\|paths symbol offset limit numbers raw max_bytes` |
| `edit` | exact-text replace; `edits[]` atomic; keeps CRLF/BOM | `path old new all\|edits` |
| `write` | create a file; `overwrite=true` to replace | `path content overwrite` |
| `hash` | sha256 + size | `path\|paths` |
| `map` | project card: languages, layout, entry points, tests, data/graph/model files, library stack (ai/llm, vectors, data, graph, text, viz, web, ui) | `path all` |
| `deps` | Python import graph (cycles, importers), external vs declared packages, local HTML/JS links and missing targets | `path module limit all` |
| `refs` | where a name is defined, imported, called, used (exact for Python; word match elsewhere) | `name path glob text limit all` |
| `schema` | data file structure: SQLite tables/keys/indexes/rows (read-only, immutable), CSV types/nulls/examples, JSON/JSONL shape (`?` = optional), GraphML/GEXF counts + attributes, Parquet via pyarrow | `path sample all` |
| `ollama` | local models (needs `sandbox`): `run` prompt(s) × model(s), chat `messages`, `format` json/schema, `think`; `embed` → similarity matrix; `models`, `ps`, `load`, `unload` | `action model prompt prompts prompt_file system system_file messages options format think show_thinking keep_alive timeout` |

`ollama` talks only to `OLLAMA_HOST` (default `127.0.0.1:11434`); there is no host argument, so
project files can't be sent elsewhere. Each call's full record (prompts, options, responses,
thinking, metrics, embeddings) is kept as its receipt artifact (`run artifacts.list` /
`artifacts.read`); the agent only sees the responses plus a stats line.

Notebooks (`.ipynb`) are read, outlined and searched as a percent-format cell view with
short text outputs. `raw=true` gives the JSON, which is what `edit` operates on.
Symbols: Python via `ast` (`Class.method`), Markdown/notebook headings, HTML
title/headings/ids/scripts, and line patterns for other languages.

`.gitignore`d and vendor dirs are skipped unless `all=true`. Paths are project-relative;
`.tools/` itself is unreachable.

`changes` reports against the last snapshot; `mark=true` re-snapshots. Before the first
snapshot it only counts files (nothing is read). A file deleted while the folder is being
scanned is simply absent from that scan. By default it
sees everything the snapshot tracks. Vendor/generated/`.git` folders are tracked only as
folders: they are named in a footer, and flagged `?` when their own timestamp moves (a
direct child added or removed; deeper edits don't move it). `gitignore=true` drops
ignored and vendor paths entirely.

Cost and scale: a file whose size and modification time match the snapshot is not
re-read; its recorded hash is reused, which is the same test git uses. Small text files are
content-hashed; PDFs, media and files of 1 MB or more are tracked by size and time only.
Repeated snapshots of an unchanged tree add nothing: observations are re-recorded only
when a file changes, derived claims are kept for the latest snapshot only, and the last 3
snapshot inventories are retained. Storage is deduplicated: evidence is content-addressed
and zlib-compressed, identical file profiles are stored once, observations point at their
evidence instead of copying it, provenance links are derived from rows on read, and freed
pages are returned (incremental vacuum). Budget about 2.6 KB of state per tracked file
(20,000 files: ~2.5 s `changes`, ~11 s `mark`, ~52 MB, flat across snapshots). For bulk folders, add them to
`.tools/snapshot-ignore` (gitignore syntax). They are then tracked as single folder
entries (`?` in `changes` when they change), while `ls`, `grep`, `schema` etc. still see
inside.

New project: `map`, then `outline`/`deps` where it points. Returning: `changes`, then
`outline` only what it names, then `changes mark=true`. Before changing a function:
`refs name=X` for its callers, `read symbol=X` for its body.

## Calling

MCP: `changes outline ls grep read edit write` directly; `map deps refs schema ollama hash` and the rest via
`run {op, args}`, `run {op:"help"}` or `run {calls:[{op,args},...]}` (batch).

CLI: `python .tools/bin/helpers.py <tool|op> key=value ...` (`run op=<name> ...`, the MCP
form, works too). A value of `@-` reads
stdin, `--args '<json>'` covers arrays and objects, and `--json` prints the full envelope.
The CLI holds `apply` unless `--authority observe|sandbox`.

```
helpers outline path=src
helpers grep "pattern=def \w+" glob=*.py context=2
helpers edit path=a.py "old=x = 1" "new=x = 2"
helpers write path=notes.md content=@- < notes.md
helpers schema path=data
helpers ollama model=qwen2.5:7b prompt_file=prompts/a.md options='{"temperature":0,"seed":1}'
helpers ollama --args '{"model":["qwen2.5:3b","qwen2.5:7b"],"prompts":["p1","p2"]}'
helpers ollama action=embed model=nomic-embed-text --args '{"prompts":["a","b","c"]}'
helpers help
```

Other ops (`helpers help` lists them): `status`, `receipts.*`, `artifacts.*`, `journal.*`,
`substrate.*`, `awareness.*`, and the reviewed write
`mutation.preview_write → mutation.approve → mutation.apply`.

## Tests

`python .tools/tests/test_helpers.py`
