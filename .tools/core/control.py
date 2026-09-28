from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass

from . import registry, runtime_records, storage
from .constants import AUTHORITY_ORDER, CONTROL_PLANE_VERSION, TOOL_CONTRACT_VERSION
from .containment import ContainmentError, resolve_declared_paths
from .contracts import ToolManifest, validate_json
from .instance import InstanceContext

@dataclass(frozen=True)
class ControlPlane:
    context: InstanceContext

    def __post_init__(self) -> None:
        storage.bootstrap(self.context)

    def _envelope(self, tool_id: str, client: str, authority: str, **payload: object) -> dict:
        return {
            **payload,
            "tool_id": tool_id,
            "client": client,
            "authority": authority,
            "control_plane": {
                "version": CONTROL_PLANE_VERSION,
                "tool_contract": TOOL_CONTRACT_VERSION,
            },
        }

    def _complete_receipt(
        self,
        receipt_id: str,
        response: dict,
        status: str,
        started: float,
        *,
        error_code: str | None = None,
        result_ok: bool | None = None,
        exit_code: int | None = None,
        manifest_digest: str | None = None,
        process: dict | None = None,
    ) -> dict:
        duration_ms = int(response.get("duration_ms") or (time.monotonic() - started) * 1000)
        response["receipt_id"] = receipt_id
        response["durably_governed"] = True
        try:
            artifact_id = runtime_records.complete_receipt(
                self.context,
                receipt_id,
                status=status,
                envelope=response,
                error_code=error_code,
                result_ok=result_ok,
                exit_code=exit_code,
                duration_ms=duration_ms,
                manifest_digest=manifest_digest,
                process=process,
            )
        except runtime_records.RecordError as exc:
            return self._receipt_failure(response, str(exc), started)
        response["artifact_id"] = artifact_id
        return response

    def _receipt_failure(self, response: dict, message: str, started: float) -> dict:
        return self._envelope(
            str(response.get("tool_id") or "unknown"),
            str(response.get("client") or "unknown"),
            str(response.get("authority") or "unknown"),
            ok=False,
            error={"code": "receipt_persistence_failed", "message": message},
            duration_ms=int((time.monotonic() - started) * 1000),
            durably_governed=False,
        )

    def _mechanical_context(self, manifest: ToolManifest) -> dict:
        domains = set(manifest.reads) | set(manifest.writes)
        excluded_roots = [str(self.context.instance_root)] if "target" in domains else []
        return {
            "target_root": str(self.context.target_root),
            "excluded_roots": excluded_roots,
        }

    def invoke(
        self,
        tool_id: str,
        arguments: dict,
        client: str,
        authority: str = "observe",
        timeout_seconds: int = 30,
    ) -> dict:
        started = time.monotonic()
        client = str(client or "").strip() or "unknown"
        authority = str(authority or "").lower()
        try:
            receipt_id = runtime_records.begin_receipt(
                self.context, tool_id=str(tool_id), client=client, authority=authority or "unknown"
            )
        except runtime_records.RecordError as exc:
            return self._receipt_failure(
                {"tool_id": tool_id, "client": client, "authority": authority}, str(exc), started
            )

        manifest: ToolManifest | None = None

        def stop(status: str, code: str, message: str, *, process: dict | None = None, **extra: object) -> dict:
            """Record and return a refusal (before the tool runs) or failure (after)."""
            digest = manifest.digest if manifest else None
            if digest:
                extra["manifest_digest"] = digest
            if status == "failure":
                extra.setdefault("duration_ms", int((time.monotonic() - started) * 1000))
            response = self._envelope(
                tool_id, client, authority, ok=False, error={"code": code, "message": message}, **extra
            )
            exit_code = extra.get("exit_code")
            return self._complete_receipt(
                receipt_id, response, status, started, error_code=code, result_ok=False,
                exit_code=exit_code if isinstance(exit_code, int) else None,
                manifest_digest=digest, process=process,
            )

        if client == "unknown":
            return stop("refusal", "invalid_client", "client is required")
        if authority not in AUTHORITY_ORDER:
            return stop("refusal", "invalid_authority", f"unknown authority: {authority}")
        if not isinstance(arguments, dict):
            return stop("refusal", "invalid_arguments", "arguments must be an object")
        try:
            manifest = registry.get(self.context, tool_id)
        except registry.RegistryError as exc:
            return stop("refusal", "registry_error", str(exc))
        if AUTHORITY_ORDER[authority] < AUTHORITY_ORDER[manifest.authority]:
            return stop(
                "refusal", "authority_denied",
                f"{tool_id} requires {manifest.authority} authority; caller supplied {authority}",
                required_authority=manifest.authority,
            )
        input_errors = validate_json(arguments, manifest.input_schema)
        if input_errors:
            return stop("refusal", "input_contract", "; ".join(input_errors))
        try:
            resolved_arguments = resolve_declared_paths(self.context, manifest, arguments)
        except ContainmentError as exc:
            return stop("refusal", "containment_refusal", str(exc))

        request = {"args": resolved_arguments, "context": self._mechanical_context(manifest)}
        environment = _child_environment(self.context)
        environment["PYTHONUTF8"] = "1"
        timeout = max(1, min(int(timeout_seconds), 300))
        try:
            process = subprocess.run(
                [sys.executable, str(manifest.entry)],
                input=json.dumps(request),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                cwd=self.context.target_root,
                env=environment,
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return stop("failure", "timeout", f"tool exceeded {timeout} seconds")
        except OSError as exc:
            return stop("failure", "process_error", str(exc))

        duration_ms = int((time.monotonic() - started) * 1000)
        raw = {"stdout": process.stdout, "stderr": process.stderr}
        ran = {"duration_ms": duration_ms, "exit_code": process.returncode}
        try:
            result = json.loads(process.stdout)
        except json.JSONDecodeError as exc:
            return stop("failure", "output_contract", f"tool output is not valid JSON: {exc}", process=raw, **ran)
        if not isinstance(result, dict):
            return stop("failure", "output_contract", "tool output must be a JSON object", process=raw, **ran)
        output_errors = validate_json(result, manifest.output_schema)
        if output_errors:
            return stop("failure", "output_contract", "; ".join(output_errors),
                        process=raw, untrusted_result=result, **ran)
        if process.returncode != 0:
            message = result.get("error") or process.stderr.strip() or f"exit code {process.returncode}"
            return stop("failure", "tool_process_failed", message, process=raw, result=result, **ran)

        response = self._envelope(
            tool_id, client, authority, ok=bool(result.get("ok")), result=result,
            manifest_digest=manifest.digest, **ran,
        )
        return self._complete_receipt(
            receipt_id,
            response,
            "success" if response["ok"] else "failure",
            started,
            error_code=None if response["ok"] else "tool_result_not_ok",
            result_ok=bool(result.get("ok")),
            exit_code=process.returncode,
            manifest_digest=manifest.digest,
            # stdout parsed cleanly and is already held verbatim in response["result"].
            process={"stderr": process.stderr},
        )


def _child_environment(context: InstanceContext) -> dict[str, str]:
    allow = {
        "APPDATA",  # Windows per-user site-packages (pip install --user) is located through it
        "COMSPEC",
        "HOME",
        "LANG",
        "OLLAMA_HOST",
        "PATH",
        "PATHEXT",
        "SYSTEMDRIVE",
        "SYSTEMROOT",
        "TEMP",
        "TMP",
        "TMPDIR",
        "USERPROFILE",
        "WINDIR",
    }
    environment = {
        name: value
        for name, value in os.environ.items()
        if name.upper() in allow
    }
    environment["PYTHONPATH"] = str(context.instance_root)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    return environment
