from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ToolGuardrailDecision:
    allowed: bool
    reason: str | None = None
    failure_code: str | None = None


WORKSPACE_OBSERVING_TOOLS = {"search_files", "read_file", "patch", "write_file"}


class ToolLoopGuardrail:
    def __init__(self, max_failed_repeats: int = 2, max_completed_repeats: int = 2) -> None:
        self.max_failed_repeats = max(1, max_failed_repeats)
        self.max_completed_repeats = max(1, max_completed_repeats)
        self._failed_signatures: dict[str, int] = {}
        self._completed_signatures: dict[str, int] = {}
        self._semantic_failed_signatures: dict[str, int] = {}
        self._semantic_completed_signatures: dict[str, int] = {}
        self._semantic_reasons: dict[str, str] = {}
        self._refreshed_file_paths: dict[str, int | None] = {}

    def before_call(
        self,
        tool_name: str,
        args: dict[str, Any],
        *,
        workspace_epoch: int | None = None,
        file_resume_policies: tuple[dict[str, Any], ...] = (),
    ) -> ToolGuardrailDecision:
        resume_policy_decision = self._file_resume_policy_decision(
            tool_name,
            args,
            file_resume_policies=file_resume_policies,
            workspace_epoch=workspace_epoch,
        )
        if resume_policy_decision is not None:
            return resume_policy_decision

        semantic_args_signature = self._semantic_args_signature(tool_name, args, workspace_epoch=workspace_epoch)
        semantic_failed_count = self._semantic_failed_signatures.get(semantic_args_signature, 0)
        if semantic_failed_count >= self.max_failed_repeats:
            return ToolGuardrailDecision(
                allowed=False,
                reason=self._semantic_reasons.get(
                    semantic_args_signature,
                    f"Blocked repeated failing tool call with no progress: {tool_name}",
                ),
                failure_code="guardrail_blocked",
            )

        signature = self._call_signature(tool_name, args, workspace_epoch=workspace_epoch)
        failed_count = self._failed_signatures.get(signature, 0)
        if failed_count >= self.max_failed_repeats:
            return ToolGuardrailDecision(
                allowed=False,
                reason=f"Blocked repeated failing tool call: {tool_name}",
                failure_code="guardrail_blocked",
            )

        semantic_completed_count = self._semantic_completed_signatures.get(semantic_args_signature, 0)
        if semantic_completed_count >= self.max_completed_repeats:
            return ToolGuardrailDecision(
                allowed=False,
                reason=self._semantic_reasons.get(
                    semantic_args_signature,
                    f"Blocked repeated no-progress tool call: {tool_name}",
                ),
                failure_code="no_progress_loop",
            )

        completed_count = self._completed_signatures.get(signature, 0)
        if completed_count >= self.max_completed_repeats:
            return ToolGuardrailDecision(
                allowed=False,
                reason=f"Blocked no-progress repeated tool call: {tool_name}",
                failure_code="no_progress_loop",
            )

        return ToolGuardrailDecision(allowed=True)

    def after_call(
        self,
        tool_name: str,
        args: dict[str, Any],
        result: dict[str, Any],
        ok: bool,
        *,
        workspace_epoch: int | None = None,
    ) -> None:
        signature = self._call_signature(tool_name, args, workspace_epoch=workspace_epoch)
        self._completed_signatures[signature] = self._completed_signatures.get(signature, 0) + 1
        self._record_file_refresh(tool_name, args, result, ok, workspace_epoch=workspace_epoch)
        semantic_observation = self._semantic_observation(tool_name, args, result, ok, workspace_epoch=workspace_epoch)
        if semantic_observation:
            kind, semantic_signature, reason = semantic_observation
            self._semantic_reasons[semantic_signature] = reason
            if kind == "failed":
                self._semantic_failed_signatures[semantic_signature] = (
                    self._semantic_failed_signatures.get(semantic_signature, 0) + 1
                )
            else:
                self._semantic_completed_signatures[semantic_signature] = (
                    self._semantic_completed_signatures.get(semantic_signature, 0) + 1
                )

        if ok and "error" not in result:
            return

        self._failed_signatures[signature] = self._failed_signatures.get(signature, 0) + 1

    def _file_resume_policy_decision(
        self,
        tool_name: str,
        args: dict[str, Any],
        *,
        file_resume_policies: tuple[dict[str, Any], ...],
        workspace_epoch: int | None = None,
    ) -> ToolGuardrailDecision | None:
        if tool_name not in {"patch", "write_file"}:
            return None
        requested_path = self._arg_path(args)
        if not requested_path:
            return None
        for policy in file_resume_policies:
            if not isinstance(policy, dict):
                continue
            policy_paths = self._policy_paths(policy)
            if requested_path not in policy_paths:
                continue
            action = str(policy.get("action") or "").strip()
            source_tool_name = str(policy.get("sourceToolName") or "").strip()
            if action == "skip_redundant_mutation" and (not source_tool_name or source_tool_name == tool_name):
                return ToolGuardrailDecision(
                    allowed=False,
                    reason=(
                        "Blocked worker file mutation because a saved artifact verifies that "
                        f"{requested_path} already matches the previous {tool_name} result. "
                        "Use the saved artifact or perform only a different follow-up step."
                    ),
                    failure_code="file_resume_policy_blocked",
                )
            if action == "reinspect_before_mutation" and not self._path_was_refreshed(
                requested_path,
                workspace_epoch=workspace_epoch,
            ):
                return ToolGuardrailDecision(
                    allowed=False,
                    reason=(
                        "Blocked worker file mutation because the saved file artifact no longer matches "
                        f"current workspace state for {requested_path}. Read the file first, then apply only "
                        "the missing intended change."
                    ),
                    failure_code="file_resume_policy_reinspect_required",
                )
        return None

    def _record_file_refresh(
        self,
        tool_name: str,
        args: dict[str, Any],
        result: dict[str, Any],
        ok: bool,
        *,
        workspace_epoch: int | None,
    ) -> None:
        if not ok or tool_name != "read_file":
            return
        path = self._arg_path(args) or self._result_path(result)
        if path:
            self._refreshed_file_paths[path] = workspace_epoch

    def _path_was_refreshed(self, path: str, *, workspace_epoch: int | None) -> bool:
        if path not in self._refreshed_file_paths:
            return False
        refreshed_epoch = self._refreshed_file_paths[path]
        return workspace_epoch is None or refreshed_epoch is None or refreshed_epoch == workspace_epoch

    @staticmethod
    def _arg_path(args: dict[str, Any]) -> str | None:
        path = args.get("path")
        if not isinstance(path, str):
            return None
        normalized = path.strip().replace("\\", "/")
        while normalized.startswith("./"):
            normalized = normalized[2:]
        return normalized or None

    @staticmethod
    def _result_path(result: dict[str, Any]) -> str | None:
        path = result.get("path")
        if not isinstance(path, str):
            return None
        normalized = path.strip().replace("\\", "/")
        while normalized.startswith("./"):
            normalized = normalized[2:]
        return normalized or None

    @staticmethod
    def _policy_paths(policy: dict[str, Any]) -> set[str]:
        value = policy.get("paths")
        if not isinstance(value, list):
            return set()
        paths: set[str] = set()
        for item in value:
            if not isinstance(item, str):
                continue
            normalized = item.strip().replace("\\", "/")
            while normalized.startswith("./"):
                normalized = normalized[2:]
            if normalized:
                paths.add(normalized)
        return paths

    def _semantic_observation(
        self,
        tool_name: str,
        args: dict[str, Any],
        result: dict[str, Any],
        ok: bool,
        *,
        workspace_epoch: int | None = None,
    ) -> tuple[str, str, str] | None:
        semantic_args_signature = self._semantic_args_signature(tool_name, args, workspace_epoch=workspace_epoch)

        if tool_name == "search_files":
            results = result.get("results")
            if ok and isinstance(results, list):
                if not results:
                    return (
                        "completed",
                        semantic_args_signature,
                        "Blocked repeated empty file search; change the query, target, or stop searching.",
                    )
                return (
                    "completed",
                    semantic_args_signature,
                    "Blocked repeated file search returning the same results; inspect a result or change the query.",
                )

        if tool_name == "search_memory":
            results = result.get("results")
            if ok and isinstance(results, list):
                if not results:
                    return (
                        "completed",
                        semantic_args_signature,
                        "Blocked repeated empty memory search; change the query or answer from available context.",
                    )
                return (
                    "completed",
                    semantic_args_signature,
                    "Blocked repeated memory search returning the same snippets; use the recalled context or change the query.",
                )

        if tool_name == "search_memory_items":
            items = result.get("items")
            if ok and isinstance(items, list):
                if not items:
                    return (
                        "completed",
                        semantic_args_signature,
                        "Blocked repeated empty structured memory search; change the scope/query or answer from available context.",
                    )
                return (
                    "completed",
                    semantic_args_signature,
                    "Blocked repeated structured memory search returning the same facts; use the recalled facts or change the query.",
                )

        if tool_name == "memory_add" and ok and result.get("duplicate") is True:
            return (
                "completed",
                semantic_args_signature,
                "Blocked repeated duplicate structured memory write; the fact is already remembered.",
            )

        if tool_name in {"memory_replace", "memory_forget"} and (not ok or "error" in result):
            return (
                "failed",
                semantic_args_signature,
                f"Blocked repeated structured memory mutation failure for {tool_name}; search memory items before retrying.",
            )

        if tool_name == "read_file" and ok:
            path = self._string_arg(args, "path")
            start_line = self._intish_arg(args, "startLine", default=1)
            line_limit = self._intish_arg(args, "lineLimit", default=None)
            max_chars = self._intish_arg(args, "maxChars", default=None)
            return (
                "completed",
                self._signature(tool_name, {
                    "path": path,
                    "startLine": start_line,
                    "lineLimit": line_limit,
                    "maxChars": max_chars,
                    "workspaceEpoch": workspace_epoch,
                }),
                "Blocked repeated read_file window; use a different line window or summarize the current content.",
            )

        if tool_name == "patch" and (not ok or "error" in result):
            return (
                "failed",
                self._signature(tool_name, {
                    "path": self._string_arg(args, "path"),
                    "oldText": self._string_arg(args, "oldText"),
                    "workspaceEpoch": workspace_epoch,
                }),
                "Blocked repeated patch failure for the same file/text; call read_file to verify current contents before patching again.",
            )

        if tool_name == "write_file" and (not ok or "error" in result):
            return (
                "failed",
                self._signature(tool_name, {
                    "path": self._string_arg(args, "path"),
                    "overwrite": bool(args.get("overwrite", False)),
                    "workspaceEpoch": workspace_epoch,
                }),
                "Blocked repeated write_file failure for the same path; choose a new path or explicitly set overwrite when intended.",
            )

        return None

    @classmethod
    def _semantic_args_signature(
        cls,
        tool_name: str,
        args: dict[str, Any],
        *,
        workspace_epoch: int | None = None,
    ) -> str:
        if tool_name == "search_files":
            return cls._signature(tool_name, {
                "query": cls._string_arg(args, "query"),
                "target": cls._string_arg(args, "target", default="all"),
                "root": cls._string_arg(args, "root", default="."),
                "workspaceEpoch": workspace_epoch,
            })

        if tool_name == "search_memory":
            return cls._signature(tool_name, {
                "query": cls._string_arg(args, "query"),
                "sessionId": cls._string_arg(args, "sessionId", default=None),
                "includeAllSessions": bool(args.get("includeAllSessions", False)),
            })

        if tool_name == "search_memory_items":
            return cls._signature(tool_name, {
                "scope": cls._string_arg(args, "scope", default=None),
                "query": cls._string_arg(args, "query", default=None),
            })

        if tool_name == "memory_add":
            return cls._signature(tool_name, {
                "scope": cls._string_arg(args, "scope"),
                "content": cls._string_arg(args, "content"),
            })

        if tool_name in {"memory_replace", "memory_forget"}:
            return cls._signature(tool_name, {
                "memoryItemId": cls._intish_arg(args, "memoryItemId", default=0),
            })

        if tool_name == "read_file":
            return cls._signature(tool_name, {
                "path": cls._string_arg(args, "path"),
                "startLine": cls._intish_arg(args, "startLine", default=1),
                "lineLimit": cls._intish_arg(args, "lineLimit", default=None),
                "maxChars": cls._intish_arg(args, "maxChars", default=None),
                "workspaceEpoch": workspace_epoch,
            })

        if tool_name == "patch":
            return cls._signature(tool_name, {
                "path": cls._string_arg(args, "path"),
                "oldText": cls._string_arg(args, "oldText"),
                "workspaceEpoch": workspace_epoch,
            })

        if tool_name == "write_file":
            return cls._signature(tool_name, {
                "path": cls._string_arg(args, "path"),
                "overwrite": bool(args.get("overwrite", False)),
                "workspaceEpoch": workspace_epoch,
            })

        return cls._signature(tool_name, args)

    @classmethod
    def _call_signature(cls, tool_name: str, args: dict[str, Any], *, workspace_epoch: int | None = None) -> str:
        if tool_name not in WORKSPACE_OBSERVING_TOOLS:
            return cls._signature(tool_name, args)

        signature_args = dict(args)
        signature_args["workspaceEpoch"] = workspace_epoch
        return cls._signature(tool_name, signature_args)

    @staticmethod
    def _string_arg(args: dict[str, Any], key: str, default: str | None = "") -> str | None:
        value = args.get(key, default)
        if value is None:
            return None
        return str(value)

    @staticmethod
    def _intish_arg(args: dict[str, Any], key: str, default: int | None) -> int | None:
        value = args.get(key, default)
        try:
            return None if value is None else int(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _signature(tool_name: str, args: dict[str, Any]) -> str:
        return f"{tool_name}:{ToolLoopGuardrail._json_dumps(args)}"

    @staticmethod
    def _json_dumps(value: Any) -> str:
        try:
            return json.dumps(value, ensure_ascii=False, sort_keys=True)
        except TypeError:
            return json.dumps(str(value), ensure_ascii=False)
