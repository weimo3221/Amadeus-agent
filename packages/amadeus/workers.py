from __future__ import annotations

import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable
from uuid import uuid4

from amadeus.agent import AgentEvent, PermissionRequest
from amadeus.memory import MessageMemoryStore


logger = logging.getLogger(__name__)

MemoryStoreProvider = Callable[[], MessageMemoryStore]
AgentRuntimeProvider = Callable[[], Any]
TaskEventPublisher = Callable[[dict[str, object], str], None]


class TaskWorker:
    def __init__(
        self,
        memory_store_provider: MemoryStoreProvider,
        agent_runtime_provider: AgentRuntimeProvider,
        *,
        max_workers: int = 2,
        publish_task_event: TaskEventPublisher | None = None,
    ) -> None:
        self._memory_store_provider = memory_store_provider
        self._agent_runtime_provider = agent_runtime_provider
        self._publish_task_event = publish_task_event
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="amadeus-task")
        self._lock = threading.Lock()
        self._running: dict[str, threading.Event] = {}
        self._turns: dict[str, tuple[str, str]] = {}

    def submit(self, task_id: str) -> None:
        self._executor.submit(self._run_task, task_id)

    def shutdown(self, *, wait: bool = True) -> None:
        self._executor.shutdown(wait=wait, cancel_futures=True)

    def cancel(self, task_id: str, *, reason: str | None = None) -> dict[str, object]:
        with self._lock:
            cancel_event = self._running.get(task_id)
            running_turn = self._turns.get(task_id)
        task = self._memory_store_provider().cancel_task(task_id, reason=reason)
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
            return
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
                failed = memory_store.fail_task(task_id, claim_lock=claim_lock, error=error_text)
                self._publish_task_update(failed, "failed")
            else:
                completed = memory_store.complete_task(task_id, claim_lock=claim_lock, result=result_text or "")
                self._publish_task_update(completed, "succeeded")
        except Exception as error:
            logger.info("Task worker execution failed taskId=%s error=%s", task_id, error)
            try:
                failed = memory_store.fail_task(task_id, claim_lock=claim_lock, error=str(error))
                self._publish_task_update(failed, "failed")
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

    def _ensure_cancelled(self, task_id: str, *, reason: str) -> dict[str, object]:
        memory_store = self._memory_store_provider()
        task = memory_store.get_task(task_id)
        if task and task.get("status") == "cancelled":
            return task
        cancelled = memory_store.cancel_task(task_id, reason=reason)
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
