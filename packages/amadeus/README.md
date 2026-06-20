# Amadeus Runtime

Python-side Amadeus runtime.

This package is intended to become the real agent core. The surrounding TypeScript apps are moving toward transport and device-adapter roles:

- `apps/desktop`: Electron window, Live2D rendering, local UI, runtime audio playback, and permission UI.
- `apps/server`: WebSocket bridge between desktop events and the Python runtime.
- `packages/amadeus`: preferred agent turn path, memory, tools, runtime HTTP API, and future model/skills/harness boundaries.

## Current active modules

- `agent.py`: active preferred turn flow.
- `memory.py`: active SQLite-backed message history, FTS search, and conversation summaries.
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
| `read_memory` | `allow` | Reads stable Markdown memory for agent facts (`MEMORY.md`) or user preferences (`USER.md`). |
| `update_memory` | `ask` | Adds, replaces, or removes bounded stable memory entries without allowing whole-file rewrites. |
| `search_memory` | `allow` | Searches prior SQLite conversation memory for earlier messages, remembered preferences, past decisions, or conversation history. |
| `search_memory_items` | `allow` | Searches durable structured `user` / `agent` / `project` memory facts stored in SQLite. |
| `memory_add` | `ask` | Adds one durable structured memory fact after user approval, with duplicate detection and source-session metadata. |
| `memory_replace` | `ask` | Replaces one active durable structured memory fact after user approval. |
| `memory_forget` | `ask` | Deletes one active durable structured memory fact after user approval. |
| `search_files` | `ask` | Searches workspace-relative filenames and/or small text file contents using `target: all | files | content`, skipping generated/heavy directories and capping result count. |
| `read_file` | `ask` | Reads an explicit, line-numbered window from a workspace-relative UTF-8 text file after search; images, PDFs, binaries, and unknown extensions return structured `kind/supported/hint` metadata instead of being decoded. |
| `patch` | `ask` | Applies a safe single-file text replacement inside the workspace, requiring a unique `oldText` match unless `replaceAll=true`, and returns a unified diff preview. |
| `write_file` | `ask` | Creates or fully overwrites a workspace-relative UTF-8 text file, refusing accidental overwrites unless `overwrite=true`, and returns size/line metadata plus a diff preview. |

The runtime layer around these tools adds behavior that tool handlers do not need to reimplement:

- `ToolRegistry` loads default specs and applies `configs/tools.yaml`.
- `ToolContext` carries session id, cwd, optional memory store, turn/tool-call ids, permission decision metadata, audit metadata, timeout, cooperative cancellation, and model-output size limits.
- `ToolResult` keeps the full tool output separately from the smaller `model_output` written back into model context.
- Tool execution records duration, stable failure codes, `tool.started` / `tool.finished` events, and persisted `tool.audit` records.
- Guardrails block repeated exact failures and repeated same-signature completed calls inside one turn.
- Stable memory is stored as auditable Markdown files under `data/memory/MEMORY.md` and `data/memory/USER.md`, then injected into the frozen system prompt at runtime startup.
- Each turn prefetches up to three relevant prior session messages and injects them into the API-only current user message as a sanitized `<memory-context>` block; the block is not persisted.
- Conversation summaries are persisted in SQLite through `GET /memory/summary` and `POST /memory/summary`, injected as reference-only context, and refreshed by threshold-based compaction or manual `POST /memory/compact`.
- Structured `memory_items` store durable `user` / `agent` / `project` facts in SQLite, expose explicit HTTP add/list/delete APIs, are searchable through the read-only `search_memory_items` tool, can be added/replaced/forgotten through approval-gated tools, and inject a small active set into model context as reference-only `<memory-items>`.
- Memory review candidates are persisted in SQLite as a human-controlled queue. `POST /memory/review/run` asks the provider to propose candidates from recent messages, `GET/POST /memory/review/candidates` lists or creates candidates, `POST /memory/review/accept` promotes a pending candidate into `memory_items`, and `POST /memory/review/reject` rejects it without writing durable memory. Rejected candidates suppress later identical suggestions for the same session/scope/content.
- Automatic memory review can run after a completed turn once the message threshold is met. It is cooldown-gated and only writes pending review candidates, never durable memory items.
- `search_memory` has a per-tool model-output policy that keeps match metadata while limiting model-context result count and snippet length.
- `search_memory_items` has a per-tool model-output policy that keeps structured fact metadata while limiting model-context item count and content length.
- `search_files` has a per-tool model-output policy that keeps search metadata while limiting model-context result count and preview length.
- `read_file` does not use hidden runtime compression. It returns a caller-controlled `startLine` / `lineLimit` text window with line numbers, `totalLines`, and `hasMore` so the model can continue reading explicitly. Non-text files are identified as `image`, `pdf`, `binary`, or `unknown` and return an unsupported response with a next-tool hint.
- `patch` follows the Hermes-style edit path: exact `oldText` / `newText`, unique-match default, optional `replaceAll`, restricted generated directories, UTF-8 text-only writes, and diff output for review.
- `write_file` is the whole-file write path: new UTF-8 text files by default, explicit `overwrite=true` for replacement, restricted generated directories, text-extension checks, size limits, parent directory creation inside the workspace, and diff output for review.

`local_file_search` is still registered as a disabled compatibility alias for older calls, but new schemas and prompts should use `search_files`.

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
- `GET /tools/list`：Python-owned source for effective tool permission state and enabled schemas.
- `GET /tools/audit?sessionId=default&toolName=search_files&decision=finished&ok=true&limit=100`：query persisted tool audit records for diagnostics.
- `POST /agent/turn`
- `POST /tools/execute`：compatibility execution endpoint for direct tool diagnostics; normal turns execute tools inside Python `AgentRuntime`.
- `POST /tools/permission`
- `GET /memory/count?sessionId=default`
- `GET /memory/messages?sessionId=default&limit=40`
- `GET /memory/search?sessionId=default&query=hello&limit=10`
- `GET /memory/items?scope=user&query=preference&limit=20`
- `GET /memory/summary?sessionId=default`
- `POST /memory/messages`
- `POST /memory/items`
- `POST /memory/items/delete`
- `POST /memory/summary`
- `POST /memory/compact`
- `POST /memory/reset`
- `POST /audio/speak`
- `GET /audio/files/{relativePath}`

## Notes

- `/agent/turn` returns an NDJSON event stream.
- Current test coverage is centered on `tests/test_python_agent_runtime.py`, which covers deterministic runtime behavior in `AgentRuntime` rather than full HTTP/bridge integration.
