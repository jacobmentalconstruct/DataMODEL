# Project Plan

This document is the project's living plan.

At project creation it is intentionally incomplete.

Do not treat empty sections as permission to infer or invent project decisions. Use them to guide planning with the user.

The initial planning process is:

**Orient → Understand → Propose → Refine → Approve → Establish Path → Declare First Tranche**

Implementation does not begin until the initial plan and first tranche have been approved.

---

## 1. Project Orientation

### Source Material

Read and reconcile:

- `PROJECT.md` — product intent, purpose, boundaries, and completion conditions.
- `.framework/DESIGN-PRINCIPLES.md` — qualities the system should preserve.
- `.framework/ARCHITECTURE.md` — structural ownership and dependency guidance.
- `.framework/WORKFLOW.md` — how planning, implementation, verification, review, and parking are performed.
- Existing project files, if any — actual current implementation state.

### Initial State

The current plan status is recorded once, under **Current Decision** (§9).

**Observed starting condition:**

_To be established from the actual project._

**Existing capabilities:**

_To be established._

**Existing constraints or inherited decisions:**

_To be established._

**Unknowns requiring discussion:**

_To be established._

---

## 2. Product Understanding

Before proposing implementation, establish a shared understanding of the product.

Product intent, capabilities, boundaries, and the completion condition live in `PROJECT.md`. Do not copy them here; update `PROJECT.md` when they change.

Record here only what planning adds:

**Interpretation needed for planning:**  
_How PROJECT.md is being read where it is ambiguous; confirm with the user._

**Planning target:**  
_If the plan aims at an intermediate milestone (e.g. a prototype) rather than PROJECT.md's completion condition, what concrete state would make that milestone meaningfully complete?_

---

## 3. Planning Questions

Resolve only the questions necessary to produce a coherent path.

Questions may include:

- What must exist first?
- What capabilities depend on other capabilities?
- What is the smallest useful product increment?
- Which architectural domains are implied?
- Which interfaces must exist?
- Which uncertainties should be resolved experimentally rather than designed in advance?
- What should deliberately be deferred?
- What would constitute evidence that each major capability works?

Do not manufacture decisions merely to fill this section.

Record only planning questions that materially affect the path.

---

## 4. Proposed Project Shape

### Major Domains

_To be proposed from product requirements and architecture guidance._

### Major Application Surfaces

_To be established._

### Important State or Data Boundaries

_To be established._

### Important Lifecycles

_To be established only where meaningful state transitions actually exist._

### External Dependencies

_To be established._

This section describes enough architecture to plan the work.

It is not a mandate to design the entire mature system before implementation begins.

---

## 5. Planned Path

The project path is a sequence of bounded tranches leading from observed current state toward the defined product outcome.

Future tranches are provisional.

They may change as earlier implementation produces new evidence.

### T0 — _Unassigned_

**Outcome:**  
_To be defined._

**Why first:**  
_To be defined._

**Depends on:**  
_To be defined._

**Produces:**  
_To be defined._

---

### T1 — _Unassigned_

**Outcome:**  
_To be defined._

**Why now:**  
_To be defined._

**Depends on:**  
_To be defined._

**Produces:**  
_To be defined._

---

### T2+ — Provisional

Add additional tranches only when they help explain the expected project path.

Far-future tranches should remain less detailed than near-term work.

Do not fully design future implementation before earlier dependencies have been tested.

---

## 6. Current Tranche

No tranche is active until explicitly declared.

### Tranche

**ID:** NONE

**Status:** NOT DECLARED

**Expected outcome:**  
_Not yet defined._

**Scope:**  
_Not yet defined._

**Explicit non-goals:**  
_Not yet defined._

**Affected ownership domains:**  
_Not yet defined._

**Acceptance criteria:**  
_Not yet defined._

**Verification requirements:**  
_Not yet defined._

**Known risks / unknowns:**  
_Not yet defined._

---

## 7. Planning Status

Use one of the following states:

**UNPLANNED**  
The project has not yet been sufficiently understood to propose a path.

**PLANNING**  
The project is being explored and the path is being developed with the user.

**PROPOSED**  
A coherent project path exists but has not yet been approved.

**APPROVED**  
The user has approved the current project path.

**ACTIVE**  
An approved tranche is being executed.

**REORIENTING**  
A tranche has been parked and the future path is being reconsidered against the new project state.

---

## 8. Initial Planning Gate

Before implementation begins, confirm that:

- the project has been inspected rather than assumed;
- product intent is understood;
- major uncertainties have been surfaced;
- the intended completion condition is clear enough to plan toward;
- the project has a plausible dependency-ordered path;
- the first tranche is small and concrete;
- its scope and non-goals are explicit;
- its acceptance criteria can be verified;
- the user has approved proceeding.

If these conditions are not met, remain in planning.

---

## 9. Current Decision

**Plan status:** UNPLANNED

**Next action:**  
Orient from the project documents and actual project state, then work with the user to construct the initial project plan.

**Implementation permission:** NO
