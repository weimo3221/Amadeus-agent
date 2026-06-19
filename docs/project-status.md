# Project Status

Last updated: 2026-06-19

This document is the live progress tracker for Amadeus Agent. Use it as the source of truth for what is implemented now. `docs/roadmap.md` is the forward-looking plan.

## Current Goal

Build a desktop Live2D interactive agent with a local runtime, starting from a small working MVP and expanding toward character behavior, voice, memory, tools, and later long-running agent features.

## Current Snapshot

Amadeus is now a working desktop MVP with a Python-first turn path.

### Current Runtime Flow

Preferred path today:

1. Desktop renderer in `apps/desktop` sends `user.message` over WebSocket.
2. `apps/server` receives the event and first tries Python `POST /agent/turn`.
3. `packages/amadeus/agent.py` runs the turn in Python:
   - loads recent SQLite history
   - saves the user message
   - makes the tool-decision call
   - executes Python tools
   - emits runtime events
   - streams the final assistant reply
   - saves the assistant message
   - optionally emits `audio.tts-ready`
4. `apps/server` relays the NDJSON runtime events back to the desktop.
5. Desktop updates chat, tool state, permission UI, runtime audio playback, and Live2D behavior.

Fallback path today:

- If Python `/agent/turn` is unavailable, `apps/server` now reports `python_runtime_unavailable` by default.
- The older TypeScript model/tool turn loop has been removed.

### Done Now

- Project scaffold is in place under `amadeus-agent`.
- Desktop app MVP is running with Electron, Vite, transparent frameless window controls, Live2D stage, chat panel, debug controls, voice toggle, and lipsync MVP.
- Local runtime MVP is running in `apps/server` with HTTP health check and WebSocket events.
- DeepSeek/OpenAI-compatible chat path is connected and supports streaming assistant replies.
- Character behavior events can drive Live2D state, expression, motion, and pointer-following reactions.
- SQLite message memory is implemented in `data/amadeus.sqlite`.
- Desktop shows memory count, tool status, tool config status, voice status, visible chat messages, and has a Reset Session button.
- Tool calling is model-triggered through OpenAI-compatible `tools` / `tool_calls`, not keyword matching.
- Tool execution now goes through a formal registry with `allow`, `ask`, and `deny` metadata.
- `get_current_time` is registered as an `allow` tool.
- `roll_dice` is registered as an `ask` tool.
- `local_file_search` is implemented as an `ask` tool in the Python runtime.
- `configs/tools.yaml` is loaded at startup and controls effective tool enabled/permission state.
- Desktop diagnostics show the loaded tool permission state from the server.
- Tool definitions, schemas, registry creation, and config loading still exist in `packages/amadeus/tools.ts` for bridge diagnostics and development scaffolding.
- Python `/agent/turn` is wired as the preferred turn path.
- Python now owns the preferred model/tool/memory/behavior path for a turn:
  - loads OpenAI-compatible provider config from environment or `.env`
  - assembles recent SQLite message history
  - makes the tool-decision call
  - executes Python tools
  - writes user/assistant messages to SQLite
  - emits desktop-compatible runtime events
  - requests Python audio output after the assistant message
- Ask-tool permission requests cross the Python runtime boundary:
  - Python emits `tool.permission.request`
  - the TypeScript bridge relays it to desktop
  - desktop sends `tool.permission.response`
  - the bridge forwards it back to Python `/tools/permission`
- Python audio interface is wired for the first pass, and desktop prefers runtime-provided audio when it receives `audio.tts-ready`.
- Desktop still has Electron/browser `speechSynthesis` fallback and currently uses it most of the time because the Python audio runtime still defaults to `NoopTtsProvider` until a real TTS provider is configured.
- Python runtime parity tests are now wired through `npm test`.
  - missing API key returns a structured runtime error
  - simple turns persist user and assistant messages
  - `allow` tools execute without permission prompts
  - denied `ask` tools return a tool error to the model
  - tool config overrides apply to Python tool metadata
  - permission broker resolution behavior is covered
- Python runtime HTTP handler tests now cover the local sidecar boundary.
  - `/tools/list` exposes effective permission state and enabled schemas
  - `/tools/permission` returns unresolved for unknown request IDs
  - `/agent/turn` streams missing API key failures as desktop-compatible NDJSON events
- TypeScript bridge tests now cover the Python-first relay boundary.
  - `/agent/turn` NDJSON events are relayed to the desktop socket
  - Python runtime connection failures return `false` from the relay so the caller can decide whether to report an error or use the explicit fallback
  - malformed Python events emit desktop-compatible `error` events without dropping later valid events
  - unresolved permission responses are forwarded to Python `/tools/permission`
- Server-level WebSocket integration tests now cover the Python-first desktop event path.
  - `user.message` over WebSocket reaches Python `/agent/turn`
  - streamed Python runtime events are returned to the WebSocket client
  - `tool.permission.response` over WebSocket is forwarded to Python
- Desktop renderer harness tests now cover runtime UI behavior.
  - `server.hello` updates model, memory, connection, and tool config diagnostics
  - assistant deltas/messages update chat output and schedule speech fallback
  - `tool.permission.request` shows Allow / Deny UI and sends `tool.permission.response`
  - chat form submission sends `user.message` over the active socket
  - `audio.tts-ready` cancels speech fallback and plays runtime audio instead
  - `tool.finished` clears permission prompts and updates tool status
- Desktop Electron smoke coverage now builds the packaged desktop app, starts the Electron main process, and verifies that the renderer finishes loading.
- The legacy TypeScript fallback loop has been removed.
  - Python runtime failures now produce an explicit desktop error
  - `apps/server` no longer owns provider calls, tool execution, memory writes, or audio trigger logic for user turns
- Phase 7 ToolRuntime first slice is in place.
  - Python tool registry/config loading now lives under `packages/amadeus/tool_runtime`.
  - Agent tool execution dispatches through `ToolRegistry` instead of direct helpers.
  - Tool execution now returns structured `ToolResult` metadata with success state, duration, and stable failure codes.
  - Tool execution now emits `tool.audit` events and keeps in-process audit records for started/finished/denied/blocked/failed decisions.
  - Tool execution now has a first-pass timeout boundary and returns `tool_timeout` for slow tool calls.
  - `ToolContext` now carries a cooperative cancellation signal; pre-cancelled calls return `tool_cancelled`, and timeout sets the cancellation signal for context-aware tools.
  - Large successful tool outputs are compacted before being written back into model context, while full output remains available on `ToolResult`.
  - A per-turn `ToolLoopGuardrail` blocks repeated exact failing tool calls and repeated completed calls that do not make progress.
  - Unit tests cover registry config aliases, cancellation behavior, guardrail threshold behavior, agent-level repeated failure blocking, and agent-level no-progress blocking.
- Local GPT-SoVITS project and Vivian model weights have been located for the first concrete TTS provider test.
- Desktop shows inline Allow / Deny prompts for `ask` tools.
- `configs/tools.yaml` mirrors the current intended tool permissions.
- Typecheck, desktop build, allow-path WebSocket test, and deny-path WebSocket test have passed.

### Still Needed

- Expand Electron end-to-end coverage beyond the current startup smoke to cover Live2D loading and real user/runtime interactions.
- Continue shrinking TypeScript bridge scaffolding now that the legacy turn loop is gone.
- Add a real Python TTS provider so runtime audio becomes the practical default, not only the interface contract.
- Add a local Live2D model bundle under `models/live2d` so the app does not depend on remote model URLs.
- Improve lipsync from a timed mouth loop to audio-driven or phoneme-aware movement.
- Add more practical `ask` tools such as opening URLs or reminders.
- Add long-term memory beyond raw message history, such as user facts, preferences, summaries, and retrieval.
- Harden the Python-owned ToolRuntime with persisted audit records if needed, per-tool result policies, richer context propagation, and richer no-progress policies where needed.
- Turn placeholder runtime boundaries into real modules where needed:
  - `packages/amadeus/model.py`
  - `packages/amadeus/skills.py`
  - `packages/amadeus/live2d.py`
  - `packages/live2d-stage`

## Completed

### Phase 0: Project Skeleton

Status: complete.

- Created the monorepo-style structure.
- Added root package/config files and initial docs.
- Added draft config files in `configs`.

### Phase 1: Desktop Live2D Shell

Status: complete for MVP.

- Added Electron + Vite desktop app in `apps/desktop`.
- Implemented transparent frameless window, always-on-top behavior, minimize support, and pin/unpin controls.
- Added Live2D stage, debug controls, pointer-following, click reaction, and loading timeout.
- The current model is still loaded from a remote test URL.

### Phase 2: Local Agent Runtime

Status: complete for MVP.

- Added `apps/server` with `/health` and `/ws`.
- Added OpenAI-compatible streaming chat.
- Connected desktop chat UI to the local server.
- Added shared runtime event types in `packages/amadeus/events.ts`.

### Phase 3: Character Behavior Link

Status: complete for MVP.

- Connected assistant state and behavior events to Live2D expression and motion handling.
- Added expression and motion alias fallback behavior.
- Added debug controls that read available model capabilities.

### Phase 4: Voice and Lipsync

Status: complete for MVP.

- Added voice toggle and browser/Electron `speechSynthesis` playback.
- Added a simple timed mouth loop for speaking.
- Added desktop voice status diagnostics.

### Phase 5: Memory and Tools

Status: MVP memory, model-triggered tools, registry, config loading, and permission prompts complete.

- Added SQLite-backed message persistence using `node:sqlite`.
- Desktop now shows memory and tool feedback.
- Added model-triggered tool calling.
- Added the first formal tool registry and tool config loader.
- Added `allow` / `ask` / `deny` tool permissions.
- Added inline desktop Allow / Deny prompts.
- Extracted TypeScript tool metadata and config loading into `packages/amadeus/tools.ts`.
- Added `local_file_search` as the first practical project-search tool.

## In Progress

### Phase 6: Python Runtime Ownership

The second vertical slice is complete: Python runtime parity tests, Python HTTP handler tests, TypeScript bridge relay tests, server-level WebSocket integration tests, and desktop renderer harness tests are in place, and `npm test` now runs them.

Phase 7 is in progress. The first vertical slice is complete: Python tool registry/config loading has been extracted into `packages/amadeus/tool_runtime`, and the Python agent loop now applies repeated-failure and no-progress guardrails during tool execution.

## Completed Subphase

### Phase 7: ToolRuntime and Guardrails Foundation

Status: first slice complete.

- Added `packages/amadeus/tool_runtime`.
- Added `ToolRegistry` for:
  - loading default Python tool specs
  - applying `configs/tools.yaml`
  - preserving the legacy `time` alias for `get_current_time`
  - exposing permission state and enabled OpenAI-compatible schemas
  - dispatching tool execution through the selected `ToolSpec`
- Moved Python tool config parsing out of `agent.py`.
- Updated `AgentRuntime` to depend on `ToolRegistry` instead of owning tool spec loading.
- Added `ToolLoopGuardrail` for repeated exact failed tool calls and repeated completed no-progress calls inside a single turn.
- Wired the guardrail into Python tool execution before running each tool call.
- Added first-pass `ToolContext` / `ToolResult` objects for structured execution metadata.
- `tool.finished` events can now include tool duration and stable failure codes.
- Added first-pass `tool.audit` events and in-process audit records for tool started/finished/denied/blocked/failed decisions.
- Added first-pass tool timeout handling with structured `tool_timeout` failures.
- Added first-pass cooperative cancellation handling with structured `tool_cancelled` failures.
- Added first-pass result preview/compression so large successful tool outputs do not flood model context.
- Added first-pass no-progress loop detection with structured `no_progress_loop` failures.
- Added focused tests for:
  - registry config alias behavior
  - structured tool execution results
  - structured timeout failures
  - structured cancellation failures and timeout cancellation signaling
  - model-context compression for large tool results
  - audit event/log behavior for allow, deny, and guardrail paths
  - guardrail threshold blocking
  - agent-level repeated failing tool call blocking
  - agent-level repeated no-progress tool call blocking
- Verified:
  - `npm test`
  - Python source compile check
  - `npm run typecheck`

What is already done:

- Python `packages/amadeus/agent.py` is the preferred owner of the turn path.
- Python `POST /agent/turn` streams NDJSON runtime events.
- Python reads/writes SQLite message memory for the preferred path.
- Python owns tool decision and Python tool execution for the preferred path.
- Python permission brokering is wired through `tool.permission.request` and `/tools/permission`.
- `npm test` covers deterministic Python runtime behavior, local Python HTTP handlers, TypeScript bridge relay behavior, server-level WebSocket integration behavior, and desktop renderer runtime UI behavior.

What is not done yet:

- `apps/server` no longer contains the legacy TypeScript fallback loop.
- Test coverage now includes Python runtime units, local Python HTTP handlers, TypeScript bridge relay behavior, server-level WebSocket integration behavior, desktop renderer runtime UI behavior, and an Electron startup smoke. Full Live2D/interaction end-to-end coverage is still missing.
- The active provider code still lives inline in `packages/amadeus/agent.py`; `model.py` is still a future abstraction boundary.
- `skills.py` and `live2d.py` are still placeholder boundaries rather than mature runtime modules.
- `packages/live2d-stage` is still not the real desktop implementation package; current Live2D behavior lives in `apps/desktop/src/renderer/main.ts`.

## Next Recommended Phase

### Phase 7 Continued: ToolRuntime Hardening

Goal: turn the first ToolRuntime slice into a production-grade tool execution layer.

Planned tasks:

- Extend `ToolContext` so tools receive audit metadata explicitly.
- Extend cancellation beyond the current cooperative signal if future tools need stronger process-level interruption.
- Persist audit records beyond the current in-process runtime log if longer-term diagnostics are needed.
- Extend result preview/compression with per-tool policies if needed.
- Extend no-progress detection with richer semantic policies if needed.
- Keep expanding Electron end-to-end coverage on the Python path before removing remaining TypeScript bridge scaffolding.
- Keep GPT-SoVITS provider work parked until its pretrained base models are installed.

The broader upgrade plan is documented in `docs/agent-maturity-upgrade-plan.md`.

## Later Phases

### Phase 7: ToolRuntime and Guardrails

In progress.

Notes:

- The first Python `tool_runtime` slice exists with registry/config loading, permission-aware schema selection, dispatch, cooperative cancellation, repeated-failure guardrails, and first-pass no-progress guardrails.
- The remaining work is the mature runtime layer: persisted audit records if needed, richer context propagation, per-tool result policies, and richer semantic no-progress guardrails.

### Phase 8: Agent Memory Optimization

Not started.

- Add conversation summary storage.
- Add user profile facts and preferences.
- Add SQLite FTS session search.
- Feed summaries and profile facts into model context.

### Phase 9: Live2D and Audio Harnesses

Not started.

- Turn Live2D and audio into installable runtime harnesses instead of ad hoc runtime/renderer coupling.
- Add `configs/harnesses.yaml`.
- Add capability feedback from desktop to runtime.

### Phase 11: Proactive Agent

Not started.

- Add scheduled reminders.
- Add daily brief.
- Add idle-time check-ins.
- Add background task state display.

### Phase 12: Advanced Agent Features

Not started.

- Add MCP bridge.
- Add sub-agent/task worker abstraction.
- Add context compression.
- Add long-task planning.
- Add human approval checkpoints.
- Add provider/harness profiles.
- Add eval coverage for tool choice, permissions, memory, Live2D, audio, and guardrails.

## Known Issues

- The desktop app currently uses a remote Live2D test model URL. A local model should be added under `models/live2d`.
- Live2D behavior mapping is currently alias-based and depends on the available motions/expressions in the loaded model.
- The current Live2D model and Cubism runtime are loaded from remote URLs, so network failures can still prevent the model from appearing.
- TTS currently falls back to browser/Electron `speechSynthesis` in normal practice because the Python audio runtime still defaults to `NoopTtsProvider`.
- GPT-SoVITS integration is blocked until required pretrained base models are downloaded into `D:\OtherProject\LearningLLM\GPT-SoVITS\GPT_SoVITS\pretrained_models`.
- Lipsync is currently a timed mouth loop, not phoneme-accurate.
- SQLite uses Node 24's experimental built-in `node:sqlite`, so Node prints an experimental warning at server startup.
- Current tests cover Python runtime-unit behavior, local HTTP handlers, TypeScript bridge relay behavior, server-level WebSocket integration behavior, desktop renderer runtime UI behavior, and Electron startup smoke behavior. Full Live2D/interaction end-to-end coverage is still missing.
- Placeholder boundaries still need real implementations or cleanup: `model.py`, `skills.py`, `live2d.py`, and `packages/live2d-stage`.

## Useful Commands

```bash
npm install
npm test
npm run test:e2e
npm run typecheck
npm --workspace apps/server run dev
npm --workspace apps/desktop run dev
npm run dev
```

## Local Runtime

Server:

```text
http://127.0.0.1:8788
ws://127.0.0.1:8788/ws
```

Python runtime:

```text
http://127.0.0.1:8790
```

Environment:

```text
OPENAI_BASE_URL=https://api.deepseek.com
OPENAI_MODEL=deepseek-v4-flash
VITE_AGENT_WS_URL=ws://127.0.0.1:8788/ws
```

The API key is stored only in local `.env`, which is ignored by git.
