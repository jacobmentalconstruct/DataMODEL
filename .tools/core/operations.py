"""One table of named operations shared by every entrance (CLI `run`, MCP `run`).

Installed tools (manifests under tools/) are invoked through the ControlPlane; the
operations here project the host's own services. Each declares the minimum authority an
entrance must hold to use it:

  observe  read anything, create previews, refresh observations
  sandbox  + write instrument state (journal)
  apply    + change the target (edit/write tools, mutation approve/apply)
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from . import app_journal, awareness, host, mutation, registry, runtime_records, substrate
from .render import compact
from .control import ControlPlane
from .instance import InstanceContext

AUTHORITY_ORDER = {"observe": 0, "sandbox": 1, "apply": 2}


class OperationError(ValueError):
    pass


@dataclass(frozen=True)
class Operation:
    name: str
    authority: str
    signature: str  # compact argument summary: `name`, `name?` for optional
    summary: str
    call: Callable[[InstanceContext, dict], dict]
    schema: dict | None = None  # when set, MCP may expose the op directly and CLI types args


def _req(args: dict, name: str) -> str:
    value = args.get(name)
    if not isinstance(value, str) or not value:
        raise OperationError(f"{name} is required")
    return value


def _text(args: dict, name: str) -> str:
    """A required string that may legitimately be empty (file content)."""
    value = args.get(name)
    if not isinstance(value, str):
        raise OperationError(f"{name} is required")
    return value


def _changes(context: InstanceContext, args: dict) -> dict:
    report = substrate.changes(context, _limit(args, 200), respect_gitignore=bool(args.get("gitignore")))
    if args.get("mark"):
        substrate.refresh(context)
        awareness.refresh(context)
        report["text"] += "\n# snapshot updated; next call reports only newer changes"
    elif report["baseline"] is not None and (
        report["added"] or report["removed"] or report["modified"] or report["uncertain"]
    ):
        report["text"] += "\n# mark=true to acknowledge these"
    return report


def _limit(args: dict, default: int) -> int:
    value = args.get("limit", default)
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise OperationError("limit must be a positive integer")
    return value


_OPERATIONS = [
    Operation("status", "observe", "", "instance status",
              lambda c, a: host.status(c)),
    Operation("changes", "observe", "mark? gitignore? limit?",
              "Files added/removed/modified since the last snapshot.",
              _changes,
              schema={"type": "object", "properties": {
                  "mark": {"type": "boolean", "description": "re-snapshot after reporting"},
                  "gitignore": {"type": "boolean",
                                "description": "skip .gitignored/vendor paths"},
                  "limit": {"type": "integer", "minimum": 1}},
                  "additionalProperties": False}),
    Operation("receipts.list", "observe", "limit?", "recent operation receipts, newest first",
              lambda c, a: {"ok": True, "receipts": runtime_records.list_receipts(c, _limit(a, 20))}),
    Operation("receipts.read", "observe", "receipt_id", "one receipt",
              lambda c, a: {"ok": True, "receipt": runtime_records.read_receipt(c, _req(a, "receipt_id"))}),
    Operation("artifacts.list", "observe", "limit?", "stored call envelopes, newest first",
              lambda c, a: {"ok": True, "artifacts": runtime_records.list_artifacts(c, _limit(a, 20))}),
    Operation("artifacts.read", "observe", "artifact_id", "full stored envelope of a call",
              lambda c, a: {"ok": True, "artifact": runtime_records.read_artifact(c, _req(a, "artifact_id"))}),
    Operation("journal.list", "observe", "limit?", "journal entries",
              lambda c, a: {"ok": True, "entries": app_journal.list_entries(c, _limit(a, 50))}),
    Operation("journal.read", "observe", "entry_id", "one journal entry",
              lambda c, a: {"ok": True, **app_journal.read_entry(c, _req(a, "entry_id"))}),
    Operation("journal.add", "sandbox", "title body? type? status?",
              "record entry|decision|backlog|status; status open|closed|decided|parked|blocked",
              lambda c, a: {"ok": True, "entry": app_journal.add_entry(
                  c, entry_type=a.get("type", "entry"), status=a.get("status", "open"),
                  title=_req(a, "title"), body=str(a.get("body", "")))}),
    Operation("journal.link", "sandbox", "entry_id target_id", "link an entry to any record id",
              lambda c, a: {"ok": True, "link": app_journal.link_entry(
                  c, _req(a, "entry_id"), _req(a, "target_id"))}),
    Operation("substrate.status", "observe", "", "substrate table counts",
              lambda c, a: substrate.status(c)),
    Operation("substrate.refresh", "observe", "", "re-observe the target",
              lambda c, a: substrate.refresh(c)),
    Operation("substrate.resources", "observe", "limit?", "observed resources",
              lambda c, a: {"ok": True, "resources": substrate.list_resources(c, _limit(a, 100))}),
    Operation("substrate.trace", "observe", "handle", "provenance of a handle",
              lambda c, a: {"ok": True, "trace": substrate.trace(c, _req(a, "handle"))}),
    Operation("substrate.resource", "observe", "handle", "one resource",
              lambda c, a: {"ok": True, "resource": substrate.read_resource(c, _req(a, "handle"))}),
    Operation("substrate.versions", "observe", "handle?", "resource versions",
              lambda c, a: {"ok": True, "versions": substrate.list_versions(c, a.get("handle"))}),
    Operation("substrate.version", "observe", "version_id", "one version",
              lambda c, a: {"ok": True, "version": substrate.read_version(c, _req(a, "version_id"))}),
    Operation("substrate.observations", "observe", "limit?", "observations",
              lambda c, a: {"ok": True, "observations": substrate.list_observations(c, _limit(a, 100))}),
    Operation("substrate.observation", "observe", "observation_id", "one observation",
              lambda c, a: {"ok": True, "observation": substrate.read_observation(c, _req(a, "observation_id"))}),
    Operation("substrate.evidence", "observe", "evidence_id", "one evidence record",
              lambda c, a: {"ok": True, "evidence": substrate.read_evidence(c, _req(a, "evidence_id"))}),
    Operation("substrate.claims", "observe", "limit?", "claims",
              lambda c, a: {"ok": True, "claims": substrate.list_claims(c, _limit(a, 100))}),
    Operation("substrate.claim", "observe", "claim_id", "one claim",
              lambda c, a: {"ok": True, "claim": substrate.read_claim(c, _req(a, "claim_id"))}),
    Operation("substrate.relation", "observe", "relation_id", "one relation",
              lambda c, a: {"ok": True, "relation": substrate.read_relation(c, _req(a, "relation_id"))}),
    Operation("awareness.revisions", "observe", "limit?", "awareness revisions",
              lambda c, a: {"ok": True, "revisions": awareness.list_revisions(c, _limit(a, 50))}),
    Operation("awareness.revision", "observe", "awareness_id", "one awareness revision",
              lambda c, a: {"ok": True, "revision": awareness.read_revision(c, _req(a, "awareness_id"))}),
    Operation("awareness.current", "observe", "", "current awareness revision",
              lambda c, a: awareness.current(c)),
    Operation("awareness.refresh", "observe", "", "rebuild awareness from the substrate",
              lambda c, a: awareness.refresh(c)),
    Operation("awareness.drill", "observe", "item_id", "evidence behind an awareness item",
              lambda c, a: {"ok": True, "drill": awareness.drill(c, _req(a, "item_id"))}),
    Operation("mutation.status", "observe", "", "governed mutation counts",
              lambda c, a: mutation.status(c)),
    Operation("mutation.preview_write", "observe", "path content overwrite?",
              "stage a reviewed write (needs fresh awareness)",
              lambda c, a: mutation.preview_write(
                  c, path=_req(a, "path"), content=_text(a, "content"),
                  overwrite=bool(a.get("overwrite", False)))),
    Operation("mutation.approve", "apply", "preview_id journal_entry_id?", "approve a preview",
              lambda c, a: mutation.approve(c, _req(a, "preview_id"),
                                            journal_entry_id=a.get("journal_entry_id"))),
    Operation("mutation.apply", "apply", "approval_id preview_id?", "apply an approval",
              lambda c, a: mutation.apply(c, _req(a, "approval_id"), preview_id=a.get("preview_id"))),
    Operation("mutation.history", "observe", "limit?", "mutation records",
              lambda c, a: {"ok": True, "mutations": mutation.list_history(c, _limit(a, 50))}),
    Operation("mutation.links", "observe", "source_id", "links for a mutation record",
              lambda c, a: {"ok": True, "links": mutation.links(c, _req(a, "source_id"))}),
]
OPERATIONS: dict[str, Operation] = {op.name: op for op in _OPERATIONS}


def permits(held: str, required: str) -> bool:
    return AUTHORITY_ORDER[held] >= AUTHORITY_ORDER[required]


def _tool_signature(schema: dict) -> str:
    required = set(schema.get("required", []))
    return " ".join(
        name if name in required else name + "?" for name in schema.get("properties", {})
    )


def help_text(context: InstanceContext, held: str, name: str | None = None) -> str:
    """Catalogue of every op, or the full contract of one op, filtered to held authority."""
    tools = registry.discover(context)
    if name:
        if name in tools:
            manifest = tools[name]
            return (f"{name} [{manifest.authority}] {manifest.description}\n"
                    f"input: {compact(manifest.input_schema)}")
        if name in OPERATIONS:
            op = OPERATIONS[name]
            return f"{name} [{op.authority}] {op.summary}\nargs: {op.signature or '(none)'}"
        raise OperationError(f"unknown op: {name}")
    lines = [f"authority held: {held}", "tools:"]
    for tool_id, manifest in tools.items():
        mark = "" if permits(held, manifest.authority) else f"  (needs {manifest.authority})"
        lines.append(f"  {tool_id} {_tool_signature(manifest.input_schema)}{mark}")
    lines.append("ops:")
    for op in OPERATIONS.values():
        mark = "" if permits(held, op.authority) else f"  (needs {op.authority})"
        lines.append(f"  {op.name} {op.signature}".rstrip() + mark)
    return "\n".join(lines)


def execute(
    context: InstanceContext,
    name: str,
    args: dict,
    *,
    held: str,
    client: str,
    timeout_seconds: int = 30,
) -> tuple[str, dict]:
    """Run a tool or op under `held` authority. Returns (kind, response)."""
    if not isinstance(args, dict):
        raise OperationError("args must be an object")
    if name in OPERATIONS:
        op = OPERATIONS[name]
        if not permits(held, op.authority):
            return "op", {"ok": False, "error": {
                "code": "authority_denied",
                "message": f"{name} requires {op.authority} authority; this entrance holds {held}",
            }}
        return "op", op.call(context, args)
    return "tool", ControlPlane(context).invoke(
        name, args, client=client, authority=held, timeout_seconds=timeout_seconds
    )
