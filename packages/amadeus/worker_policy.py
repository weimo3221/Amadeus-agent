from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any


DEFAULT_WORKER_PROFILE = "planner"
ALLOWED_WORKER_PROFILES = {"researcher", "planner", "coder", "reviewer", "synthesizer"}
KNOWN_TOOLSETS = {"read", "search", "memory", "web", "plan", "task", "skills", "patch", "write", "terminal", "code", "browser", "vision"}
WORKER_SANDBOX_MODES = {"read_only", "workspace_write", "workspace_execute"}
PROFILE_DEFAULT_SANDBOX_MODE: dict[str, str] = {
    "researcher": "read_only",
    "planner": "read_only",
    "coder": "workspace_write",
    "reviewer": "read_only",
    "synthesizer": "read_only",
}
WORKSPACE_MUTATION_TOOLS = {"patch", "write_file"}
WORKSPACE_EXECUTION_TOOLS = {"terminal", "process", "execute_code"}
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
    sandbox_mode: str = "workspace_write"
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
    sandbox_mode = worker_sandbox_mode_for_task(task, profile=profile)
    allowed_tool_names = frozenset(worker_tool_names_for_sandbox(worker_tool_names_for_task(task), sandbox_mode))
    return WorkerRuntimeScope(
        worker_profile=profile,
        allowed_toolsets=toolsets,
        allowed_tool_names=allowed_tool_names,
        sandbox_mode=sandbox_mode,
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


def worker_sandbox_mode_for_task(task: dict[str, object], *, profile: str | None = None) -> str:
    effective_profile = profile or worker_profile_for_task(task)
    default_mode = PROFILE_DEFAULT_SANDBOX_MODE.get(effective_profile, "read_only")
    hints = task.get("contextHints")
    if not isinstance(hints, dict):
        return default_mode
    for key in ("sandboxMode", "workerSandboxMode", "sandbox"):
        raw_value = hints.get(key)
        value = normalize_worker_sandbox_mode(raw_value)
        if value:
            return value
    return default_mode


def normalize_worker_sandbox_mode(value: object) -> str | None:
    text = str(value or "").strip().lower().replace("-", "_")
    aliases = {
        "readonly": "read_only",
        "read": "read_only",
        "read_only": "read_only",
        "workspace_read": "read_only",
        "write": "workspace_write",
        "workspace_write": "workspace_write",
        "workspace": "workspace_write",
        "execute": "workspace_execute",
        "workspace_execute": "workspace_execute",
        "workspace_exec": "workspace_execute",
    }
    return aliases.get(text)


def worker_tool_names_for_sandbox(tool_names: set[str], sandbox_mode: str) -> set[str]:
    return {tool_name for tool_name in tool_names if worker_sandbox_allows_tool_name(sandbox_mode, tool_name)}


def worker_sandbox_allows_tool(scope: WorkerRuntimeScope | None, tool_name: str) -> bool:
    return scope is None or worker_sandbox_allows_tool_name(scope.sandbox_mode, tool_name)


def worker_sandbox_denial_reason(scope: WorkerRuntimeScope | None, tool_name: str) -> str:
    mode = scope.sandbox_mode if scope else "none"
    if tool_name in WORKSPACE_MUTATION_TOOLS:
        return f"Worker sandbox mode {mode} does not allow workspace mutation tool: {tool_name}"
    if tool_name in WORKSPACE_EXECUTION_TOOLS:
        return f"Worker sandbox mode {mode} does not allow command/code execution tool: {tool_name}"
    return f"Worker sandbox mode {mode} does not allow tool: {tool_name}"


def worker_sandbox_allows_tool_name(sandbox_mode: str, tool_name: str) -> bool:
    if sandbox_mode == "workspace_execute":
        return True
    if sandbox_mode == "workspace_write":
        return tool_name not in WORKSPACE_EXECUTION_TOOLS
    if sandbox_mode == "read_only":
        return tool_name not in WORKSPACE_MUTATION_TOOLS and tool_name not in WORKSPACE_EXECUTION_TOOLS
    return tool_name not in WORKSPACE_MUTATION_TOOLS and tool_name not in WORKSPACE_EXECUTION_TOOLS




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
        risk_level = _risk_level_for_labels(risk_labels, default="medium")
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
        risk_labels = _file_action_risk_labels(tool_name, path, args)
        risk_level = _risk_level_for_labels(
            risk_labels,
            default="medium" if tool_name in {"patch", "write_file"} else "low",
        )
        return {
            "key": f"{tool_name}:path:{path_key}",
            "label": f"{tool_name} {path_key}",
            "riskLevel": risk_level,
            "riskLabels": risk_labels,
        }

    if tool_name == "web_extract":
        url = str(args.get("url") or "").strip()
        risk_labels = _web_action_risk_labels(url)
        return {
            "key": f"web_extract:url:{url or '*'}",
            "label": f"web page extraction{f' from {url}' if url else ''}",
            "riskLevel": _risk_level_for_labels(risk_labels, default="medium"),
            "riskLabels": risk_labels,
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
    if not command:
        labels.append("unknown_target")
    destructive_patterns = [
        r"(^|[;&|]\s*)rm\s+",
        r"(^|[;&|]\s*)sudo\s+rm\s+",
        r"(^|[;&|]\s*)rmdir\s+",
        r"(^|[;&|]\s*)sudo\s+rmdir\s+",
        r"(^|[;&|]\s*)git\s+clean\b",
        r"(^|[;&|]\s*)git\s+reset\s+--hard\b",
        r"(^|[;&|]\s*)find\b.*\s-delete\b",
        r"\b(dropdb|truncate|docker\s+system\s+prune)\b",
    ]
    installer_patterns = [
        r"\b(npm|pnpm|yarn|pip|pip3|uv|brew|cargo|gem)\s+install\b",
        r"\bnpx\b",
    ]
    network_script_patterns = [
        r"\b(curl|wget)\b.*\|\s*(sh|bash|zsh|python|python3)\b",
        r"\b(bash|sh|zsh)\s+<\s*\(",
    ]
    network_patterns = [
        r"\b(curl|wget|ssh|scp|rsync)\b",
        r"\bhttps?://",
    ]
    privileged_patterns = [
        r"(^|[;&|]\s*)sudo\b",
        r"(^|[;&|]\s*)(chmod|chown)\s+(-R\s+)?",
    ]
    secret_patterns = [
        r"\b(API[_-]?KEY|TOKEN|SECRET|PASSWORD|PRIVATE[_-]?KEY)\b",
        r"(^|[;&|]\s*)(cat|grep|sed|awk|tail|head)\b.*(\.env|id_rsa|credentials|secret|token|\.pem)\b",
        r"(^|[;&|]\s*)(env|printenv)\b",
    ]
    if any(re.search(pattern, command) for pattern in destructive_patterns):
        labels.append("destructive")
    if any(re.search(pattern, command) for pattern in installer_patterns):
        labels.append("installer")
    if any(re.search(pattern, command) for pattern in network_script_patterns):
        labels.append("network_script")
    if any(re.search(pattern, command, flags=re.IGNORECASE) for pattern in network_patterns):
        labels.append("network_access")
    if any(re.search(pattern, command, flags=re.IGNORECASE) for pattern in privileged_patterns):
        labels.append("privileged")
    if any(re.search(pattern, command, flags=re.IGNORECASE) for pattern in secret_patterns):
        labels.append("sensitive_data")
    return _dedupe_labels(labels)


def _file_action_risk_labels(tool_name: str, path: str, args: dict[str, Any]) -> list[str]:
    labels: list[str] = []
    if tool_name in {"patch", "write_file"}:
        labels.append("workspace_mutation")
    else:
        labels.append("local_file_exposure")
    normalized_path = path.replace("\\", "/").strip()
    if not normalized_path:
        labels.append("unknown_target")
    if normalized_path.startswith("/") or normalized_path.startswith("../") or "/../" in normalized_path:
        labels.append("workspace_external_path")
    if _is_sensitive_path(normalized_path):
        labels.append("sensitive_path")
    if tool_name == "write_file":
        labels.append("whole_file_write")
    if tool_name == "patch" and bool(args.get("replaceAll")):
        labels.append("bulk_replace")
    return _dedupe_labels(labels)


def _web_action_risk_labels(url: str) -> list[str]:
    labels = ["external_content"]
    normalized = url.strip().lower()
    if not normalized:
        labels.append("unknown_target")
    if normalized.startswith("file:"):
        labels.append("local_file_exposure")
    if normalized.startswith("http://"):
        labels.append("insecure_transport")
    if re.search(r"(token|api_key|apikey|secret|password)=", normalized):
        labels.append("sensitive_data")
    return _dedupe_labels(labels)


def _is_sensitive_path(path: str) -> bool:
    normalized = path.lower()
    sensitive_patterns = [
        r"(^|/)\.env($|[./-])",
        r"(^|/)\.ssh($|/)",
        r"(^|/)id_rsa($|[./-])",
        r"(^|/)(credentials|secrets?|tokens?)(\.|/|$)",
        r"\.(pem|key|p12|pfx)$",
    ]
    return any(re.search(pattern, normalized) for pattern in sensitive_patterns)


def _risk_level_for_labels(labels: list[str], *, default: str) -> str:
    high_risk_labels = {
        "destructive",
        "installer",
        "network_script",
        "privileged",
        "sensitive_data",
        "sensitive_path",
        "workspace_external_path",
        "whole_file_write",
        "bulk_replace",
    }
    return "high" if any(label in high_risk_labels for label in labels) else default


def _dedupe_labels(labels: list[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for label in labels:
        if label not in seen:
            seen.add(label)
            deduped.append(label)
    return deduped


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
