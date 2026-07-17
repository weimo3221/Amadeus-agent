#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any


DEFAULT_RUNTIME_URL = "http://127.0.0.1:8790"
DEFAULT_BRIDGE_URL = "http://127.0.0.1:8788"


@dataclass
class SoakFailure:
    target: str
    error: str

    def to_payload(self) -> dict[str, str]:
        return {
            "target": self.target,
            "error": self.error,
        }


def main() -> int:
    parser = argparse.ArgumentParser(description="Poll a running Amadeus runtime/bridge stack for soak validation.")
    parser.add_argument("--runtime-url", default=DEFAULT_RUNTIME_URL, help="Python runtime base URL.")
    parser.add_argument("--bridge-url", default=DEFAULT_BRIDGE_URL, help="Node bridge base URL.")
    parser.add_argument("--session-id", default="default", help="Session id used for runtime observability probes.")
    parser.add_argument("--duration", type=float, default=60.0, help="Seconds to keep polling. 0 still takes one sample.")
    parser.add_argument("--interval", type=float, default=2.0, help="Seconds between samples.")
    parser.add_argument("--skip-bridge", action="store_true", help="Only poll the Python runtime.")
    parser.add_argument("--skip-observability", action="store_true", help="Skip /runtime/observability probes.")
    args = parser.parse_args()

    result = run_soak(
        runtime_url=args.runtime_url.rstrip("/"),
        bridge_url=args.bridge_url.rstrip("/"),
        session_id=args.session_id,
        duration_seconds=max(0.0, args.duration),
        interval_seconds=max(0.1, args.interval),
        check_bridge=not args.skip_bridge,
        check_observability=not args.skip_observability,
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result["ok"] else 1


def run_soak(
    *,
    runtime_url: str,
    bridge_url: str,
    session_id: str,
    duration_seconds: float,
    interval_seconds: float,
    check_bridge: bool = True,
    check_observability: bool = True,
) -> dict[str, Any]:
    started = time.monotonic()
    deadline = started + duration_seconds
    samples = 0
    failures: list[SoakFailure] = []
    last_runtime_status = "unknown"
    last_observability_status = "unknown"
    last_bridge_status = "skipped"

    while True:
        samples += 1
        health = probe_json(f"{runtime_url}/runtime/health", "runtime")
        if isinstance(health, SoakFailure):
            failures.append(health)
            last_runtime_status = "error"
        else:
            last_runtime_status = str(health.get("status") or "unknown")
            if last_runtime_status == "error":
                failures.append(SoakFailure("runtime", "runtime health status is error"))

        if check_observability:
            observability = probe_json(
                f"{runtime_url}/runtime/observability?sessionId={quote_query(session_id)}&limit=10",
                "observability",
            )
            if isinstance(observability, SoakFailure):
                failures.append(observability)
                last_observability_status = "error"
            else:
                last_observability_status = str(observability.get("summary", {}).get("healthStatus") or "unknown")

        if check_bridge:
            bridge = probe_json(f"{bridge_url}/health", "bridge")
            if isinstance(bridge, SoakFailure):
                failures.append(bridge)
                last_bridge_status = "error"
            else:
                last_bridge_status = "ok"

        if time.monotonic() >= deadline:
            break
        time.sleep(interval_seconds)

    elapsed = time.monotonic() - started
    return {
        "ok": not failures,
        "samples": samples,
        "durationSeconds": round(elapsed, 3),
        "runtimeUrl": runtime_url,
        "bridgeUrl": bridge_url if check_bridge else None,
        "sessionId": session_id,
        "lastRuntimeStatus": last_runtime_status,
        "lastObservabilityStatus": last_observability_status if check_observability else "skipped",
        "lastBridgeStatus": last_bridge_status,
        "failureCount": len(failures),
        "failures": [failure.to_payload() for failure in failures],
    }


def probe_json(url: str, target: str) -> dict[str, Any] | SoakFailure:
    try:
        request = urllib.request.Request(url, headers={"Accept": "application/json"}, method="GET")
        with urllib.request.urlopen(request, timeout=3.0) as response:
            body = response.read().decode("utf-8")
            if not (200 <= response.status < 300):
                return SoakFailure(target, f"HTTP {response.status}: {body[:300]}")
            payload = json.loads(body or "{}")
    except urllib.error.HTTPError as error:
        body = error.read().decode("utf-8", errors="replace")
        return SoakFailure(target, f"HTTP {error.code}: {body[:300]}")
    except (OSError, urllib.error.URLError, json.JSONDecodeError) as error:
        return SoakFailure(target, str(error))

    if not isinstance(payload, dict):
        return SoakFailure(target, "response is not a JSON object")
    if payload.get("ok") is False:
        return SoakFailure(target, f"response ok=false: {payload}")
    return payload


def quote_query(value: str) -> str:
    return urllib.parse.quote(value, safe="")


if __name__ == "__main__":
    raise SystemExit(main())
