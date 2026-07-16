from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any

from amadeus.agent import AgentRuntime
from amadeus.memory import MessageMemoryStore
from amadeus.workers import (
    SynchronousTaskRunner,
    TaskResourceLimits,
    TaskWorker,
    apply_current_process_resource_limits,
)


def _worker_workspace_from_env() -> Path | None:
    for key in ("AMADEUS_WORKER_WORKSPACE_OVERRIDE", "AMADEUS_WORKSPACE"):
        value = str(os.environ.get(key) or "").strip()
        if not value:
            continue
        candidate = Path(value).expanduser()
        try:
            candidate.mkdir(parents=True, exist_ok=True)
            return candidate.resolve()
        except (OSError, RuntimeError, ValueError):
            return None
    return None


def run_task_once(
    *,
    memory_store: MessageMemoryStore,
    agent_runtime: Any,
    task_id: str,
    runner_kind: str = "process_entrypoint",
    run_id: str | None = None,
) -> dict[str, object]:
    worker = TaskWorker(
        lambda: memory_store,
        lambda: agent_runtime,
        runner=SynchronousTaskRunner(),
        runner_kind=runner_kind,
        attempt_run_id=run_id,
    )
    worker.submit(task_id)
    worker.shutdown()
    task = memory_store.get_task(task_id)
    if task is None:
        raise ValueError("task not found")
    return task


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run one Amadeus background task in a dedicated worker process.")
    parser.add_argument("--task-id", default=os.environ.get("AMADEUS_TASK_ID", ""), help="Task id to claim and execute.")
    parser.add_argument("--run-id", default=os.environ.get("AMADEUS_TASK_RUN_ID", ""), help="Optional attempt run id.")
    parser.add_argument(
        "--database",
        default=os.environ.get("AMADEUS_MEMORY_DB", ""),
        help="Path to the Amadeus SQLite memory database.",
    )
    args = parser.parse_args(argv)
    task_id = str(args.task_id or "").strip()
    if not task_id:
        parser.error("--task-id or AMADEUS_TASK_ID is required")
    database_text = str(args.database or "").strip()
    if not database_text:
        parser.error("--database or AMADEUS_MEMORY_DB is required")
    database = Path(database_text).expanduser()
    workspace_root = _worker_workspace_from_env()
    memory_store = MessageMemoryStore(database, default_workspace_path=workspace_root)
    resource_limits = TaskResourceLimits.from_environment()
    resource_limit_result = apply_current_process_resource_limits(resource_limits)
    try:
        memory_store.record_task_event(
            task_id,
            event_type="worker_resource_limits_applied",
            message="Worker process resource limits applied",
            metadata=resource_limit_result,
        )
    except Exception:
        pass
    runtime = AgentRuntime(
        memory_store,
        audio_runtime=None,
        workspace_root=workspace_root or Path.cwd(),
    )
    run_id = str(args.run_id or "").strip() or None
    task = run_task_once(memory_store=memory_store, agent_runtime=runtime, task_id=task_id, run_id=run_id)
    status = str(task.get("status") or "")
    if status == "succeeded":
        return 0
    if status in {"failed", "cancelled"}:
        return 2
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
