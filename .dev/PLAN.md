# Plan: DataMODEL skeleton cleanup

Scope: the skeleton's own code and documents (see `.dev/PROJECT.md`). Measurements come
from `python .dev/bench.py` so every tranche compares like with like. Sample project for
field tests: a *copy* of `C:\Jacob\_AppDesign\sample-targets-for-apps\_theCELL` (the
original is never modified).

## 1. Current state (analysis of 2026-09-27)

The tools are correct on a real project (_theCELL: map, changes, outline, deps, refs,
schema, grep, read, edit, write all accurate, 0.2–0.6 s per call); tests pass on Python
3.10, 3.13 and 3.14; `.gitignore` matching agrees with git on negation, `**`, anchored
and directory patterns. The problems are in `.tools/core/`:

**Over-reach.** `substrate.py` (1,845 lines) does seven jobs: walking the folder,
classifying the project, content-addressed evidence, derived claims, a provenance graph,
pruning, and read APIs. Its only real consumer is `changes`, which needs path, kind,
size, mtime and hash from the last snapshot. The claims and graph are reachable only
through 16 `substrate.*`/`awareness.*` ops that no receipt shows in use; the project
classification duplicates `map`, less well.

**Two write paths.** `edit`/`write` write directly; `mutation.preview → approve → apply`
(`mutation.py` 771 lines, depending on `awareness.py` 454 lines) is a second path that
`WORKFLOW.md` names as the way to make user-approved writes. It refuses until
`changes mark=true` (discarding the pending change report), refuses again if any file in
the project changed since, and can only create files, not edit them. This contradicts
DESIGN-PRINCIPLES §3 and §7 and Design Test questions 9–10.

**Frailties.**
- Receipt artifacts are unbounded: every call's full result is stored, never pruned.
- `_describe_resource` calls `os.lstat` unguarded: a file that disappears mid-walk
  (editor temp files) crashes `changes`/`mark` (from code reading; not reproduced).
- `.mcp.json` launches `python`, absent on many macOS/Linux machines.
- The MCP instructions say `run op=help`; that form fails on the CLI (`help` works).

**Small bugs.** `read` shows non-UTF-8 files with U+FFFD and no notice (`edit` refuses
them safely). `refs` reports a decorated definition at its decorator line. `map` counts
the skeleton's own files as project content. Op results (e.g. mutation preview) print as
raw JSON while tool results are compact text.

**Staleness.** `"...not analyzed by T7"` (another project's tranche); a dead
`USEFUL_HELPERS_IDENTITY_` env filter; compatibility branches for old row and snapshot
formats that no fresh copy can have; an 8-step migration chain; `_AUTHORITY_ORDER`
defined twice; `VERIFIED_PYTHON` lists 3.10–3.13 though 3.14 also passes.

**Structure.** The root `PROJECT.md`/`PLAN.md` are shipped templates, so the skeleton had
no place for its own plan (resolved in T0 by `.dev/`, excluded from `pack`).

## 2. Decisions (user, 2026-09-27)

1. Remove the governed-mutation workflow. Approval is per tranche (WORKFLOW cycle step
   6); `edit`/`write` under `apply` authority is the one write path; receipts record every
   write.
2. Drop per-file version history; git covers it.
3. The skeleton's own PROJECT/PLAN live in `.dev/`, which `pack` leaves out.
4. Receipt retention: keep receipt summaries forever; keep full artifacts of writes
   (apply/sandbox tools, including `ollama`); keep the last 200 full artifacts of
   observe-only calls. Journal entries are never pruned.

## 3. Target end state

A smaller toolset with one responsibility per core module: a snapshot store for
`changes`, receipts with retention, the journal, contracts and the control plane, and the
entrances (CLI and MCP). One write path. Docs, help text and MCP instructions name only
operations that exist. Behaviour the tools have today is kept, with the bugs above fixed
under regression tests.

## 4. Stop conditions

Development ends when all of these hold:

- **S1 Size:** no core module over ~500 lines; `.tools` code (tests excluded) at least
  ~30% smaller than the T0 baseline; no `substrate.*`, `awareness.*` or `mutation.*` ops.
- **S2 One write path, consistent docs:** only `edit`/`write` change the target;
  `WORKFLOW.md`, `AGENT-START-HERE.md`, both READMEs and the MCP instructions agree; a
  test checks that every op named in those documents exists.
- **S3 `changes` unchanged:** all existing change-tracking tests pass; at 20,000 files,
  `changes`/`mark` time and state size are no worse than the T0 baseline.
- **S4 Bounded storage:** a test shows state size stops growing under repeated large
  reads; journal entries are never pruned.
- **S5 Bugs fixed with tests:** lstat race, `refs` line, `read` encoding notice, CLI
  `run op=help`, `.mcp.json` portability, `VERIFIED_PYTHON` set from measured runs.
- **S6 No stale code:** no `T7`, `USEFUL_HELPERS`, or old-format compatibility code; one
  squashed schema; an existing copy's journal and receipts carry across the upgrade.
- **S7 Field test:** a scripted end-to-end run on a fresh copy of _theCELL passes, with
  timings recorded; the suite passes on Python 3.10 and 3.14.
- **S8 Plan has a home:** `.dev/` exists and `pack` excludes it. *(Met in T0.)*

## 5. Tranches

| # | Tranche | Meets |
|---|---|---|
| T0 | `.dev/` home (pack excludes it), this plan, baseline measurements (`bench.py`) | S8, S3 baseline |
| T1 | Low-risk fixes and stale-code removal; docs-vs-ops test | S5, S6 (partly), S2 test |
| T2 | Receipt retention | S4 |
| T3 | Remove the mutation workflow; align WORKFLOW and docs | S2 |
| T4 | Replace substrate + awareness with a small snapshot store; squash the schema, carrying the journal and receipts across; re-measure | S1, S3, S6 |
| T5 | Polish: compact op output, `map` ignores skeleton files, docs refresh | S2 |
| T6 | Field test on _theCELL under 3.10 and 3.14; repack; commit and push. End. | S7 |

T1 and T2 are independent of each other. T4 depends on T3 (mutation reads awareness).

## 6. Baseline (T0)

`python .dev/bench.py files=20000`, 2026-09-27, Python 3.13.6, Windows 10:

| measure | T0 |
|---|---|
| `.tools` code, tests excluded | 8,122 lines (substrate 1,845; mutation 771; storage 454; awareness 454) |
| `changes`, no baseline yet | 82.9 s |
| `mark`, first | 14.8 s |
| `changes`, idle | 2.2 s |
| `changes`, 1% of files edited | 3.2 s |
| `mark`, second | 9.7 s |
| state after two marks | 51.9 MB (2.54 KB/file) |
| state growth, 5 reads of a 315 KB file | 120 KB |

Finding for T1: `changes` with no baseline only reports a file count, yet it hashes every
file. Part of the 82.9 s is probably a cold cache or antivirus scanning 20,000
freshly written files (the `mark` right after it took 14.8 s); the split is not measured.

**T1 A/B (2026-09-28, same session, back to back: T0 code, T1 code, T0 code again):**

| measure | T0 | T1 | T0 again |
|---|---|---|---|
| `changes`, no baseline | 156.8 s | 4.2 s | 150.1 s |
| `mark`, first | 26.2 s | 188.3 s | 23.5 s |
| `changes`, idle | 3.9 s | 3.4 s | 3.4 s |
| `changes`, 1% edited | 5.3 s | 4.8 s | 4.9 s |
| `mark`, second | 17.4 s | 15.4 s | 15.2 s |
| state after two marks | 2.54 KB/file | 2.54 KB/file | 2.53 KB/file |

Reading: on this machine the first read of 20,000 freshly written files costs ~150 s
(most likely antivirus), paid by whichever command reads first. T0 paid it in `changes`
and read everything again in `mark`; T1 pays it once, in `mark`, and `changes` answers in
4 s. Repeated-use timings are equal or better. The machine ran slower than on 2026-09-27
(T0 code: idle `changes` 2.2 s then, 3.4–3.9 s now), so **compare tranches only by A/B
runs in one session**, and judge S3 on the idle, 1%-edited and second-`mark` rows.

## 7. Current tranche

**ID:** T2 — **Status:** DECLARED (2026-09-28), awaiting the user's go-ahead

**Expected outcome:** state storage stops growing with ordinary read-only use, per
decision 4, without losing any record the project cites as evidence.

**Scope:**
1. Retention in `core/runtime_records.py`: receipt rows are kept forever. Full artifacts
   are kept for calls to tools whose manifest authority is `sandbox` or `apply` (writes,
   `ollama`), and for any artifact a journal entry links to. For calls to `observe` tools
   (and refusals that never reached a tool), only the newest 200 artifacts are kept.
2. Pruning clears the receipt's `artifact_id` before deleting (foreign keys are
   enforced), runs cheaply as receipts complete, not on every call, and hands pages back
   (incremental vacuum).
3. Reading a pruned artifact through its receipt says it was pruned under retention,
   not "not found".
4. Remove the dead `create_artifact` (no callers).
5. Document the retention rule in `.tools/README.md` and `WORKFLOW.md` ("cite receipt
   ids": link the journal entry to them to keep their full artifacts).

**Explicit non-goals:** pruning receipt rows or journal entries; changing what an
artifact contains (T5); schema changes (T4). If a column turns out to be needed, T2
stops and reports rather than adding it.

**Affected ownership domains:** `core/runtime_records.py`, `core/app_journal.py` (read
only, for links), `core/registry.py` (read only, for manifest authority), docs, tests.

**Acceptance criteria:**
- A test making 300 observe calls leaves at most ~250 observe artifacts, and state size
  stops growing across a second batch of calls.
- Artifacts of `edit`/`write`/`ollama` calls and journal-linked artifacts survive
  pruning (test).
- Every receipt row survives; a pruned receipt reports that its artifact was pruned
  (test).
- Each test fails on T1 code; the full suite passes on 3.10 and 3.14.
- A same-session A/B `bench.py` run shows no regression in the repeated-use rows.

**Verification requirements:** suite on 3.10 and 3.14 (all five if retention touches
anything version-sensitive); A/B `bench.py files=20000`.

**Known risks / unknowns:** a tool removed after its receipts were written has no
manifest; such artifacts are treated as observe-level (pruneable) unless journal-linked.

## 8. Current Decision

**Plan status:** ACTIVE; T1 parked, T2 declared
**Implementation permission:** NO until the user approves T2.

## 9. Parked tranches

**T1 — PARKED 2026-09-28.**
Outcome met, all nine items: vanished paths are absent, not a crash; `changes` with no
baseline counts without reading; `refs` reports decorated definitions at their `def`
line; `read` flags invalid UTF-8; the CLI accepts `run op=<name>`; unpack repoints
`.mcp.json` when `python` is missing; `VERIFIED_PYTHON` = 3.10–3.14, measured; the dead
env filter is gone and the authority order lives once in `constants.py`; `DocsTests`
checks every op the docs and MCP instructions name. Also: a shared in-process
`Target.core()` test helper; MCP instructions became a module constant (`INSTRUCTIONS`).
Evidence: 46 tests pass on Python 3.10, 3.11, 3.12, 3.13 and 3.14; all six regression
tests fail on T0 code (git worktree run); A/B bench in §6. Limitations: item 6 is tested
by simulating a missing `python`, not on a real macOS/Linux machine; the docs check
errors on T0 code only because `INSTRUCTIONS` did not exist (it is a guard, and its
self-test proves detection); the lstat fix and no-baseline shortcut live in
`substrate.py` and must carry into T4's snapshot store. Docs: `.tools/README.md` (CLI
`op=` form, `read` UTF-8 notice, `changes` before the first snapshot, `.mcp.json` on
unpack) and `README.md` (python3 on unpack).

**T0 — PARKED 2026-09-27, committed 2026-09-28.**
Outcome met: `.dev/` holds PROJECT/PLAN/bench and `pack` excludes it (tested; `pack`
also reports every exclusion); the analysis, decisions, stop conditions and tranches are
recorded here; baseline in §6; READMEs and `AGENTS.md` document `.dev/` (including
"delete `.dev/` after cloning to start a project"). Evidence: full suite passes (Python
3.13); `bench.py` runs; journal entries 2–4. Limitations: `.dev/PROJECT.md` was PROPOSED
at parking (confirmed DEFINED 2026-09-28); the S1 size target is ~30% (not the ~40% first stated)
because the corrected baseline is 8,122 lines, not ~6,100.
