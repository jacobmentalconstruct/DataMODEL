# Workflow

This is the working pattern for all project work: tranches, repair passes, cleanups, and packaging. It exists so a fresh human or agent can pick up the project without having to infer the rhythm from chat history.

## The Cycle

1. Observe the project and reconcile documented state with actual state.
2. Declare the current state.
3. Define the expected and required outcome, scope, and explicit non-goals.
4. Define the task list that maps the current state to that outcome.
5. Alert the user and pause while the tranche is still being planned.
6. If the user gives the go-ahead, implement only the declared tranche.
7. Consolidate the work and remove accidental complexity, temporary scaffolding, and ownership drift.
8. Verify the result against the declared outcome and acceptance criteria.
9. Review for bugs, frailties, stale assumptions, rough edges, inefficient logic, and missed small improvements inside tranche scope.
10. Fix what verification or review found, then reverify.
11. When documentation is approved or already in-scope, update stale docs and record the reason for the update.
12. Park the tranche with verification evidence, limitations, deferrals, and the next provisional step.
13. Reorient against the newly parked state before declaring the next tranche.
14. Move to the next planned step only after the user approves continuing.

## Addendums

- Keep tranches small. Tightly scoped work is easier to test, easier to park, and easier to resume.
- Keep every write boundary explicit: it should always be clear which participant may change what, and changes the user must approve go through an explicit approval step. Agents propose; approved operations act.
- Do not expand feature scope just because the code is warm. New behavior goes into the next tranche unless required to satisfy current acceptance criteria.
- Future tranches are provisional until reached; evidence from completed work may change the planned path.
- Changes of direction, tranche status, verification evidence, public or user-facing instructions, known limitations, deferrals, and stop criteria are recorded in the project record, not only in chat.
- Prefer proof over vibes. If a claim matters, capture a command, artifact, or limitation.
- Keep the README human. It should help someone understand what this is without sounding like a generated product brochure.

## Parking Bar

A tranche is parked only when:

- The expected outcome is met or the limitation is named.
- Scope and non-goals remain intact, or any deviation is recorded.
- Relevant tests/checks have run, or skipped checks explain why.
- Verification evidence supports the tranche's completion claims.
- No background process needed by the tranche is still running.
- Temporary files are removed or classified as runtime artifacts.
- Documentation reflects the resulting state.
- The next step is short, concrete, provisional, and discoverable from docs.

## With `.tools`

When the project contains `.tools/`, these steps have direct instruments:

| Step | Instrument |
|---|---|
| 1 Observe / 13 Reorient | `changes` (what moved since the last parked snapshot); `map` for a project not seen before; then `outline`, `deps`, `schema`, `refs` where they point |
| 2 Declare state, 12 Park | `run journal.add type=status status=parked title=… body=…` (evidence, limitations, deferrals, next step), then `changes mark=true` so reorientation starts from the parked state |
| Project record (decisions, deferrals, backlog) | `journal.add type=decision\|backlog\|entry`; statuses `open closed decided parked blocked`; `journal.link` ties entries to receipts or mutations |
| Proof over vibes | every tool call is receipted (`receipts.list`, `artifacts.read`); cite receipt ids as evidence |
| User-approved writes | `mutation.preview_write` → user approves (`mutation.approve`) → `mutation.apply` |
