from __future__ import annotations

import argparse
import logging
import os
import signal
import sys
import threading
from pathlib import Path
from typing import Any
from uuid import uuid4

RUNTIME_DIR = Path(__file__).resolve().parent
PACKAGES_DIR = RUNTIME_DIR.parent
REPO_ROOT = PACKAGES_DIR.parent
sys.path.insert(0, str(PACKAGES_DIR))

from amadeus.memory import MessageMemoryStore
from amadeus.workers import (
    DEFAULT_TASK_WORKSPACE_ISOLATION,
    SubprocessTaskRunner,
    TaskResourceLimits,
    TaskWorker,
)


logger = logging.getLogger(__name__)
DEFAULT_LEASE_NAME = "task-supervisor"
DEFAULT_POLL_INTERVAL_SECONDS = 1.0
DEFAULT_LEASE_SECONDS = 45.0


class DurableTaskSupervisor:
    def __init__(
        self,
        *,
        database_path: str | Path,
        workspace_path: str | Path | None = None,
        workspace_isolation: str = DEFAULT_TASK_WORKSPACE_ISOLATION,
        sandbox_root: str | Path | None = None,
        logs_root: str | Path | None = None,
        os_sandbox_mode: str | None = None,
        max_workers: int = 2,
        poll_interval_seconds: float = DEFAULT_POLL_INTERVAL_SECONDS,
        lease_seconds: float | None = None,
        owner_id: str | None = None,
        resource_limits: TaskResourceLimits | None = None,
        process_factory: Any | None = None,
    ) -> None:
        self.database_path = Path(database_path).expanduser()
        self.workspace_path = Path(workspace_path).expanduser() if workspace_path else None
        self.poll_interval_seconds = max(0.1, float(poll_interval_seconds))
        self.lease_seconds = max(
            1.0,
            float(lease_seconds if lease_seconds is not None else DEFAULT_LEASE_SECONDS),
        )
        self.owner_id = owner_id or f"supervisor-{uuid4().hex[:16]}"
        self.memory_store = MessageMemoryStore(
            self.database_path,
            default_workspace_path=self.workspace_path,
        )
        self.runner = SubprocessTaskRunner(
            database_path=self.database_path,
            max_workers=max_workers,
            workspace_path=self.workspace_path,
            workspace_isolation=workspace_isolation,
            sandbox_root=sandbox_root,
            logs_root=logs_root,
            supervisor_id=self.owner_id,
            resource_limits=resource_limits,
            os_sandbox_mode=os_sandbox_mode,
            process_factory=process_factory,
        )
        self.worker = TaskWorker(
            lambda: self.memory_store,
            lambda: None,
            max_workers=max_workers,
            stale_after_seconds=max(15.0, self.lease_seconds),
            recovery_interval_seconds=self.poll_interval_seconds,
            runner_kind="subprocess",
            runner=self.runner,
        )
        self.stop_event = threading.Event()
        self._owns_lease = False
        self._lease_heartbeat_stop = threading.Event()
        self._lease_heartbeat_thread: threading.Thread | None = None

    def acquire(self) -> bool:
        result = self.memory_store.acquire_supervisor_lease(
            DEFAULT_LEASE_NAME,
            owner_id=self.owner_id,
            pid=os.getpid(),
            lease_seconds=self.lease_seconds,
            metadata=self._lease_metadata(),
        )
        self._owns_lease = bool(result["acquired"])
        return self._owns_lease

    def tick(self) -> dict[str, object]:
        if not self._owns_lease and not self.acquire():
            return {
                "ownerId": self.owner_id,
                "leaseAcquired": False,
                "runner": self.runner.status(),
            }
        heartbeat = self.memory_store.heartbeat_supervisor_lease(
            DEFAULT_LEASE_NAME,
            owner_id=self.owner_id,
            lease_seconds=self.lease_seconds,
            metadata=self._lease_metadata(),
        )
        if heartbeat is None:
            self._owns_lease = False
            raise RuntimeError("task supervisor lost its durable lease")
        reconciliation = self.runner.reconcile_durable_processes()
        recovered = self.worker.recover()
        status = {
            "ownerId": self.owner_id,
            "leaseAcquired": True,
            "recoveredTaskCount": len(recovered),
            "reconciliation": reconciliation,
            "runner": self.runner.status(),
        }
        final_heartbeat = self.memory_store.heartbeat_supervisor_lease(
            DEFAULT_LEASE_NAME,
            owner_id=self.owner_id,
            lease_seconds=self.lease_seconds,
            metadata=self._lease_metadata(status),
        )
        if final_heartbeat is None:
            self._owns_lease = False
            raise RuntimeError("task supervisor lost its durable lease")
        return status

    def run(self, *, once: bool = False) -> int:
        if not self.acquire():
            lease = self.memory_store.get_supervisor_lease(DEFAULT_LEASE_NAME)
            logger.error("Task supervisor lease is already held: %s", lease)
            return 2
        print(
            f"Amadeus task supervisor ready owner={self.owner_id} database={self.database_path}",
            flush=True,
        )
        self._start_lease_heartbeat()
        try:
            while not self.stop_event.is_set():
                self.tick()
                if once:
                    return 0
                self.stop_event.wait(self.poll_interval_seconds)
            return 0
        finally:
            self.close(detach_children=True)

    def request_stop(self) -> None:
        self.stop_event.set()

    def close(self, *, detach_children: bool) -> None:
        self._lease_heartbeat_stop.set()
        heartbeat_thread = self._lease_heartbeat_thread
        if (
            heartbeat_thread
            and heartbeat_thread.is_alive()
            and heartbeat_thread is not threading.current_thread()
        ):
            heartbeat_thread.join(timeout=max(1.0, self.lease_seconds / 3.0 + 0.5))
        if detach_children:
            self.runner.detach()
        else:
            self.worker.shutdown(wait=False)
        if self._owns_lease:
            self.memory_store.release_supervisor_lease(
                DEFAULT_LEASE_NAME,
                owner_id=self.owner_id,
            )
            self._owns_lease = False

    def _start_lease_heartbeat(self) -> None:
        if self._lease_heartbeat_thread and self._lease_heartbeat_thread.is_alive():
            return
        self._lease_heartbeat_stop.clear()
        self._lease_heartbeat_thread = threading.Thread(
            target=self._lease_heartbeat_loop,
            name="amadeus-task-supervisor-lease-heartbeat",
            daemon=True,
        )
        self._lease_heartbeat_thread.start()

    def _lease_heartbeat_loop(self) -> None:
        interval = max(0.25, min(self.poll_interval_seconds, self.lease_seconds / 3.0))
        while not self._lease_heartbeat_stop.wait(interval):
            if not self._owns_lease:
                return
            try:
                heartbeat = self.memory_store.heartbeat_supervisor_lease(
                    DEFAULT_LEASE_NAME,
                    owner_id=self.owner_id,
                    lease_seconds=self.lease_seconds,
                )
            except Exception:
                logger.exception("Task supervisor lease heartbeat failed")
                continue
            if heartbeat is None:
                self._owns_lease = False
                self.stop_event.set()
                logger.error("Task supervisor lost its durable lease")
                return

    def _lease_metadata(self, status: dict[str, object] | None = None) -> dict[str, object]:
        metadata: dict[str, object] = {
            "workspacePath": str(self.workspace_path) if self.workspace_path else None,
            "pollIntervalSeconds": self.poll_interval_seconds,
            "leaseSeconds": self.lease_seconds,
            "osSandbox": self.runner.status().get("osSandbox"),
        }
        if status:
            metadata["status"] = status
        return metadata


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the durable Amadeus task subprocess supervisor.")
    parser.add_argument(
        "--database",
        default=os.environ.get("AMADEUS_MEMORY_DB", str(REPO_ROOT / "data" / "amadeus.sqlite")),
    )
    parser.add_argument(
        "--workspace",
        default=os.environ.get("AMADEUS_WORKSPACE", str(REPO_ROOT)),
    )
    parser.add_argument(
        "--workspace-isolation",
        default=os.environ.get("AMADEUS_TASK_WORKSPACE_ISOLATION", DEFAULT_TASK_WORKSPACE_ISOLATION),
    )
    parser.add_argument(
        "--sandbox-root",
        default=os.environ.get("AMADEUS_TASK_WORKSPACE_SANDBOX_ROOT", ""),
    )
    parser.add_argument(
        "--logs-root",
        default=os.environ.get("AMADEUS_TASK_LOGS_ROOT", ""),
    )
    parser.add_argument(
        "--os-sandbox",
        default=os.environ.get("AMADEUS_TASK_OS_SANDBOX", "auto"),
    )
    parser.add_argument(
        "--max-workers",
        type=int,
        default=int(os.environ.get("AMADEUS_TASK_MAX_WORKERS", "2")),
    )
    parser.add_argument(
        "--poll-interval",
        type=float,
        default=float(
            os.environ.get(
                "AMADEUS_TASK_SUPERVISOR_POLL_SECONDS",
                str(DEFAULT_POLL_INTERVAL_SECONDS),
            )
        ),
    )
    parser.add_argument(
        "--lease-seconds",
        type=float,
        default=float(
            os.environ.get(
                "AMADEUS_TASK_SUPERVISOR_LEASE_SECONDS",
                str(DEFAULT_LEASE_SECONDS),
            )
        ),
    )
    parser.add_argument("--owner-id", default=os.environ.get("AMADEUS_TASK_SUPERVISOR_ID", ""))
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=getattr(logging, os.environ.get("AMADEUS_LOG_LEVEL", "INFO").upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    supervisor = DurableTaskSupervisor(
        database_path=args.database,
        workspace_path=args.workspace,
        workspace_isolation=args.workspace_isolation,
        sandbox_root=args.sandbox_root or None,
        logs_root=args.logs_root or None,
        os_sandbox_mode=args.os_sandbox,
        max_workers=max(1, args.max_workers),
        poll_interval_seconds=max(0.1, args.poll_interval),
        lease_seconds=args.lease_seconds,
        owner_id=args.owner_id or None,
    )

    def handle_signal(_signum: int, _frame: object) -> None:
        supervisor.request_stop()

    previous_handlers: dict[int, Any] = {}
    for signum in (signal.SIGINT, signal.SIGTERM):
        previous_handlers[signum] = signal.getsignal(signum)
        signal.signal(signum, handle_signal)
    try:
        return supervisor.run(once=args.once)
    finally:
        for signum, handler in previous_handlers.items():
            signal.signal(signum, handler)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
