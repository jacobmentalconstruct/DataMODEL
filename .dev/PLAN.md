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
| T4a | Replace substrate + awareness with a small snapshot store (additive schema step that carries the `changes` baseline over); remove their ops | S1, S3 |
| T4b | Squash the schema: drop dead tables, one create-schema path, reclassify pre-T2 artifacts, convert old rows, delete compatibility code; journal and receipts carry across | S1, S6 |
| T5 | Polish: compact op output, `map` ignores skeleton files, docs refresh | S2 |
| T6 | Field test on _theCELL under 3.10 and 3.14; repack; commit and push. End. | S7 |

T1 and T2 are independent of each other. T4 depends on T3 (mutation read awareness). T4 was
split into T4a and T4b on 2026-09-28 to keep each tranche small (WORKFLOW addendum).

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

**T2 A/B (2026-09-28, same session: T1 code, then T2 code):**

| measure | T1 | T2 |
|---|---|---|
| `.tools` code, tests excluded | 8,175 lines | 8,202 lines |
| `changes`, idle | 3.5 s | 3.3 s |
| `changes`, 1% edited | 5.1 s | 4.9 s |
| `mark`, second | 16.5 s | 14.3 s |
| state after two marks | 2.53 KB/file | 2.53 KB/file |
| state after 300 / 600 observe calls (~8 KB results) | 1,736 / 3,272 KB | 1,496 / 1,540 KB |

The last row comes from an ad hoc run of the same flood against each copy's receipt API:
T1 grows linearly, T2 stays flat. (`bench.py`'s "5 reads" row cannot show retention: 5
calls are far below the 200 kept.)

## 7. Current tranche

**ID:** T4a — **Status:** DECLARED (2026-09-28), awaiting the user's go-ahead

**Expected outcome:** `changes` runs on a small snapshot store instead of the substrate
and awareness machinery, with identical behaviour and a much smaller state; the 16
`substrate.*`/`awareness.*` ops are gone.

**Scope:**
1. New `core/snapshot.py` (target: under ~350 lines) owning the walk and the store:
   - the walk moves over from `substrate.py` with its rules intact: vendor/generated
     subtrees and `.tools/snapshot-ignore` folders recorded as folders only (with the
     software-context rule for `vendor`/`build`/`dist`); PDFs, media and files of 1 MB
     or more by size and mtime only; hash reuse when size and mtime match; access errors
     recorded; T1's vanished-path and no-baseline fixes;
   - the store: one table of snapshot entries (path, kind, size, mtime, hash,
     folder-only flag) plus one row per snapshot (time, limitations); only the latest
     snapshot is kept (decision 2: no per-file history).
2. `changes` keeps its output and flags exactly (`M`/`A`/`D`/`?` lines, untracked footer,
   `gitignore`, `limit`, `mark`); `mark=true` writes a new snapshot.
3. Schema step 9 (additive): create the snapshot tables and copy the latest substrate
   inventory into them, so an existing copy's `changes` baseline survives the upgrade.
   Old tables are left in place for T4b.
4. Delete `core/substrate.py`, `core/awareness.py`, their ops, and `render._awareness`;
   drop `substrate`/`awareness` from the MCP `run` description and the docs.
5. Tests: every existing change-tracking test passes unchanged in meaning; the provenance
   and awareness tests go with their code; T1's regression tests target `snapshot`; a new
   test upgrades a copy that has a baseline and sees `changes` report against it.

**Explicit non-goals:** dropping old tables, squashing migrations, reclassifying pre-T2
artifacts, removing JSON-text compatibility (T4b); output formatting (T5).

**Affected ownership domains:** `core/snapshot.py` (new), `core/substrate.py` and
`core/awareness.py` (deleted), `core/storage.py` (step 9), `core/operations.py`,
`core/render.py`, `core/mcp.py`, docs, tests, `.dev/bench.py` if needed.

**Acceptance criteria:**
- The change-tracking tests (baseline diff, untracked subtrees, same-size edit,
  snapshot-ignore, gitignore flag, unchanged snapshots do not grow) and T1's regression
  tests pass; no `substrate.*`/`awareness.*` op remains; `DocsTests` passes.
- The upgrade test passes: an existing baseline survives step 9.
- Same-session A/B `bench.py files=20000`: idle and 1%-edited `changes` and second
  `mark` no slower than T3; state per file far below 2.5 KB.
- The full suite passes on 3.10 and 3.14; this repository's own copy upgrades and
  `changes` still reports against its last mark.

**Verification requirements:** suite on 3.10 and 3.14 (all five if storage changes touch
sqlite behaviour); A/B bench; upgrade of this repository's own state (backed up first).

**Known risks / unknowns:** the walk's classification rules are subtle (software-context
folders, bulk folders, access errors); moving them verbatim, not rewriting them, is the
mitigation, and the existing tests pin their behaviour.

## 8. Current Decision

**Plan status:** ACTIVE; T3 parked, T4a declared
**Implementation permission:** NO until the user approves T4a.

## 9. Parked tranches

**T3 — PARKED 2026-09-28.**
Outcome met: one write path. `core/mutation.py` and its six `mutation.*` ops are deleted;
`edit`/`write` under `apply` authority are the only way the tools change project files.
`WORKFLOW.md`'s approved-writes row now names the tranche approval and `edit`/`write`,
and its `journal.link` row names what the code accepts (receipts, artifacts);
`.tools/README.md`, the MCP `run` description and the op/render docstrings no longer
mention mutation. `test_governed_mutation_flow` became `test_ops_are_gated_by_authority`
(it kept the only op-level authority check); the MCP help test asserts no `mutation`
entry. Evidence: grep finds `mutation` only in `core/storage.py` migrations (T4b),
DESIGN-PRINCIPLES §3's general wording, and the tests' guards; `help` lists none; 48
tests pass on 3.10 and 3.14; code 8,202 -> 7,415 lines. Limitation: the `mutation_*`
tables remain in existing copies until T4b.

**T2 — PARKED 2026-09-28.**
Outcome met: read-only use no longer grows state. Receipt rows are kept forever; full
artifacts are kept for tools that ran with `sandbox`/`apply` authority and for anything a
journal entry links to (directly or through its receipt); observe results and refusals
keep only the newest 200, pruned in batches of 50 with incremental vacuum; a pruned
receipt says so; `artifacts.read` of a missing id mentions retention; dead
`create_artifact` removed; retention documented in `.tools/README.md` and `WORKFLOW.md`.
Deviation from the declared scope: instead of looking up manifest authority in the
registry at prune time, the control plane classifies each result as it completes and
records it in the artifact's existing `kind` column (`observation` vs `tool_result`), so
`runtime_records` needs no registry dependency and no schema change. Evidence: 48 tests
pass on Python 3.10–3.14; both retention tests fail on T1 code; the flood comparison and
A/B bench in §6. Limitation: artifacts written before T2 all have kind `tool_result`, so
existing copies never prune them; T4's schema squash should reclassify them.

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
