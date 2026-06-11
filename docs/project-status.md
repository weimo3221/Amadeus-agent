# Project Status

Last updated: 2026-06-11

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

- If Python `/agent/turn` is unavailable, `apps/server/src/index.ts` still contains the older TypeScript turn loop for provider calls, SQLite writes, tool execution, permission prompts, behavior events, and Python `/audio/speak` integration.

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
- `local_file_search` is implemented as an `ask` tool in both the Python runtime and the TypeScript fallback.
- `configs/tools.yaml` is loaded at startup and controls effective tool enabled/permission state.
- Desktop diagnostics show the loaded tool permission state from the server.
- Python `/agent/turn` is wired as the preferred turn path.
- Python now owns the preferred model/tool/memory/behavior path for a turn.
- Ask-tool permission requests now cross the Python runtime boundary:
  - Python emits `tool.permission.request`
  - the TypeScript bridge relays it to desktop
  - desktop sends `tool.permission.response`
  - the bridge forwards it back to Python `/tools/permission`
- Python runtime parity tests are wired through `npm test`.
- Python audio interface is wired for the first pass, and desktop prefers runtime-provided audio when it receives `audio.tts-ready`.
- Desktop still has Electron/browser `speechSynthesis` fallback and currently uses it most of the time because the Python audio runtime still defaults to `NoopTtsProvider` until a real TTS provider is configured.

### Still Needed

- Add HTTP relay and desktop WebSocket integration tests for the Python-first path.
- Remove the legacy TypeScript turn loop after parity confidence is high enough.
- Add a real Python TTS provider so runtime audio becomes the practical default, not only the interface contract.
- Add a local Live2D model bundle under `models/live2d` so the app does not depend on remote model URLs.
- Improve lipsync from a timed mouth loop to audio-driven or phoneme-aware movement.
- Add more practical `ask` tools such as opening URLs or reminders.
- Add long-term memory beyond raw message history, such as user facts, preferences, summaries, and retrieval.
- Formalize a Python-owned ToolRuntime with guardrails, audits, and timeout/cancellation handling.
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

Status: in progress.

What is already done:

- Python `packages/amadeus/agent.py` is the preferred owner of the turn path.
- Python `POST /agent/turn` streams NDJSON runtime events.
- Python reads/writes SQLite message memory for the preferred path.
- Python owns tool decision and Python tool execution for the preferred path.
- Python permission brokering is wired through `tool.permission.request` and `/tools/permission`.
- `npm test` covers deterministic Python runtime behavior.

What is not done yet:

- `apps/server` still contains the full legacy TypeScript fallback loop.
- The current Python test coverage is runtime-unit coverage only; there are still no HTTP endpoint tests, bridge relay tests, or desktop WebSocket integration tests.
- The active provider code still lives inline in `packages/amadeus/agent.py`; `model.py` is still a future abstraction boundary.
- `skills.py` and `live2d.py` are still placeholder boundaries rather than mature runtime modules.
- `packages/live2d-stage` is still not the real desktop implementation package; current Live2D behavior lives in `apps/desktop/src/renderer/main.ts`.

## Next Recommended Phase

### Phase 6 Completion: Python Runtime Ownership Cleanup

Goal: finish the Python-first migration by proving parity and shrinking the TypeScript fallback path.

Planned tasks:

- Add focused HTTP endpoint tests for the Python runtime.
- Add bridge-level tests for relaying Python NDJSON events through `apps/server`.
- Add desktop/WebSocket integration coverage for the permission round-trip and normal chat flow.
- Keep Python `/agent/turn` as the source of truth for normal user turns.
- Remove the legacy TypeScript tool/model loop after parity tests cover the Python path.
- Keep GPT-SoVITS provider work parked until its pretrained base models are installed.

The broader upgrade plan is documented in `docs/agent-maturity-upgrade-plan.md`.

## Later Phases

### Phase 7: ToolRuntime and Guardrails

Not started as a formal runtime phase.

Notes:

- Tool registry, config loading, and permission-aware tools already exist.
- The remaining work is the mature runtime layer: Python-owned ToolSpec/ToolContext/ToolResult, timeout/cancellation handling, audit records, and repeated-failure / no-progress guardrails.

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
- Current Python tests only cover runtime-unit behavior. HTTP relay and desktop integration coverage are still missing.
- Placeholder boundaries still need real implementations or cleanup: `model.py`, `skills.py`, `live2d.py`, and `packages/live2d-stage`.

## Useful Commands

```bash
npm install
npm test
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
