"""Compact, agent-facing renderings of control-plane responses.

The full governed envelope (receipt, artifact, digests, timings) is always persisted by the
control plane; renderings only decide what an entrance shows. Nothing here may drop an
error or a disclosed limitation.
"""
from __future__ import annotations

import json


def compact(document: object) -> str:
    return json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _error_line(response: dict) -> str:
    error = response.get("error")
    result = response.get("result") if isinstance(response.get("result"), dict) else {}
    if isinstance(error, dict):
        code = error.get("code") or "error"
        message = error.get("message") or result.get("error") or ""
    else:
        code = "error"
        message = str(error or result.get("error") or "operation failed")
    if code in {"tool_process_failed", "tool_result_not_ok", "error"}:
        return f"error: {message}"
    return f"error[{code}]: {message}"


def invocation(response: dict) -> str:
    """Render a ControlPlane.invoke response."""
    result = response.get("result")
    if not response.get("ok"):
        if isinstance(result, dict) and result.get("text") and not result.get("error"):
            return "error: " + str(result["text"])
        return _error_line(response)
    if isinstance(result, dict):
        if isinstance(result.get("text"), str):
            return result["text"]
        return compact({k: v for k, v in result.items() if k not in {"ok", "tool"}})
    return compact(response)


def _awareness(revision: dict) -> str:
    basis = revision.get("basis") or {}
    lines = [
        f"awareness {revision.get('awareness_id')} "
        f"(freshness={revision.get('freshness')}, basis={basis.get('status')})"
    ]
    summary = revision.get("summary")
    if summary:
        lines.append("summary: " + (summary if isinstance(summary, str) else compact(summary)))
    for finding in revision.get("findings") or []:
        lines.append(
            f"- {finding.get('title')}: {finding.get('statement')} [{finding.get('item_id')}]"
        )
    for label in ("unknowns", "limitations"):
        values = revision.get(label) or []
        if values:
            lines.append(f"{label}: " + (compact(values) if not all(isinstance(v, str) for v in values)
                                         else "; ".join(values)))
    return "\n".join(lines)


def projection(operation: str, response: dict) -> str:
    """Render a non-tool operation (status, receipts, journal, substrate, awareness)."""
    if not isinstance(response, dict):
        return compact(response)
    if response.get("ok") is False:
        return _error_line(response)
    if isinstance(response.get("text"), str):
        return response["text"]
    if operation in {"awareness.current", "awareness.refresh"} and isinstance(response.get("revision"), dict):
        return _awareness(response["revision"])
    body = {k: v for k, v in response.items() if k != "ok"}
    if len(body) == 1:
        (only,) = body.values()
        return compact(only)
    return compact(body)
