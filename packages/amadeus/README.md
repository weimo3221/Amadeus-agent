# Amadeus Runtime

Python-side Amadeus runtime.

This package is intended to become the real agent core. The surrounding TypeScript apps are moving toward transport and device-adapter roles:

- `apps/desktop`: Electron window, Live2D rendering, local UI, runtime audio playback, and permission UI.
- `apps/server`: WebSocket bridge between desktop events and the Python runtime.
- `packages/amadeus`: preferred agent turn path, memory, tools, runtime HTTP API, and future model/skills/harness boundaries.

## Current active modules

- `agent.py`: active preferred turn flow.
- `memory.py`: active SQLite-backed message history.
- `tools/`: active Python tool implementations and their public registry entrypoint.
- `audio.py`: active audio/TTS interface.
- `server.py`: active HTTP runtime.

## Current placeholder boundaries

These files exist as future module boundaries, but are not yet the main active implementation path:

- `model.py`
- `skills.py`
- `live2d.py`

## Current runtime behavior

- `agent.py` contains the real preferred turn logic today.
- The runtime loads recent SQLite history, saves user and assistant messages, makes the tool-decision call, executes Python tools, streams `assistant.delta`, emits `assistant.message`, and may emit `audio.tts-ready`.
- Tool permission requests are brokered through streamed `tool.permission.request` events plus `POST /tools/permission`.
- Audio is wired, but the default runtime still uses `NoopTtsProvider`, so Python TTS does not produce a real audio URL unless a real provider is added.

## Current Tools

Active tools are defined under `tools/` as Python handlers plus OpenAI-compatible `ToolSpec` metadata. Effective enablement and permission state are loaded from `../../configs/tools.yaml`.

| Tool | Permission | What it does |
| --- | --- | --- |
| `get_current_time` | `allow` | Returns the current date/time for a requested IANA timezone. It defaults to `Asia/Shanghai` and falls back to UTC for invalid timezones. |
| `roll_dice` | `ask` | Rolls one or more dice with bounded `sides` and `count`, returning individual rolls and the total. |
| `local_file_search` | `ask` | Searches workspace-relative filenames and small text files for a query, skipping generated/heavy directories and capping result count. |

The runtime layer around these tools adds behavior that tool handlers do not need to reimplement:

- `ToolRegistry` loads default specs and applies `configs/tools.yaml`.
- `ToolContext` carries session id, cwd, timeout, cooperative cancellation, and model-output size limits.
- `ToolResult` keeps the full tool output separately from the smaller `model_output` written back into model context.
- Tool execution records duration, stable failure codes, `tool.started` / `tool.finished` events, and persisted `tool.audit` records.
- Guardrails block repeated exact failures and repeated same-signature completed calls inside one turn.
- `local_file_search` has a per-tool model-output policy that keeps search metadata while limiting model-context result count and preview length.

## Adding A Tool

Adding a simple local tool is intentionally lightweight:

1. Add a Python handler in a focused module under `tools/`, such as `tools/reminders.py`.
   - Signature can be `handler(args)` for simple tools.
   - Use `handler(args, context)` if the tool needs `ToolContext`, for example to check `context.is_cancelled()` in a long loop.
   - Return a JSON-serializable `dict`. Return `{"error": "..."}` for expected tool-level failures.
2. Add a `ToolSpec` next to the handler and register it from `tools/__init__.py`.
   - `name` must match the function schema name.
   - `permission` should usually be `allow` for safe read-only low-risk tools, `ask` for local filesystem/network/user-visible actions, and `deny` only when disabled by default.
   - `schema` should be OpenAI-compatible function metadata with clear parameter descriptions.
3. Add or update the matching entry in `../../configs/tools.yaml`.
   - `enabled: true|false`
   - `permission: allow|ask|deny`
4. Add focused tests in `../../tests/test_tool_runtime.py` or `../../tests/test_python_agent_runtime.py`.
   - Test handler success/failure.
   - Test config permission behavior if the permission matters.
   - Add a per-tool model-output policy in `tool_runtime/registry.py` only if the result can become large or noisy.

For a simple read-only tool, this is usually a small change: one handler, one `ToolSpec`, one config entry, and one or two tests. Tools that touch the network, filesystem writes, subprocesses, credentials, or long-running work need more care: use `ask`, enforce workspace/path constraints, support cancellation if possible, and keep model output compact.

## Run

```bash
python packages/amadeus/server.py
```

Default endpoint:

```text
http://127.0.0.1:8790
```

## Current HTTP API

- `GET /health`
- `GET /tools/list`
- `POST /agent/turn`
- `POST /tools/execute`
- `POST /tools/permission`
- `GET /memory/count?sessionId=default`
- `GET /memory/messages?sessionId=default&limit=40`
- `POST /memory/messages`
- `POST /memory/reset`
- `POST /audio/speak`
- `GET /audio/files/{relativePath}`

## Notes

- `/agent/turn` returns an NDJSON event stream.
- Current test coverage is centered on `tests/test_python_agent_runtime.py`, which covers deterministic runtime behavior in `AgentRuntime` rather than full HTTP/bridge integration.
