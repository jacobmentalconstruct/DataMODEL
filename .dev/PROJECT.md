# Project Definition: DataMODEL (the skeleton itself)

This folder (`.dev/`) holds the definition and plan for developing the skeleton. The root
`PROJECT.md` and `PLAN.md` are blank templates that ship to every project unpacked from it;
`pack` leaves `.dev/` out.

## Identity

**Name:** DataMODEL
**Short description:** A reusable project skeleton for working with AI agents: a standing
way of working (`.framework/`), living per-project `PROJECT.md`/`PLAN.md`, and a governed,
stdlib-only local toolset (`.tools/`) with an MCP server.
**Purpose:** Unzip it into an empty folder, a Python project, or a pile of old files, and
an agent can orient, plan with the user, and work in small verified steps, with cheap and
exact project awareness and a record of what it did.

## Intended user

One human user (Python, HTML, data science, generative text pipelines, graph-shaped data,
local models via Ollama) working with agents: Claude Code today, local Ollama models only
later. When that switch happens Claude Code's own tools go away, so everything the work
needs must come from the skeleton itself.

## Primary capabilities

- Unpack into any folder without overwriting it; the tools set themselves up on first use.
- Orient cheaply: project map, symbol outlines, import graphs, references, search, data
  file schemas, and "what changed since last time".
- Change files safely (exact-text edits, explicit overwrites), contained to the project.
- Keep a project record: decision journal and receipts of what was run.
- Run local-model experiments through Ollama, recorded.
- Run allowlisted commands (tests, git, scripts) in the project, contained, timed out and
  receipted, with the allowlist editable per project.
- Work with a local Ollama model through a minimal agent loop that drives the same tools,
  so a local model can do what an agent does today without Claude Code.
- Pack the project back into a self-unpacking archive without private state.

## Boundaries

**In scope:** the framework documents, the toolset, pack/unpack, tests, the local agent
loop.
**Out of scope:** any particular product built with the skeleton; network services; web
access for models (local-only for now); non-stdlib dependencies (optional extras such as
pyarrow excepted).
**Deferred:** `changes detail=true` (changed symbols), cached index, tree-sitter as an
optional backend; a per-call approval gate (approval is per tranche for now); web fetch.

## Constraints

- Python 3.10+, standard library only; Windows, macOS, Linux.
- Local-only; `ollama` talks only to `OLLAMA_HOST`.
- Lean by default: small code, small outputs, small context needed to use the tools.
- Must scale to large folders (tens of thousands of files) through `snapshot-ignore`.

## Completion condition

The skeleton is complete when the stop conditions in `.dev/PLAN.md` hold: the toolset is
lean with one responsibility per module and one write path, docs match behaviour exactly,
state storage is bounded, known bugs are fixed with tests, a local Ollama model can work
a small task on a real sample project through the local loop (orient, read, edit, run
the tests, report), and a scripted end-to-end run on that project passes on the oldest
and newest supported Python.

## Known unknowns

- The local loop's shape: default model, context budget for small models, how a run is
  started, stopped and recorded. Settled when its tranche is declared.

## Current Decision

**Definition status:** REVISING (2026-09-28): the local-loop and command-runner
capabilities were added from the user's answers; the earlier definition was DEFINED on
2026-09-28. Awaiting the user's confirmation of this wording.
