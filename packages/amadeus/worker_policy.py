from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any


DEFAULT_WORKER_PROFILE = "planner"
ALLOWED_WORKER_PROFILES = {"researcher", "planner", "coder", "reviewer", "synthesizer"}
KNOWN_TOOLSETS = {"read", "search", "memory", "web", "plan", "task", "skills", "patch", "write", "terminal", "code", "browser", "vision"}
PROFILE_TOOLSET_POLICY: dict[str, set[str]] = {
    "researcher": {"read", "search", "memory", "web"},
    "planner": {"read", "search", "memory", "plan", "task", "skills"},
    "coder": {"read", "search", "memory", "web", "skills", "patch", "write", "terminal", "code", "browser", "vision"},
    "reviewer": {"read", "search", "memory"},
    "synthesizer": {"read", "memory"},
}
DEFAULT_PROFILE_TOOLSETS: dict[str, list[str]] = {
    "researcher": ["read", "search", "memory", "web"],
    "planner": ["read", "search", "memory", "plan"],
    "coder": ["read", "search", "memory", "patch"],
    "reviewer": ["read", "search", "memory"],
    "synthesizer": ["read", "memory"],
}
BASE_WORKER_TOOLS = {"get_current_time", "clarify"}
PROFILE_AUTO_APPROVED_ASK_TOOLS: dict[str, set[str]] = {
    "researcher": {"web_extract"},
    "planner": set(),
    "coder": {"patch"},
    "reviewer": set(),
    "synthesizer": set(),
}
TOOLSET_TOOL_NAMES: dict[str, set[str]] = {
    "read": {"search_files", "read_file", "read_session_messages"},
    "search": {"search_files", "search_memory"},
    "memory": {"search_memory", "search_memory_items", "read_memory"},
    "web": {"web_search", "web_extract"},
    "plan": {"update_plan"},
    "task": {"create_task", "list_tasks", "cancel_task"},
    "skills": {"skills_list", "skill_view"},
    "patch": {"patch"},
    "write": {"write_file"},
    "terminal": {"terminal", "process"},
    "code": {"execute_code"},
    "browser": {
        "browser_navigate",
        "browser_snapshot",
        "browser_click",
        "browser_type",
        "browser_scroll",
        "browser_back",
        "browser_press",
        "browser_get_images",
        "browser_vision",
        "browser_console",
        "browser_cdp",
        "browser_dialog",
    },
    "vision": {"vision_analyze"},
}


@dataclass(frozen=True)
class WorkerRuntimeScope:
    worker_profile: str
    allowed_toolsets: tuple[str, ...]
    allowed_tool_names: frozenset[str]
    workspace_path: str | None = None
    approved_ask_tool_names: frozenset[str] = frozenset()
    approved_ask_tool_actions: frozenset[str] = frozenset()
    approved_ask_tool_action_expirations: tuple[tuple[str, str], ...] = ()
    file_resume_policies: tuple[dict[str, Any], ...] = ()


@dataclass(frozen=True)
class WorkerActionPermissionDecision:
    decision: str
    reason: str | None = None
    action_key: str | None = None
    action_label: str | None = None
    risk_level: str | None = None
    risk_labels: tuple[str, ...] = ()


def build_worker_runtime_scope(task: dict[str, object]) -> WorkerRuntimeScope:
    profile = worker_profile_for_task(task)
    toolsets = tuple(worker_toolsets_for_task(task))
    allowed_tool_names = frozenset(worker_tool_names_for_task(task))
    return WorkerRuntimeScope(
        worker_profile=profile,
        allowed_toolsets=toolsets,
        allowed_tool_names=allowed_tool_names,
        workspace_path=worker_workspace_path_for_task(task),
        approved_ask_tool_names=frozenset(
            name for name in worker_approved_ask_tool_names_for_task(task) if name in allowed_tool_names
        ),
        approved_ask_tool_actions=frozenset(worker_approved_ask_tool_actions_for_task(task)),
        approved_ask_tool_action_expirations=worker_approved_ask_tool_action_expirations_for_task(task),
    )


def worker_file_resume_policies_from_artifacts(artifacts: list[dict[str, object]]) -> tuple[dict[str, Any], ...]:
    policies: list[dict[str, Any]] = []
    for artifact in artifacts:
        metadata = artifact.get("metadata")
        if not isinstance(metadata, dict):
            continue
        policy = metadata.get("fileResumePolicy")
        if not isinstance(policy, dict):
            continue
        normalized = dict(policy)
        source_tool_name = str(metadata.get("toolName") or "").strip()
        if source_tool_name:
            normalized["sourceToolName"] = source_tool_name
        artifact_id = str(artifact.get("id") or "").strip()
        if artifact_id:
            normalized["artifactId"] = artifact_id
        policies.append(normalized)
    return tuple(policies)


def worker_profile_for_task(task: dict[str, object]) -> str:
    profile = str(task.get("workerProfile") or DEFAULT_WORKER_PROFILE).strip().lower()
    return profile if profile in ALLOWED_WORKER_PROFILES else DEFAULT_WORKER_PROFILE


def worker_toolsets_for_task(task: dict[str, object]) -> list[str]:
    profile = worker_profile_for_task(task)
    raw_allowed = task.get("allowedToolsets")
    explicit = _string_list(raw_allowed)
    allowed_by_profile = PROFILE_TOOLSET_POLICY[profile]
    filtered = [toolset for toolset in explicit if toolset in allowed_by_profile]
    return filtered or list(DEFAULT_PROFILE_TOOLSETS[profile])


def worker_tool_names_for_task(task: dict[str, object]) -> set[str]:
    toolsets = worker_toolsets_for_task(task)
    names = set(BASE_WORKER_TOOLS)
    for toolset in toolsets:
        names.update(TOOLSET_TOOL_NAMES.get(toolset, set()))
    names.difference_update(_string_list(task.get("disallowedTools")))
    return names


def worker_workspace_path_for_task(task: dict[str, object]) -> str | None:
    hints = task.get("contextHints")
    if not isinstance(hints, dict):
        return None
    for key in ("workspacePath", "workspace", "cwd"):
        raw_value = hints.get(key)
        value = str(raw_value or "").strip()
        if value:
            return value
    return None


def worker_approved_ask_tool_names_for_task(task: dict[str, object]) -> set[str]:
    checkpoint = task.get("checkpoint")
    if not isinstance(checkpoint, dict):
        return set()
    if str(checkpoint.get("phase") or "") != "approval_resume_requested":
        return set()
    if _raw_string_list(checkpoint.get("approvedToolActions")) or str(checkpoint.get("approvedToolAction") or "").strip():
        return set()
    names = set(_string_list(checkpoint.get("approvedTools")))
    single = str(checkpoint.get("approvedToolName") or "").strip()
    if single:
        names.add(single)
    return names


def worker_approved_ask_tool_actions_for_task(task: dict[str, object]) -> set[str]:
    checkpoint = task.get("checkpoint")
    if not isinstance(checkpoint, dict):
        return set()
    if str(checkpoint.get("phase") or "") != "approval_resume_requested":
        return set()
    actions = set(_raw_string_list(checkpoint.get("approvedToolActions")))
    single = str(checkpoint.get("approvedToolAction") or "").strip()
    if single:
        actions.add(single)
    return actions


def worker_approved_ask_tool_action_expirations_for_task(task: dict[str, object]) -> tuple[tuple[str, str], ...]:
    checkpoint = task.get("checkpoint")
    if not isinstance(checkpoint, dict):
        return ()
    if str(checkpoint.get("phase") or "") != "approval_resume_requested":
        return ()
    expirations: dict[str, str] = {}
    raw_expirations = checkpoint.get("approvedToolActionExpirations")
    if isinstance(raw_expirations, dict):
        for key, value in raw_expirations.items():
            action_key = str(key or "").strip()
            expires_at = str(value or "").strip()
            if action_key and expires_at:
                expirations[action_key] = expires_at
    single_action = str(checkpoint.get("approvedToolAction") or "").strip()
    single_expires_at = str(checkpoint.get("approvedToolActionExpiresAt") or "").strip()
    if single_action and single_expires_at:
        expirations[single_action] = single_expires_at
    return tuple(sorted(expirations.items()))


def worker_permission_decision(scope: WorkerRuntimeScope | None, tool_name: str, permission: str) -> str:
    return worker_action_permission_decision(scope, tool_name, {}, permission).decision


def worker_action_permission_decision(
    scope: WorkerRuntimeScope | None,
    tool_name: str,
    args: dict[str, Any],
    permission: str,
) -> WorkerActionPermissionDecision:
    action = worker_action_policy(tool_name, args)
    if scope is None or permission != "ask":
        return WorkerActionPermissionDecision(
            decision="prompt",
            action_key=action["key"],
            action_label=action["label"],
            risk_level=action["riskLevel"],
            risk_labels=tuple(action["riskLabels"]),
        )
    if action["key"] in scope.approved_ask_tool_actions and tool_name in scope.allowed_tool_names:
        expires_at = _approved_action_expires_at(scope, action["key"])
        if expires_at and _is_expired(expires_at):
            return WorkerActionPermissionDecision(
                decision="deny",
                reason=f"Worker action approval expired for: {action['label']}",
                action_key=action["key"],
                action_label=action["label"],
                risk_level=action["riskLevel"],
                risk_labels=tuple(action["riskLabels"]),
            )
        return WorkerActionPermissionDecision(
            decision="auto_approve",
            reason="Approved action checkpoint matches this worker tool action.",
            action_key=action["key"],
            action_label=action["label"],
            risk_level=action["riskLevel"],
            risk_labels=tuple(action["riskLabels"]),
        )
    if tool_name in scope.approved_ask_tool_names and tool_name in scope.allowed_tool_names:
        return WorkerActionPermissionDecision(
            decision="auto_approve",
            reason="Legacy approved tool checkpoint matches this worker tool.",
            action_key=action["key"],
            action_label=action["label"],
            risk_level=action["riskLevel"],
            risk_labels=tuple(action["riskLabels"]),
        )
    if tool_name in PROFILE_AUTO_APPROVED_ASK_TOOLS.get(scope.worker_profile, set()) and tool_name in scope.allowed_tool_names:
        return WorkerActionPermissionDecision(
            decision="auto_approve",
            reason="Worker profile allows this ask-tool action.",
            action_key=action["key"],
            action_label=action["label"],
            risk_level=action["riskLevel"],
            risk_labels=tuple(action["riskLabels"]),
        )
    return WorkerActionPermissionDecision(
        decision="deny",
        reason=f"Worker action requires approval: {action['label']}",
        action_key=action["key"],
        action_label=action["label"],
        risk_level=action["riskLevel"],
        risk_labels=tuple(action["riskLabels"]),
    )


def worker_action_policy(tool_name: str, args: dict[str, Any]) -> dict[str, Any]:
    if tool_name == "terminal":
        command = str(args.get("command") or "").strip()
        normalized = _normalize_command(command)
        key = f"terminal:command:{hashlib.sha256(normalized.encode('utf-8')).hexdigest()[:16]}"
        risk_labels = _terminal_risk_labels(normalized)
        risk_level = "high" if any(label in risk_labels for label in ("destructive", "installer", "network_script")) else "medium"
        label = f"terminal command `{_truncate_label(normalized or '(empty command)', 120)}`"
        return {"key": key, "label": label, "riskLevel": risk_level, "riskLabels": risk_labels}

    if tool_name == "process":
        action = str(args.get("action") or "list").strip().lower() or "list"
        if action == "signal":
            action = "kill"
        risk_labels = ["destructive", "process_signal"] if action == "kill" else ["local_process_inspection"]
        risk_level = "high" if action == "kill" else "medium"
        pid = args.get("pid")
        pid_label = f" pid {pid}" if isinstance(pid, int) else ""
        return {
            "key": f"process:{action}",
            "label": f"process {action}{pid_label}",
            "riskLevel": risk_level,
            "riskLabels": risk_labels,
        }

    if tool_name in {"patch", "write_file", "read_file", "vision_analyze"}:
        path = str(args.get("path") or "").strip()
        path_key = path.replace("\\", "/") or "*"
        risk = "workspace_mutation" if tool_name in {"patch", "write_file"} else "local_file_exposure"
        risk_level = "medium" if tool_name in {"patch", "write_file"} else "low"
        return {
            "key": f"{tool_name}:path:{path_key}",
            "label": f"{tool_name} {path_key}",
            "riskLevel": risk_level,
            "riskLabels": [risk],
        }

    if tool_name == "web_extract":
        url = str(args.get("url") or "").strip()
        return {
            "key": f"web_extract:url:{url or '*'}",
            "label": f"web page extraction{f' from {url}' if url else ''}",
            "riskLevel": "medium",
            "riskLabels": ["external_content"],
        }

    return {
        "key": f"{tool_name}:run",
        "label": f"{tool_name} action",
        "riskLevel": "medium",
        "riskLabels": ["ask_tool"],
    }


def _normalize_command(command: str) -> str:
    return " ".join(command.strip().split())


def _terminal_risk_labels(command: str) -> list[str]:
    labels: list[str] = ["shell_command"]
    destructive_patterns = [
        r"(^|[;&|]\s*)rm\s+",
        r"(^|[;&|]\s*)rmdir\s+",
        r"(^|[;&|]\s*)git\s+clean\b",
        r"(^|[;&|]\s*)find\b.*\s-delete\b",
    ]
    installer_patterns = [
        r"\b(npm|pnpm|yarn|pip|pip3|uv|brew|cargo|gem)\s+install\b",
        r"\bnpx\b",
    ]
    network_script_patterns = [
        r"\b(curl|wget)\b.*\|\s*(sh|bash|zsh|python|python3)\b",
        r"\b(bash|sh|zsh)\s+<\s*\(",
    ]
    if any(re.search(pattern, command) for pattern in destructive_patterns):
        labels.append("destructive")
    if any(re.search(pattern, command) for pattern in installer_patterns):
        labels.append("installer")
    if any(re.search(pattern, command) for pattern in network_script_patterns):
        labels.append("network_script")
    return labels


def _truncate_label(value: str, max_chars: int) -> str:
    return value if len(value) <= max_chars else value[: max_chars - 3] + "..."


def _approved_action_expires_at(scope: WorkerRuntimeScope, action_key: str) -> str | None:
    for key, expires_at in scope.approved_ask_tool_action_expirations:
        if key == action_key:
            return expires_at
    return None


def _is_expired(expires_at: str) -> bool:
    try:
        parsed = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
    except ValueError:
        return True
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed <= datetime.now(timezone.utc)


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    seen: set[str] = set()
    output: list[str] = []
    for item in value:
        normalized = str(item or "").strip().lower()
        if normalized and normalized not in seen:
            seen.add(normalized)
            output.append(normalized)
    return output


def _raw_string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    seen: set[str] = set()
    output: list[str] = []
    for item in value:
        normalized = str(item or "").strip()
        if normalized and normalized not in seen:
            seen.add(normalized)
            output.append(normalized)
    return output
