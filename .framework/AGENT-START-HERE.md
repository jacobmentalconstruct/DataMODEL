# Agents — Start Here

This directory (`.framework/`) contains the standing definitions used to begin and continue projects. It is reusable: the same in every project, and not edited per project.

Do not treat these documents as a project specification by themselves.

Their purpose is to establish how a project should be understood, designed, planned, and developed with the user.

The project's own living documents are at the project root: `PROJECT.md` (what is being built) and `PLAN.md` (the route and current decision).

## Read First

Read, in this order:

1. `PROJECT.md` and `PLAN.md` at the project root — at minimum their **Current Decision** sections.
2. The condensed forms of the standing definitions:
   - `DESIGN-PRINCIPLES.md` — **Design Test** and **Core Principle** (the qualities the system should preserve);
   - `ARCHITECTURE.md` — **Architectural Decision Rule** and **Core Constraint** (the default ownership and structural model);
   - `WORKFLOW.md` — in full; it is short (the development and tranche-working process).
3. The full text of any principle or architecture section that a current decision touches. With `.tools`: `read path=.framework/ARCHITECTURE.md symbol="3. Managers"`.

The condensed sections summarize, and do not replace, the full documents. When in doubt, read the full section.

Then inspect the actual target project.

---

# Determine the Starting Condition

## If the project is empty or has not yet been defined

Do not invent the project.

Begin with the user.

Establish enough product intent to fill `PROJECT.md` with:

- what is being built;
- why it exists;
- primary user capabilities;
- important boundaries and non-goals;
- project-specific constraints;
- a meaningful completion condition.

Do not begin implementation during this process.

Once `PROJECT.md` is coherent:

1. adopt `PLAN.md` for the project;
2. orient the plan from `PROJECT.md`, the standing definitions, and actual project state;
3. identify unknowns with the user;
4. propose the major dependency-ordered path;
5. refine it with the user;
6. define the first bounded tranche;
7. obtain approval;
8. begin implementation only after the planning gate is satisfied.

The project begins in **planning**, not implementation.

---

## If the project already contains work

The work may be code, documents, data, or an unsorted collection of files.

Do not assume the documentation accurately represents current reality.

First inspect:

- the project tree;
- relevant implementation;
- project-specific documentation;
- current `PROJECT.md`;
- current `PLAN.md`;
- current state/history artifacts, if present (with `.tools`: the journal and receipts);
- tests, schemas, data files, interfaces, and other evidence relevant to the active work.

Reconcile documented state with observed state.

Then determine whether the project is:

- unplanned;
- planning;
- awaiting approval;
- active;
- reorienting after a parked tranche;
- or otherwise blocked/inconsistent.

Resume through `PLAN.md` and `WORKFLOW.md`.

Do not silently begin the next tranche.

---

# Instruments

If the project contains `.tools/`, use it for observation and record-keeping; its `README.md` lists every tool. Paths are relative to the project root, and `.tools/` itself is not a project file.

| Question | Tool |
|---|---|
| What is this project? (first contact) | `map` |
| What changed since the last parked state? | `changes` (and `changes mark=true` when parking) |
| What is in these files / this tree? | `outline`, then `read symbol=…` for one definition or section |
| How do modules depend on each other? | `deps` (graph, cycles), `deps module=<file>` |
| Who defines or uses this name? | `refs name=…` |
| What shape is this data? | `schema` |
| What was decided, deferred, or parked? | `run journal.list`, `run journal.read entry_id=…` |
| Record a decision, status change, or parking | `run journal.add type=decision\|status\|backlog status=… title=… body=…` |
| Evidence of what was actually run | `run receipts.list`, `run artifacts.read artifact_id=…` |

Record every change of definition status, plan status, or tranche status as a `journal.add type=status` entry as well as in the documents, so the history is attributable.

---

# Document Roles

`PROJECT.md` (project root)
: Defines this project's product intent, boundaries, and completion condition.

`PLAN.md` (project root)
: Defines the current proposed route through this particular project, and holds the implementation permission. It begins intentionally incomplete and is developed with the user.

`DESIGN-PRINCIPLES.md`
: Defines the qualities the system should preserve, including shared reality, shared application pathways, transparent lifecycles, and human/agent collaboration through the same underlying machine.

`ARCHITECTURE.md`
: Defines the default ownership grammar and dependency direction.

`WORKFLOW.md`
: Defines how work moves from orientation through planning, implementation, verification, review, parking, and reorientation.

Observed project reality
: Determines what actually exists now. When documentation and implementation disagree, expose the disagreement rather than silently choosing one.

---

# Startup Rule

**Orient before proposing.  
Propose before implementing.  
Plan with the user rather than for the user.**

For a new project:

**Definitions → User Intent → PROJECT.md → PLAN.md → Approved First Tranche → Implementation**

For an existing project:

**Definitions → Observe Reality → Reconcile Project State → PLAN.md → Approved Current/Next Tranche → Implementation**

Do not create substantial project structure merely because the target root is empty.

Do not infer missing product decisions from the standing definitions.

Do not continue beyond a planning or tranche boundary without the approval required by `WORKFLOW.md`.

---

# First Response to the User

After initial orientation, explain concisely:

- what you understand the project to be;
- what is known;
- what remains unresolved;
- what you propose to establish next.

If the project is new, begin product definition with the user.

If the project already exists, begin by reporting the reconciled current state.

Do not begin implementation unless the applicable planning gate has already been satisfied.
