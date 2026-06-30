from __future__ import annotations

import concurrent.futures
from typing import Any

from amadeus.tools.base import ToolSpec, normalize_positive_int
from amadeus.tools.read_file import read_file
from amadeus.tools.search_files import search_files
from amadeus.tools.search_memory import search_memory


MAX_DELEGATE_QUERIES = 3
MAX_DELEGATE_PATHS = 5
MAX_DELEGATE_FINDINGS = 12
MAX_DELEGATE_TASK_CHARS = 1000
MAX_DELEGATE_SUMMARY_CHARS = 2400
MAX_CONCURRENCY = 2


def _normalize_text(value: Any, field_name: str, max_chars: int) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"{field_name} is required")
    if "\x00" in normalized:
        raise ValueError(f"{field_name} must be UTF-8 text")
    return normalized[:max_chars]


def _normalize_string_list(value: Any, *, max_items: int, max_chars: int) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError("queries and paths must be arrays when provided")
    normalized: list[str] = []
    seen: set[str] = set()
    for item in value[:max_items]:
        if not isinstance(item, str):
            continue
        text = item.strip()
        if not text or text in seen:
            continue
        normalized.append(text[:max_chars])
        seen.add(text)
    return normalized


def delegate_task(args: dict[str, Any], context: Any = None) -> dict[str, Any]:
    try:
        task = _normalize_text(args.get("task"), "task", MAX_DELEGATE_TASK_CHARS)
        queries = _normalize_string_list(args.get("queries"), max_items=MAX_DELEGATE_QUERIES, max_chars=160)
        paths = _normalize_string_list(args.get("paths"), max_items=MAX_DELEGATE_PATHS, max_chars=240)
    except ValueError as error:
        return {"error": str(error)}

    if not queries:
        queries = [task[:160]]

    include_memory = bool(args.get("includeMemory")) if isinstance(args.get("includeMemory"), bool) else True
    max_results = normalize_positive_int(args.get("maxResults"), 5, 1, 10)
    findings: list[str] = []
    memory_results: list[dict[str, Any]] = []
    file_results: list[dict[str, Any]] = []
    file_reads: list[dict[str, Any]] = []
    errors: list[str] = []

    def cancelled() -> bool:
        return bool(getattr(context, "is_cancelled", lambda: False)())

    def run_search(query: str) -> dict[str, Any]:
        if cancelled():
            return {"error": "delegate task cancelled"}
        return search_files({"query": query, "target": "all", "maxResults": max_results})

    def run_memory_search(query: str) -> dict[str, Any]:
        if cancelled():
            return {"error": "delegate task cancelled"}
        return search_memory({"query": query, "limit": max_results}, context)

    def run_read(path: str) -> dict[str, Any]:
        if cancelled():
            return {"error": "delegate task cancelled"}
        return read_file({"path": path, "lineLimit": 80, "maxChars": 6000})

    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_CONCURRENCY) as executor:
        futures: list[tuple[str, concurrent.futures.Future[dict[str, Any]]]] = []
        for query in queries:
            futures.append(("file_search", executor.submit(run_search, query)))
            if include_memory:
                futures.append(("memory_search", executor.submit(run_memory_search, query)))
        for path in paths:
            futures.append(("file_read", executor.submit(run_read, path)))

        for kind, future in futures:
            if cancelled():
                errors.append("delegate task cancelled")
                break
            try:
                result = future.result(timeout=20)
            except Exception as error:
                errors.append(str(error))
                continue
            if "error" in result:
                errors.append(str(result["error"]))
                continue
            if kind == "file_search":
                file_results.extend(result.get("results") if isinstance(result.get("results"), list) else [])
            elif kind == "memory_search":
                memory_results.extend(result.get("results") if isinstance(result.get("results"), list) else [])
            elif kind == "file_read":
                file_reads.append(result)

    for item in memory_results[:MAX_DELEGATE_FINDINGS]:
        content = str(item.get("content") or item.get("snippet") or "").strip()
        if content:
            findings.append(f"Memory: {content[:220]}")

    for item in file_results[:MAX_DELEGATE_FINDINGS]:
        path = str(item.get("path") or "").strip()
        preview = str(item.get("preview") or "").strip()
        if path:
            findings.append(f"File match {path}: {preview[:180]}")

    for item in file_reads[:MAX_DELEGATE_PATHS]:
        path = str(item.get("path") or "").strip()
        content = str(item.get("content") or "").strip()
        if path and content:
            findings.append(f"Read {path}: {content[:260]}")

    summary_lines = [
        "Restricted research delegate completed.",
        f"Task: {task}",
    ]
    if findings:
        summary_lines.append("Findings:")
        summary_lines.extend(f"- {finding}" for finding in findings[:MAX_DELEGATE_FINDINGS])
    else:
        summary_lines.append("Findings: no direct matches found in the allowed memory/file sources.")
    if errors:
        summary_lines.append("Notes:")
        summary_lines.extend(f"- {error}" for error in sorted(set(errors))[:4])

    summary = "\n".join(summary_lines)
    if len(summary) > MAX_DELEGATE_SUMMARY_CHARS:
        summary = summary[: MAX_DELEGATE_SUMMARY_CHARS - 16] + "... [truncated]"

    return {
        "task": task,
        "delegateType": "restricted_research",
        "maxDepth": 1,
        "maxConcurrency": MAX_CONCURRENCY,
        "allowedTools": ["search_files", "read_file", "search_memory"],
        "summary": summary,
        "findingCount": len(findings),
        "memoryResultCount": len(memory_results),
        "fileResultCount": len(file_results),
        "fileReadCount": len(file_reads),
        "findings": findings[:MAX_DELEGATE_FINDINGS],
        "errors": sorted(set(errors))[:8],
    }


DELEGATE_TASK_TOOL_SPEC = ToolSpec(
    name="delegate_task",
    display_name="Delegating restricted research task",
    permission="allow",
    enabled=True,
    handler=delegate_task,
    schema={
        "type": "function",
        "function": {
            "name": "delegate_task",
            "description": (
                "Run a restricted research/search subtask with max_depth=1 and max_concurrency=2. "
                "It can search memory, search files, and read explicit file windows, but cannot write files, "
                "run shell commands, call tools recursively, or control Live2D/audio. The parent receives only a concise summary and structured findings."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "task": {
                        "type": "string",
                        "description": "Focused research task for the restricted delegate.",
                    },
                    "queries": {
                        "type": "array",
                        "description": "Optional search queries. Defaults to the task text and is capped at 3.",
                        "items": {"type": "string"},
                    },
                    "paths": {
                        "type": "array",
                        "description": "Optional workspace-relative files to read in bounded windows. Capped at 5.",
                        "items": {"type": "string"},
                    },
                    "includeMemory": {
                        "type": "boolean",
                        "description": "Whether to search current-session memory. Defaults to true.",
                    },
                    "maxResults": {
                        "type": "number",
                        "description": "Maximum results per search query. Defaults to 5 and is capped at 10.",
                    },
                },
                "required": ["task"],
                "additionalProperties": False,
            },
        },
    },
)
