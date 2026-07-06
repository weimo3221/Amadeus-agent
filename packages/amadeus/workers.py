from __future__ import annotations

import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Protocol
from uuid import uuid4

from amadeus.agent import AgentEvent, PermissionRequest
from amadeus.memory import MessageMemoryStore


logger = logging.getLogger(__name__)

MemoryStoreProvider = Callable[[], MessageMemoryStore]
AgentRuntimeProvider = Callable[[], Any]
TaskEventPublisher = Callable[[dict[str, object], str], None]
TaskCallable = Callable[[str], None]


class TaskRunner(Protocol):
    def submit(self, task_id: str, run_task: TaskCallable) -> None:
        ...

    def shutdown(self, *, wait: bool = True) -> None:
        ...


class InProcessTaskRunner:
    def __init__(self, *, max_workers: int = 2) -> None:
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="amadeus-task")

    def submit(self, task_id: str, run_task: TaskCallable) -> None:
        self._executor.submit(run_task, task_id)

    def shutdown(self, *, wait: bool = True) -> None:
        self._executor.shutdown(wait=wait, cancel_futures=True)


class TaskWorker:
    def __init__(
        self,
        memory_store_provider: MemoryStoreProvider,
        agent_runtime_provider: AgentRuntimeProvider,
        *,
        max_workers: int = 2,
        publish_task_event: TaskEventPublisher | None = None,
        retry_base_delay_seconds: float = 1.0,
        retry_max_delay_seconds: float = 30.0,
        stale_after_seconds: float = 300.0,
        runner: TaskRunner | None = None,
    ) -> None:
        self._memory_store_provider = memory_store_provider
        self._agent_runtime_provider = agent_runtime_provider
        self._publish_task_event = publish_task_event
        self._retry_base_delay_seconds = max(0.0, float(retry_base_delay_seconds))
        self._retry_max_delay_seconds = max(self._retry_base_delay_seconds, float(retry_max_delay_seconds))
        self._stale_after_seconds = max(1.0, float(stale_after_seconds))
        self._runner = runner or InProcessTaskRunner(max_workers=max_workers)
        self._lock = threading.Lock()
        self._running: dict[str, threading.Event] = {}
        self._turns: dict[str, tuple[str, str]] = {}
        self._timers: dict[str, threading.Timer] = {}

    def submit(self, task_id: str) -> None:
        self._runner.submit(task_id, self._run_task)

    def recover(self) -> list[dict[str, object]]:
        memory_store = self._memory_store_provider()
        recovered = memory_store.recover_stale_running_tasks(stale_after_seconds=self._stale_after_seconds)
        for task in recovered:
            self._publish_task_update(task, "recovered")
        runnable = memory_store.list_runnable_tasks(limit=100)
        for task in runnable:
            self.submit(str(task["id"]))
        return recovered

    def shutdown(self, *, wait: bool = True) -> None:
        with self._lock:
            timers = list(self._timers.values())
            self._timers.clear()
        for timer in timers:
            timer.cancel()
        self._runner.shutdown(wait=wait)

    def cancel(self, task_id: str, *, reason: str | None = None) -> dict[str, object]:
        with self._lock:
            cancel_event = self._running.get(task_id)
            running_turn = self._turns.get(task_id)
        task = self._memory_store_provider().cancel_task(task_id, reason=reason)
        self._sync_plan_item(self._memory_store_provider(), task, "cancelled")
        self._publish_task_update(task, "cancelled")
        if cancel_event:
            cancel_event.set()
        if running_turn:
            session_id, turn_id = running_turn
            try:
                self._agent_runtime_provider().cancel_turn(session_id, turn_id=turn_id)
            except Exception as error:
                logger.info("Task worker failed to cancel backing turn taskId=%s error=%s", task_id, error)
        return task

    def _run_task(self, task_id: str) -> None:
        memory_store = self._memory_store_provider()
        claim_lock = f"worker-{uuid4().hex[:12]}"
        cancel_event = threading.Event()
        task = memory_store.start_task(task_id, claim_lock=claim_lock)
        if not task or task.get("status") != "running":
            if task and task.get("status") == "queued":
                self._schedule_if_needed(task)
            return
        self._sync_plan_item(memory_store, task, "in_progress")
        self._publish_task_update(task, "running")

        session_id = str(task["sessionId"])
        with self._lock:
            self._running[task_id] = cancel_event

        result_text: str | None = None
        error_text: str | None = None
        try:
            prompt = self._task_prompt(task)
            runtime = self._agent_runtime_provider()
            for event in runtime.run_turn(session_id, prompt, self._deny_permission):
                if isinstance(event, AgentEvent):
                    event_type = event.type
                    payload = event.payload
                else:
                    event_type = str(getattr(event, "type", ""))
                    payload = getattr(event, "payload", {})
                if event_type == "agent.turn.started":
                    try:
                        memory_store.heartbeat_task(task_id, claim_lock=claim_lock)
                    except Exception:
                        logger.debug("Task heartbeat failed taskId=%s", task_id, exc_info=True)
                    turn_id = str(payload.get("turnId") or "")
                    if turn_id:
                        with self._lock:
                            self._turns[task_id] = (session_id, turn_id)
                        if cancel_event.is_set():
                            runtime.cancel_turn(session_id, turn_id=turn_id)
                if cancel_event.is_set():
                    self._ensure_cancelled(task_id, reason="Task worker cancelled")
                    return
                if event_type == "assistant.message":
                    result_text = str(payload.get("text") or "")
                elif event_type == "agent.turn.cancelled":
                    self._ensure_cancelled(task_id, reason="Agent turn cancelled")
                    return
                elif event_type == "error":
                    error_text = str(payload.get("message") or payload.get("code") or "Task failed")

            if cancel_event.is_set():
                self._ensure_cancelled(task_id, reason="Task worker cancelled")
                return
            if error_text:
                self._handle_failure(memory_store, task_id, claim_lock=claim_lock, task=task, error=error_text)
            elif bool(task.get("reviewRequired")):
                blocked = memory_store.block_task(
                    task_id,
                    claim_lock=claim_lock,
                    result=result_text or "",
                    reason="Review required before marking this task complete.",
                )
                self._sync_plan_item(memory_store, blocked, "pending")
                self._publish_task_update(blocked, "blocked")
            else:
                completed = memory_store.complete_task(task_id, claim_lock=claim_lock, result=result_text or "")
                self._sync_plan_item(memory_store, completed, "completed")
                self._publish_task_update(completed, "succeeded")
        except Exception as error:
            logger.info("Task worker execution failed taskId=%s error=%s", task_id, error)
            try:
                latest = memory_store.get_task(task_id) or task
                self._handle_failure(memory_store, task_id, claim_lock=claim_lock, task=latest, error=str(error))
            except Exception as finish_error:
                logger.info("Task worker failed to mark task failed taskId=%s error=%s", task_id, finish_error)
        finally:
            with self._lock:
                self._running.pop(task_id, None)
                self._turns.pop(task_id, None)

    @staticmethod
    def _task_prompt(task: dict[str, object]) -> str:
        title = str(task.get("title") or "").strip()
        body = str(task.get("body") or "").strip()
        if body:
            return f"{title}\n\n{body}"
        return title

    def _handle_failure(
        self,
        memory_store: MessageMemoryStore,
        task_id: str,
        *,
        claim_lock: str,
        task: dict[str, object],
        error: str,
    ) -> dict[str, object]:
        attempt_count = int(task.get("attemptCount") or 0)
        max_attempts = int(task.get("maxAttempts") or 1)
        if attempt_count < max_attempts:
            next_run_at = self._next_retry_at(attempt_count)
            retried = memory_store.retry_task(
                task_id,
                claim_lock=claim_lock,
                error=error,
                next_run_at=next_run_at,
            )
            self._publish_task_update(retried, "retry_scheduled")
            self._schedule_if_needed(retried)
            return retried
        failed = memory_store.fail_task(task_id, claim_lock=claim_lock, error=error)
        self._sync_plan_item(memory_store, failed, "pending")
        self._publish_task_update(failed, "failed")
        return failed

    @staticmethod
    def _sync_plan_item(memory_store: MessageMemoryStore, task: dict[str, object], status: str) -> None:
        plan_item_id = str(task.get("planItemId") or "").strip()
        session_id = str(task.get("sessionId") or "").strip()
        if not plan_item_id or not session_id:
            return
        try:
            memory_store.update_plan_item_status(
                session_id=session_id,
                plan_item_id=plan_item_id,
                status=status,
            )
        except Exception:
            logger.debug("Task worker failed to sync plan item taskId=%s status=%s", task.get("id"), status, exc_info=True)

    def _next_retry_at(self, attempt_count: int) -> str:
        delay = min(
            self._retry_max_delay_seconds,
            self._retry_base_delay_seconds * (2 ** max(0, attempt_count - 1)),
        )
        return (datetime.now(timezone.utc) + timedelta(seconds=delay)).isoformat()

    def _schedule_if_needed(self, task: dict[str, object]) -> None:
        task_id = str(task.get("id") or "")
        if not task_id:
            return
        candidates = [value for value in (task.get("nextRunAt"), task.get("dueAt")) if value]
        if not candidates:
            return
        delay = max(0.0, max(self._delay_until(str(value)) for value in candidates))
        if delay <= 0:
            self.submit(task_id)
            return

        def _resubmit() -> None:
            with self._lock:
                self._timers.pop(task_id, None)
            self.submit(task_id)

        with self._lock:
            existing = self._timers.pop(task_id, None)
            if existing:
                existing.cancel()
            timer = threading.Timer(delay, _resubmit)
            timer.daemon = True
            self._timers[task_id] = timer
            timer.start()

    @staticmethod
    def _delay_until(value: str) -> float:
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return 0.0
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return (parsed.astimezone(timezone.utc) - datetime.now(timezone.utc)).total_seconds()

    def _ensure_cancelled(self, task_id: str, *, reason: str) -> dict[str, object]:
        memory_store = self._memory_store_provider()
        task = memory_store.get_task(task_id)
        if task and task.get("status") == "cancelled":
            return task
        cancelled = memory_store.cancel_task(task_id, reason=reason)
        self._sync_plan_item(memory_store, cancelled, "cancelled")
        self._publish_task_update(cancelled, "cancelled")
        return cancelled

    def _publish_task_update(self, task: dict[str, object], action: str) -> None:
        if self._publish_task_event is None:
            return
        try:
            self._publish_task_event(task, action)
        except Exception as error:
            logger.info("Task worker failed to publish task update taskId=%s action=%s error=%s", task.get("id"), action, error)

    @staticmethod
    def _deny_permission(_request: PermissionRequest) -> bool:
        return False
