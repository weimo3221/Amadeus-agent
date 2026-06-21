from __future__ import annotations

import logging
import re
from dataclasses import dataclass


logger = logging.getLogger(__name__)

SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("private_key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----", re.IGNORECASE)),
    ("api_key_assignment", re.compile(r"\b(?:api[_-]?key|secret[_-]?key|access[_-]?key|client[_-]?secret)\b\s*[:=]\s*['\"]?[A-Za-z0-9_./+=-]{12,}", re.IGNORECASE)),
    ("token_assignment", re.compile(r"\b(?:token|auth[_-]?token|bearer|jwt|session[_-]?id|cookie)\b\s*[:=]\s*['\"]?[A-Za-z0-9_./+=-]{16,}", re.IGNORECASE)),
    ("password_assignment", re.compile(r"\b(?:password|passwd|pwd)\b\s*[:=]\s*['\"]?[^'\"\s]{6,}", re.IGNORECASE)),
    ("bearer_token", re.compile(r"\bbearer\s+[A-Za-z0-9_./+=-]{16,}", re.IGNORECASE)),
    ("github_token", re.compile(r"\bgh[opsu]_[A-Za-z0-9_]{20,}\b")),
    ("openai_key", re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b")),
    ("aws_access_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
)

TEMPORARY_DEBUG_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("transient_error", re.compile(r"\b(?:traceback|stack trace|exception|runtimeerror|typeerror|valueerror|syntaxerror)\b", re.IGNORECASE)),
    ("temporary_failure", re.compile(r"\b(?:failed|failure|error|crash|timeout|timed out|retry|rerun)\b", re.IGNORECASE)),
    ("command_state", re.compile(r"\b(?:running command|terminal|process id|command id|exit code|stdout|stderr|logs?)\b", re.IGNORECASE)),
    ("test_run_state", re.compile(r"\b(?:tests? (?:failed|passed|running)|npm test|pytest|unittest|typecheck|git diff --check)\b", re.IGNORECASE)),
    ("ui_state", re.compile(r"\b(?:currently open|opened file|selected line|cursor|panel|modal|button clicked|screen state)\b", re.IGNORECASE)),
    ("temporary_wording", re.compile(r"\b(?:temporary|temporarily|one[- ]?off|for now|current run|this run|right now|at the moment)\b", re.IGNORECASE)),
)

UNCERTAIN_CLAIM_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("speculative_modal", re.compile(r"\b(?:may|might|maybe|perhaps|possibly|probably|likely|seems?|appears?|looks like)\b", re.IGNORECASE)),
    ("uncertain_phrase", re.compile(r"\b(?:not sure|unclear|unknown|guess|assume|assumption|hypothesis|speculat(?:e|ion|ive))\b", re.IGNORECASE)),
    ("chinese_speculation", re.compile(r"(?:可能|也许|大概|似乎|看起来|猜测|推测|不确定|不清楚|假设|应该是|疑似)")),
)

LOCAL_PATH_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("tmp_path", re.compile(r"(?:^|[\s'\"`])(?:/tmp|/private/tmp|/var/folders|/var/tmp)(?:/|[\s'\"`]|$)", re.IGNORECASE)),
    ("home_cache_path", re.compile(r"(?:^|[\s'\"`])/(?:Users|home)/[^/\s'\"`]+/(?:Library/Caches|\.cache|\.npm|\.pnpm-store|\.yarn|\.cargo|\.venv|venv)(?:/|[\s'\"`]|$)", re.IGNORECASE)),
    ("home_path", re.compile(r"(?:^|[\s'\"`])/(?:Users|home)/[^/\s'\"`]+/(?:Desktop|Documents|Downloads|Workspace|work|projects|repos|src|tmp)(?:/|[\s'\"`]|$)", re.IGNORECASE)),
    ("project_cache_path", re.compile(r"(?:^|[\s'\"`])(?:\.?/)?(?:node_modules|\.next|dist|build|coverage|\.pytest_cache|__pycache__|\.mypy_cache|\.ruff_cache)(?:/|[\s'\"`]|$)", re.IGNORECASE)),
    ("generated_artifact", re.compile(r"(?:^|[\s'\"`])(?:[^/\s'\"`]+/)*(?:tmp|temp|cache|generated|artifacts?)/(?:[^/\s'\"`]+)", re.IGNORECASE)),
)

USER_SCOPE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("user_preference", re.compile(r"\b(?:the user|user)\s+(?:prefers?|likes?|wants?|asked|requested|expects?|uses?|works?|needs?)\b", re.IGNORECASE)),
    ("user_identity", re.compile(r"\b(?:the user's|user's)\s+(?:name|role|team|language|preference|style|workflow|habit)\b", re.IGNORECASE)),
    ("chinese_user", re.compile(r"(?:用户|使用者).{0,12}(?:偏好|喜欢|希望|要求|习惯|使用|正在|需要)")),
)

PROJECT_SCOPE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("project_fact", re.compile(r"\b(?:the project|project|repo|repository|codebase|runtime|package|module|config|endpoint|api|database|schema|table)\b", re.IGNORECASE)),
    ("implementation_fact", re.compile(r"\b(?:implements?|implemented|stores?|loads?|exposes?|uses?|supports?|persists?|configured|wired)\b", re.IGNORECASE)),
    ("chinese_project", re.compile(r"(?:项目|仓库|代码库|运行时|配置|接口|模块).{0,16}(?:使用|实现|支持|存储|加载|暴露|配置)")),
)

AGENT_SCOPE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("agent_behavior", re.compile(r"\b(?:the agent|agent|assistant|amadeus)\s+(?:should|must|needs? to|will|prefers?|asks?|responds?|uses?)\b", re.IGNORECASE)),
    ("assistant_behavior", re.compile(r"\b(?:respond|reply|ask before|confirm before|avoid|use concise|provide updates|explain)\b", re.IGNORECASE)),
    ("chinese_agent", re.compile(r"(?:agent|助手|助理|模型).{0,16}(?:应该|必须|需要|回复|询问|确认|避免|使用)")),
)


@dataclass(frozen=True)
class MemorySafetyDecision:
    allowed: bool
    reason: str = ""


def evaluate_memory_candidate(scope: str, content: str, reason: str | None = None) -> MemorySafetyDecision:
    normalized_scope = scope.strip().lower()
    safe_scope = normalized_scope if normalized_scope in {"user", "agent", "project"} else "invalid"
    scope_chars = len(scope)
    content_chars = len(content)
    reason_chars = len(reason or "")
    logger.info(
        "Evaluating memory review candidate safety scope=%s scopeChars=%s contentChars=%s reasonChars=%s",
        safe_scope,
        scope_chars,
        content_chars,
        reason_chars,
    )

    text = "\n".join(part for part in [scope, content, reason or ""] if part).strip()
    if not text:
        logger.info("Rejecting memory review candidate safety reason=empty_candidate scope=%s scopeChars=%s", safe_scope, scope_chars)
        return MemorySafetyDecision(False, "empty_candidate")

    secret_reason = detect_secret_reason(text)
    if secret_reason:
        logger.info(
            "Rejecting memory review candidate safety reason=%s scope=%s contentChars=%s reasonChars=%s",
            secret_reason,
            safe_scope,
            content_chars,
            reason_chars,
        )
        return MemorySafetyDecision(False, secret_reason)

    debug_reason = detect_temporary_debug_reason(text)
    if debug_reason:
        logger.info(
            "Rejecting memory review candidate safety reason=%s scope=%s contentChars=%s reasonChars=%s",
            debug_reason,
            safe_scope,
            content_chars,
            reason_chars,
        )
        return MemorySafetyDecision(False, debug_reason)

    uncertain_reason = detect_uncertain_claim_reason(text)
    if uncertain_reason:
        logger.info(
            "Rejecting memory review candidate safety reason=%s scope=%s contentChars=%s reasonChars=%s",
            uncertain_reason,
            safe_scope,
            content_chars,
            reason_chars,
        )
        return MemorySafetyDecision(False, uncertain_reason)

    local_path_reason = detect_local_path_reason(text)
    if local_path_reason:
        logger.info(
            "Rejecting memory review candidate safety reason=%s scope=%s contentChars=%s reasonChars=%s",
            local_path_reason,
            safe_scope,
            content_chars,
            reason_chars,
        )
        return MemorySafetyDecision(False, local_path_reason)

    scope_reason = detect_scope_mismatch_reason(scope, content, reason)
    if scope_reason:
        logger.info(
            "Rejecting memory review candidate safety reason=%s scope=%s contentChars=%s reasonChars=%s",
            scope_reason,
            safe_scope,
            content_chars,
            reason_chars,
        )
        return MemorySafetyDecision(False, scope_reason)

    logger.info(
        "Accepted memory review candidate safety scope=%s contentChars=%s reasonChars=%s",
        safe_scope,
        content_chars,
        reason_chars,
    )
    return MemorySafetyDecision(True)


def detect_secret_reason(text: str) -> str | None:
    for reason, pattern in SECRET_PATTERNS:
        if pattern.search(text):
            logger.info("Detected secret-like memory candidate pattern=%s textChars=%s", reason, len(text))
            return f"secret:{reason}"
    return None


def detect_temporary_debug_reason(text: str) -> str | None:
    for reason, pattern in TEMPORARY_DEBUG_PATTERNS:
        if pattern.search(text):
            logger.info("Detected temporary debug memory candidate pattern=%s textChars=%s", reason, len(text))
            return f"temporary_debug:{reason}"
    return None


def detect_uncertain_claim_reason(text: str) -> str | None:
    for reason, pattern in UNCERTAIN_CLAIM_PATTERNS:
        if pattern.search(text):
            logger.info("Detected uncertain memory candidate pattern=%s textChars=%s", reason, len(text))
            return f"uncertain_claim:{reason}"
    return None


def detect_local_path_reason(text: str) -> str | None:
    for reason, pattern in LOCAL_PATH_PATTERNS:
        if pattern.search(text):
            logger.info("Detected local path memory candidate pattern=%s textChars=%s", reason, len(text))
            return f"local_path:{reason}"
    return None


def detect_scope_mismatch_reason(scope: str, content: str, reason: str | None = None) -> str | None:
    normalized_scope = scope.strip().lower()
    if normalized_scope not in {"user", "agent", "project"}:
        logger.info("Detected invalid memory candidate scope scopeChars=%s", len(scope))
        return "scope:invalid"

    text = "\n".join(part for part in [content, reason or ""] if part).strip()
    if not text:
        logger.info("Skipping memory candidate scope classification due to empty text scope=%s", normalized_scope)
        return None

    user_signal = first_scope_signal(text, USER_SCOPE_PATTERNS)
    project_signal = first_scope_signal(text, PROJECT_SCOPE_PATTERNS)
    agent_signal = first_scope_signal(text, AGENT_SCOPE_PATTERNS)
    logger.info(
        "Classified memory candidate scope signals scope=%s userSignal=%s projectSignal=%s agentSignal=%s textChars=%s",
        normalized_scope,
        user_signal or "",
        project_signal or "",
        agent_signal or "",
        len(text),
    )

    if normalized_scope == "user":
        if agent_signal and not user_signal:
            logger.info("Detected memory candidate scope mismatch scope=user expected=agent signal=%s", agent_signal)
            return f"scope:user_contains_agent:{agent_signal}"
        if project_signal and not user_signal:
            logger.info("Detected memory candidate scope mismatch scope=user expected=project signal=%s", project_signal)
            return f"scope:user_contains_project:{project_signal}"
    elif normalized_scope == "agent":
        if user_signal and not agent_signal:
            logger.info("Detected memory candidate scope mismatch scope=agent expected=user signal=%s", user_signal)
            return f"scope:agent_contains_user:{user_signal}"
        if project_signal and not agent_signal:
            logger.info("Detected memory candidate scope mismatch scope=agent expected=project signal=%s", project_signal)
            return f"scope:agent_contains_project:{project_signal}"
    elif normalized_scope == "project":
        if user_signal and not project_signal:
            logger.info("Detected memory candidate scope mismatch scope=project expected=user signal=%s", user_signal)
            return f"scope:project_contains_user:{user_signal}"
        if agent_signal and not project_signal:
            logger.info("Detected memory candidate scope mismatch scope=project expected=agent signal=%s", agent_signal)
            return f"scope:project_contains_agent:{agent_signal}"

    return None


def first_scope_signal(text: str, patterns: tuple[tuple[str, re.Pattern[str]], ...]) -> str | None:
    for reason, pattern in patterns:
        if pattern.search(text):
            return reason
    return None
