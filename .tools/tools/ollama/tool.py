"""Thin, stdlib-only client for a local Ollama server.

The host comes only from OLLAMA_HOST (or the default), never from arguments, so a caller
cannot redirect project content to another machine. The control plane stores the full
result (prompts, options, responses, metrics) as the call's receipt artifact; the text an
agent sees is just the responses plus one stats line per run.
"""
from __future__ import annotations

import json
import math
import os
import urllib.error
import urllib.request
from pathlib import Path

from core.fswalk import human_size
from core.tool_runtime import MechanicalContext, run_tool

_MAX_RUNS = 24


def host() -> str:
    raw = os.environ.get("OLLAMA_HOST", "").strip() or "127.0.0.1:11434"
    if "://" not in raw:
        raw = "http://" + raw
    scheme, rest = raw.split("://", 1)
    hostport = rest.rstrip("/")
    if hostport.startswith("0.0.0.0"):
        hostport = "127.0.0.1" + hostport[len("0.0.0.0"):]
    if ":" not in hostport.rsplit("]", 1)[-1]:
        hostport += ":11434"
    return f"{scheme}://{hostport}"


class OllamaError(RuntimeError):
    pass


def _call(path: str, payload: dict | None, timeout: int) -> dict:
    url = host() + path
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"},
                                     method="GET" if payload is None else "POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        try:
            message = json.loads(body).get("error", body)
        except ValueError:
            message = body
        raise OllamaError(f"{path}: HTTP {exc.code}: {message}") from exc
    except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
        reason = getattr(exc, "reason", exc)
        raise OllamaError(f"Ollama not reachable at {host()} ({reason}); is `ollama serve` running?") from exc


def _seconds(nanoseconds: object) -> float:
    return (nanoseconds or 0) / 1e9


def _stats(model: str, result: dict) -> str:
    tokens = result.get("eval_count") or 0
    rate = tokens / _seconds(result.get("eval_duration")) if result.get("eval_duration") else 0
    parts = [model, f"{result.get('prompt_eval_count', 0)}+{tokens} tok"]
    if rate:
        parts.append(f"{rate:.1f} tok/s")
    parts.append(f"{_seconds(result.get('total_duration')):.1f}s")
    load = _seconds(result.get("load_duration"))
    if load >= 0.5:
        parts[-1] += f" (load {load:.1f}s)"
    if result.get("done_reason") and result["done_reason"] != "stop":
        parts.append(f"stopped: {result['done_reason']}")
    return "# " + " · ".join(parts)


def _read(path: str | None) -> str | None:
    return None if path is None else Path(path).read_text(encoding="utf-8", errors="replace")


def _models_arg(arguments: dict) -> list[str]:
    model = arguments.get("model")
    models = [model] if isinstance(model, str) else list(model or [])
    if not models:
        raise OllamaError("model is required (action=models lists installed ones)")
    return models


def _run(arguments: dict, timeout: int) -> dict:
    models = _models_arg(arguments)
    system = arguments.get("system") or _read(arguments.get("system_file"))
    messages = arguments.get("messages")
    prompts: list[str] = list(arguments.get("prompts") or [])
    if arguments.get("prompt") is not None:
        prompts.insert(0, arguments["prompt"])
    if arguments.get("prompt_file"):
        prompts.append(_read(arguments["prompt_file"]) or "")
    if not prompts and not messages:
        raise OllamaError("prompt, prompts, prompt_file or messages is required")
    jobs = [(model, prompt) for prompt in (prompts or [None]) for model in models]
    if len(jobs) > _MAX_RUNS:
        raise OllamaError(f"{len(jobs)} runs requested; limit is {_MAX_RUNS} per call")

    shared = {key: arguments[key] for key in ("options", "format", "think", "keep_alive") if key in arguments}
    runs: list[dict] = []
    chunks: list[str] = []
    for index, (model, prompt) in enumerate(jobs, start=1):
        if messages:
            chat = ([{"role": "system", "content": system}] if system else []) + list(messages)
            if prompt is not None:
                chat.append({"role": "user", "content": prompt})
            result = _call("/api/chat", {"model": model, "messages": chat, "stream": False, **shared}, timeout)
            message = result.get("message") or {}
            response, thinking = message.get("content", ""), message.get("thinking", "")
        else:
            payload = {"model": model, "prompt": prompt, "stream": False, **shared}
            if system:
                payload["system"] = system
            result = _call("/api/generate", payload, timeout)
            response, thinking = result.get("response", ""), result.get("thinking", "")
        metrics = {key: result.get(key) for key in (
            "total_duration", "load_duration", "prompt_eval_count", "prompt_eval_duration",
            "eval_count", "eval_duration", "done_reason")}
        runs.append({"model": model, "prompt": prompt, "system": system, "messages": messages,
                     "response": response, "thinking": thinking, "metrics": metrics, **shared})
        header = f"## {index}. {model}" + (f" · prompt {prompts.index(prompt) + 1}" if len(prompts) > 1 else "")
        body = []
        if thinking:
            body.append(f"<thinking>\n{thinking.strip()}\n</thinking>" if arguments.get("show_thinking")
                        else f"# (thinking: {len(thinking)} chars hidden; show_thinking=true)")
        body.append(response.strip())
        body.append(_stats(model, result))
        chunks.append(("\n".join([header] + body)) if len(jobs) > 1 else "\n".join(body))
    return {"ok": True, "text": "\n\n".join(chunks), "host": host(), "runs": runs}


def _embed(arguments: dict, timeout: int) -> dict:
    inputs: list[str] = list(arguments.get("prompts") or [])
    if arguments.get("prompt") is not None:
        inputs.insert(0, arguments["prompt"])
    if arguments.get("prompt_file"):
        inputs.append(_read(arguments["prompt_file"]) or "")
    if not inputs:
        raise OllamaError("embed needs prompt or prompts")
    lines: list[str] = []
    stored: dict[str, list] = {}
    for model in _models_arg(arguments):
        vectors = _call("/api/embed", {"model": model, "input": inputs}, timeout).get("embeddings", [])
        stored[model] = vectors
        dims = len(vectors[0]) if vectors else 0
        lines.append(f"{model}: {len(vectors)} inputs x {dims} dims")
        if len(vectors) > 1:
            norms = [math.sqrt(sum(x * x for x in v)) or 1.0 for v in vectors]
            for i, a in enumerate(vectors):
                row = [sum(x * y for x, y in zip(a, b)) / (norms[i] * norms[j]) for j, b in enumerate(vectors)]
                lines.append(f"  {i + 1}: " + " ".join(f"{value:.2f}" for value in row))
    return {"ok": True, "text": "\n".join(lines), "host": host(), "embeddings": stored}


def run(arguments: dict, context: MechanicalContext) -> dict:
    action = arguments.get("action", "run")
    timeout = int(arguments.get("timeout", 280))
    try:
        if action == "run":
            return _run(arguments, timeout)
        if action == "embed":
            return _embed(arguments, timeout)
        if action == "models":
            models = _call("/api/tags", None, timeout).get("models", [])
            lines = [f"{m['name']}  {human_size(m.get('size', 0))}  "
                     f"{m.get('details', {}).get('parameter_size', '?')} {m.get('details', {}).get('quantization_level', '')}".rstrip()
                     for m in sorted(models, key=lambda m: m["name"])]
            return {"ok": True, "text": "\n".join(lines) or "(no models installed)", "host": host(),
                    "models": [m["name"] for m in models]}
        if action == "ps":
            models = _call("/api/ps", None, timeout).get("models", [])
            lines = [f"{m['name']}  vram {human_size(m.get('size_vram', 0))}  until {m.get('expires_at', '?')[:19]}"
                     for m in models]
            return {"ok": True, "text": "\n".join(lines) or "(no models loaded)", "host": host(),
                    "models": [m["name"] for m in models]}
        if action in ("load", "unload"):
            keep = arguments.get("keep_alive", "10m") if action == "load" else 0
            done = []
            for model in _models_arg(arguments):
                result = _call("/api/generate", {"model": model, "keep_alive": keep}, timeout)
                done.append(f"{action}ed {model}" + (f" ({_seconds(result.get('load_duration')):.1f}s)"
                                                      if action == "load" else ""))
            return {"ok": True, "text": "\n".join(done), "host": host()}
        return {"ok": False, "error": f"unknown action: {action}"}
    except OllamaError as exc:
        return {"ok": False, "error": str(exc)}


if __name__ == "__main__":
    raise SystemExit(run_tool(run))
