from __future__ import annotations

import re
from dataclasses import dataclass


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


@dataclass(frozen=True)
class MemorySafetyDecision:
    allowed: bool
    reason: str = ""


def evaluate_memory_candidate(scope: str, content: str, reason: str | None = None) -> MemorySafetyDecision:
    text = "\n".join(part for part in [scope, content, reason or ""] if part).strip()
    if not text:
        return MemorySafetyDecision(False, "empty_candidate")

    secret_reason = detect_secret_reason(text)
    if secret_reason:
        return MemorySafetyDecision(False, secret_reason)

    debug_reason = detect_temporary_debug_reason(text)
    if debug_reason:
        return MemorySafetyDecision(False, debug_reason)

    uncertain_reason = detect_uncertain_claim_reason(text)
    if uncertain_reason:
        return MemorySafetyDecision(False, uncertain_reason)

    local_path_reason = detect_local_path_reason(text)
    if local_path_reason:
        return MemorySafetyDecision(False, local_path_reason)

    return MemorySafetyDecision(True)


def detect_secret_reason(text: str) -> str | None:
    for reason, pattern in SECRET_PATTERNS:
        if pattern.search(text):
            return f"secret:{reason}"
    return None


def detect_temporary_debug_reason(text: str) -> str | None:
    for reason, pattern in TEMPORARY_DEBUG_PATTERNS:
        if pattern.search(text):
            return f"temporary_debug:{reason}"
    return None


def detect_uncertain_claim_reason(text: str) -> str | None:
    for reason, pattern in UNCERTAIN_CLAIM_PATTERNS:
        if pattern.search(text):
            return f"uncertain_claim:{reason}"
    return None


def detect_local_path_reason(text: str) -> str | None:
    for reason, pattern in LOCAL_PATH_PATTERNS:
        if pattern.search(text):
            return f"local_path:{reason}"
    return None
