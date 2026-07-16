#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PYTHON_RUNTIME_URL = "http://127.0.0.1:8790"
DEFAULT_BRIDGE_URL = "http://127.0.0.1:8788"
DEFAULT_MAIN_UI_URL = "http://127.0.0.1:5178"


@dataclass(frozen=True)
class ManagedProcess:
    name: str
    command: list[str]
    health_url: str | None = None
    required: bool = True


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the Amadeus local development stack with basic supervision.")
    parser.add_argument("--no-desktop", action="store_true", help="Run only Python runtime and Node bridge.")
    parser.add_argument("--reuse-existing", action="store_true", help="Reuse already healthy runtime/bridge processes instead of failing on occupied health endpoints.")
    parser.add_argument("--health-timeout", type=float, default=30.0, help="Seconds to wait for each HTTP health check.")
    args = parser.parse_args()

    python_runtime_url = os.environ.get("AMADEUS_PYTHON_RUNTIME_URL", DEFAULT_PYTHON_RUNTIME_URL).rstrip("/")
    bridge_url = os.environ.get("AMADEUS_SERVER_URL", DEFAULT_BRIDGE_URL).rstrip("/")
    main_ui_url = os.environ.get("AMADEUS_MAIN_UI_DEV_URL", DEFAULT_MAIN_UI_URL).rstrip("/")
    npm = "npm.cmd" if os.name == "nt" else "npm"
    task_runner = str(os.environ.get("AMADEUS_TASK_RUNNER", "subprocess")).strip().lower().replace("-", "_")
    subprocess_runners = {"subprocess", "external_process", "external", "process_entrypoint"}
    supervisor_mode = str(
        os.environ.get(
            "AMADEUS_TASK_SUPERVISOR_MODE",
            "external" if task_runner in subprocess_runners else "embedded",
        )
    ).strip().lower()

    processes: list[ManagedProcess] = []
    if task_runner in subprocess_runners and supervisor_mode == "external":
        processes.append(ManagedProcess(
            name="task-supervisor",
            command=[sys.executable, "packages/amadeus/task_supervisor.py"],
            health_url=None,
        ))
    processes.extend([
        ManagedProcess(
            name="python-runtime",
            command=[sys.executable, "packages/amadeus/server.py"],
            health_url=f"{python_runtime_url}/runtime/health",
        ),
        ManagedProcess(
            name="bridge",
            command=[npm, "--workspace", "apps/server", "run", "dev"],
            health_url=f"{bridge_url}/health",
        ),
    ])
    if not args.no_desktop:
        processes.append(ManagedProcess(
            name="main-ui",
            command=[npm, "--workspace", "apps/desktop-ui-next", "run", "dev"],
            health_url=main_ui_url,
        ))
        processes.append(ManagedProcess(
            name="desktop",
            command=[npm, "--workspace", "apps/desktop", "run", "dev"],
            health_url=None,
        ))

    supervisor = StackSupervisor(
        processes,
        health_timeout_seconds=args.health_timeout,
        reuse_existing=args.reuse_existing,
    )
    return supervisor.run()


class StackSupervisor:
    def __init__(self, processes: list[ManagedProcess], *, health_timeout_seconds: float, reuse_existing: bool) -> None:
        self.processes = processes
        self.health_timeout_seconds = max(1.0, health_timeout_seconds)
        self.reuse_existing = reuse_existing
        self.children: list[tuple[ManagedProcess, subprocess.Popen[str]]] = []
        self.stop_requested = threading.Event()

    def run(self) -> int:
        install_signal_handlers(self.request_stop)
        try:
            for spec in self.processes:
                if self.should_reuse_or_fail(spec):
                    continue
                child = self.start_process(spec)
                self.children.append((spec, child))
                if spec.health_url:
                    self.wait_for_health(spec, child)
            return self.wait_for_exit()
        except KeyboardInterrupt:
            self.request_stop("keyboard interrupt")
            return 130
        except RuntimeError as error:
            print(f"[dev-stack] {error}", file=sys.stderr, flush=True)
            return 1
        finally:
            self.stop_all()

    def should_reuse_or_fail(self, spec: ManagedProcess) -> bool:
        if not spec.health_url or not http_ok(spec.health_url):
            return False
        if self.reuse_existing:
            print(f"[dev-stack] reusing existing {spec.name}: {spec.health_url}", flush=True)
            return True
        raise RuntimeError(
            f"{spec.name} health endpoint is already serving at {spec.health_url}. "
            "Stop the existing process, change the port environment variables, or pass --reuse-existing."
        )

    def start_process(self, spec: ManagedProcess) -> subprocess.Popen[str]:
        print(f"[dev-stack] starting {spec.name}: {' '.join(spec.command)}", flush=True)
        child = subprocess.Popen(
            spec.command,
            cwd=REPO_ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        threading.Thread(target=stream_output, args=(spec.name, child), daemon=True).start()
        return child

    def wait_for_health(self, spec: ManagedProcess, child: subprocess.Popen[str]) -> None:
        assert spec.health_url is not None
        deadline = time.monotonic() + self.health_timeout_seconds
        while time.monotonic() < deadline:
            if self.stop_requested.is_set():
                return
            return_code = child.poll()
            if return_code is not None:
                raise RuntimeError(f"{spec.name} exited before becoming healthy with code {return_code}")
            if http_ok(spec.health_url):
                print(f"[dev-stack] {spec.name} healthy: {spec.health_url}", flush=True)
                return
            time.sleep(0.5)
        raise RuntimeError(f"{spec.name} did not become healthy within {self.health_timeout_seconds:g}s: {spec.health_url}")

    def wait_for_exit(self) -> int:
        while not self.stop_requested.is_set():
            for spec, child in self.children:
                return_code = child.poll()
                if return_code is None:
                    continue
                if spec.required:
                    print(f"[dev-stack] {spec.name} exited with code {return_code}; stopping stack", flush=True)
                    return int(return_code or 0)
            time.sleep(0.5)
        return 0

    def request_stop(self, reason: str) -> None:
        if not self.stop_requested.is_set():
            print(f"[dev-stack] stopping stack: {reason}", flush=True)
        self.stop_requested.set()

    def stop_all(self) -> None:
        for spec, child in reversed(self.children):
            if child.poll() is not None:
                continue
            print(f"[dev-stack] terminating {spec.name}", flush=True)
            child.terminate()
        deadline = time.monotonic() + 8.0
        for _spec, child in reversed(self.children):
            remaining = max(0.1, deadline - time.monotonic())
            try:
                child.wait(timeout=remaining)
            except subprocess.TimeoutExpired:
                child.kill()


def stream_output(name: str, child: subprocess.Popen[str]) -> None:
    if child.stdout is None:
        return
    for line in child.stdout:
        print(f"[{name}] {line.rstrip()}", flush=True)


def http_ok(url: str) -> bool:
    try:
        request = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(request, timeout=1.0) as response:
            return 200 <= response.status < 300
    except (OSError, urllib.error.URLError):
        return False


def install_signal_handlers(on_signal: Callable[[str], None]) -> None:
    def _handler(signum: int, _frame: object) -> None:
        on_signal(signal.Signals(signum).name)

    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, _handler)


if __name__ == "__main__":
    raise SystemExit(main())
