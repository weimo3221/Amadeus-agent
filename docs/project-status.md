# Project Status

Last updated: 2026-06-11

This document is the live progress tracker for Amadeus Agent. Update it whenever a project phase is completed or the immediate next step changes.

## Current Goal

Build a desktop Live2D interactive agent with a local runtime, starting from a small working MVP and expanding toward character behavior, voice, memory, and tools.

## Current Snapshot

### Done Now

- Project scaffold is in place under `amadeus-agent`.
- Desktop app MVP is running with Electron, Vite, transparent frameless window controls, Live2D stage, chat panel, debug controls, voice toggle, and lipsync MVP.
- Local runtime MVP is running in `apps/server` with HTTP health check and WebSocket events.
- DeepSeek/OpenAI-compatible chat path is connected and supports streaming assistant replies.
- Character behavior events can drive Live2D state, expression, motion, and pointer-following reactions.
- SQLite message memory is implemented in `data/amadeus.sqlite`.
- Desktop shows memory count, tool status, voice status, visible chat messages, and has a Reset Session button.
- Tool calling is model-triggered through OpenAI-compatible `tools` / `tool_calls`, not keyword matching.
- Server tool execution now goes through a formal `toolRegistry`.
- Old keyword-triggered time helper and direct-answer helper path have been removed.
- Tool permissions now support `allow`, `ask`, and `deny` metadata.
- `get_current_time` is registered as an `allow` tool.
- `roll_dice` is registered as the first low-risk `ask` tool.
- Server loads effective tool enabled/permission settings from `configs/tools.yaml` at startup.
- Desktop diagnostics show the loaded tool permission state from the server.
- Tool definitions, schemas, registry creation, and config loading now live in `packages/amadeus/tools.ts`.
- Python runtime sidecar scaffolding exists under `packages/amadeus`.
- Python audio interface is now wired for the first pass. Current playback still falls back to Electron/browser `speechSynthesis` until a real TTS provider is configured.
- Python `/agent/turn` is now wired for the first pass. The TypeScript server prefers the Python runtime for user turns and keeps the previous TypeScript loop as a fallback when the Python runtime is unavailable.
- Python now owns the preferred model/tool/memory/behavior path for a turn:
  - loads OpenAI-compatible provider config from environment or `.env`
  - assembles recent SQLite message history
  - makes the tool-decision call
  - executes Python tools
  - writes user/assistant messages to SQLite
  - emits desktop-compatible runtime events
  - requests Python audio output after the assistant message
- Ask-tool permission requests can now cross the Python runtime boundary: Python emits `tool.permission.request`, the TypeScript bridge relays it to desktop, and desktop responses are forwarded back to Python `/tools/permission`.
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
- Phase 7 ToolRuntime first slice is in place.
  - Python tool registry/config loading now lives under `packages/amadeus/tool_runtime`.
  - Agent tool execution dispatches through `ToolRegistry` instead of direct helpers.
  - A per-turn `ToolLoopGuardrail` blocks repeated exact failing tool calls.
  - Unit tests cover registry config aliases, guardrail threshold behavior, and agent-level repeated failure blocking.
- Local GPT-SoVITS project and Vivian model weights have been located for the first concrete TTS provider test.
- Desktop shows inline Allow / Deny prompts for `ask` tools.
- `configs/tools.yaml` mirrors the current intended tool permissions.
- `local_file_search` is implemented as a practical `ask` tool in both the TypeScript fallback and Python runtime.
- Typecheck, desktop build, allow-path WebSocket test, and deny-path WebSocket test have passed.

### Still Needed

- Add a local Live2D model bundle under `models/live2d` so the app does not depend on remote model URLs.
- Replace browser `speechSynthesis` MVP with Python-owned audio output and a stronger TTS option if better voice quality is needed.
- Install GPT-SoVITS pretrained base models before testing Vivian TTS; the local `GPT_SoVITS/pretrained_models` directory is currently missing required base assets.
- Improve lipsync from timed mouth movement to audio-driven or phoneme-aware movement.
- Add more practical `ask` tools after local file search, such as opening URLs or reminders.
- Add long-term memory beyond raw message history, such as user facts, preferences, and summaries.
- Add proactive features later: reminders, daily brief, idle check-ins, and background task state.
- Add advanced agent capabilities later: MCP bridge, long-task planning, context compression, and human approval checkpoints.

## Completed

### Phase 0: Project Skeleton

Status: complete.

- Created `amadeus-agent` project directory.
- Created monorepo-style structure:
  - `apps/desktop`
  - `apps/server`
  - `packages/amadeus`
  - `packages/live2d-stage`
  - `configs`
  - `docs`
  - `models/live2d`
- Added root `package.json`.
- Added `.gitignore`.
- Added `.env.example`.
- Added initial design docs:
  - `docs/architecture.md`
  - `docs/roadmap.md`
  - `docs/event-protocol.md`
  - `docs/implementation-notes.md`
- Added config drafts:
  - `configs/character.yaml`
  - `configs/providers.yaml`
  - `configs/tools.yaml`

### Phase 1: Desktop Live2D Shell

Status: complete for MVP.

- Added Electron + Vite desktop app in `apps/desktop`.
- Implemented transparent frameless desktop window.
- Implemented always-on-top behavior.
- Added Pin and Close controls.
- Added Live2D stage container.
- Added default remote Live2D test model URL through `VITE_LIVE2D_MODEL_URL`.
- Added fallback status message when Live2D loading fails.
- Added pointer-following head movement.
- Added click motion trigger.
- Added basic chat panel UI.
- Verified:
  - `npm install`
  - `npm run typecheck`
  - `npm --workspace apps/desktop run build`
  - Electron dev window launch

### Phase 2: Local Agent Runtime

Status: complete for MVP.

- Added `apps/server`.
- Added local HTTP health endpoint at `/health`.
- Added WebSocket endpoint at `/ws`.
- Added DeepSeek/OpenAI-compatible streaming chat.
- Added server-side session history in memory.
- Added basic system prompt for Amadeus.
- Added runtime events:
  - `server.hello`
  - `assistant.state`
  - `assistant.delta`
  - `assistant.message`
  - `character.behavior`
  - `error`
- Added shared event types, now located at `packages/amadeus/events.ts`.
- Connected desktop chat UI to local WebSocket server.
- Desktop now sends `user.message`.
- Desktop now streams `assistant.delta` into the chat panel.
- Verified WebSocket to DeepSeek path with test prompt:
  - Prompt: `用一句中文介绍你自己`
  - Response: `我是Amadeus，您的桌面Live2D伴侣，随时准备为您提供简洁实用的帮助。`
- Verified:
  - `npm run typecheck`
  - `npm --workspace apps/desktop run build`

### Phase 3: Character Behavior Link

Status: complete for MVP.

- Added a renderer-side Live2D behavior controller.
- Stored the loaded Live2D model in a controller instead of keeping it as a local-only variable.
- Connected `assistant.state` events to Live2D behavior:
  - `idle`: neutral expression and idle motion
  - `thinking`: focused/serious behavior
  - `speaking`: smile/talk behavior
  - `error`: confused/error behavior
- Connected `character.behavior` events from the server to the Live2D controller.
- Added motion aliases so behavior names such as `think`, `talk`, `nod`, and `shake_head` can fall back to model-specific groups such as `TapBody`, `FlickHead`, and `Idle`.
- Added expression aliases so behavior names such as `smile`, `serious`, `confused`, and `curious` can fall back safely when a model does not define that exact expression.
- Added safe fallbacks: missing motions or expressions are ignored instead of crashing the UI.
- Updated pointer-following to go through the controller.
- Updated click reaction to go through the controller.
- Added a small debug panel on the Live2D stage for manually testing:
  - assistant state
  - expression
  - motion
- Improved the debug panel so expression and motion dropdowns are populated from the loaded Live2D model's declared capabilities instead of hard-coded guesses.
- Added a capability summary in the debug panel, for example `N expressions, M motion groups`.
- Kept behavior alias fallback for runtime events while making manual debug selections use real model names directly.
- Fixed a renderer startup risk caused by importing `MotionPriority` from the package root; the desktop app now uses the numeric force priority to avoid pulling an extra runtime entry.
- Added a 15 second Live2D model loading timeout so the UI no longer stays on `Loading Live2D model...` forever when the model or Cubism runtime cannot load.
- Fixed the Electron preload path from `out/preload/index.js` to `out/preload/index.mjs`, which restores renderer access to `window.amadeus` and makes window controls work.
- Clarified the top-right window control:
  - `Unpin`: stop keeping the window always on top
  - `Pin`: keep the window always on top again
- Added a `Minimize` titlebar button backed by Electron IPC.
- Updated minimize behavior to cancel always-on-top before minimizing, and explicitly enabled `minimizable` plus taskbar visibility for the frameless desktop window.
- Verified:
  - `npm run typecheck`
  - `npm --workspace apps/desktop run build`

### Phase 4: Voice and Lipsync

Status: complete for MVP.

- Added a `Voice On` / `Voice Off` titlebar toggle.
- Added browser/Electron `speechSynthesis` based TTS playback for completed assistant replies.
- Auto-detects Chinese text and uses `zh-CN`; otherwise uses `en-US`.
- Cancels current speech when a new user message is sent.
- Added a simple mouth loop that drives Live2D `ParamMouthOpenY` while speech is active.
- Stops mouth movement when speech ends or errors.
- Added a voice status line in the chat panel for diagnosing local speech state.
- Keeps the active `SpeechSynthesisUtterance` referenced while speaking to avoid premature cleanup.
- Waits for system voices through `voiceschanged`, selects a matching voice when available, and calls `speechSynthesis.resume()` after queueing speech.
- Keeps this as a local MVP without introducing a paid/cloud TTS provider yet.
- Verified:
  - `npm run typecheck`
  - `npm --workspace apps/desktop run build`

### Phase 5: Memory and Tools

Status: MVP memory, model-triggered tools, registry, and permission prompts complete.

- Added SQLite-backed message persistence using Node 24's built-in `node:sqlite`.
- Database path: `data/amadeus.sqlite`.
- Added `messages` table for persisted user and assistant messages.
- Switched the WebSocket session to a stable `default` session so conversation history can be loaded after server restarts.
- Server now loads the most recent persisted messages into LLM context.
- `session.reset` now clears persisted messages for the session.
- Added the first safe local current-time tool, which was later moved into model-triggered tool calling:
  - exposes current date/time through the server runtime
  - now answers through the LLM after a tool result is returned
  - emits `tool.started` and `tool.finished`
  - persists the user question and final assistant answer
- Verified WebSocket time tool path:
  - Prompt: `现在几点？`
  - Response: `现在是 2026年5月31日星期日 12:39:51。`
- Verified:
  - `npm run typecheck`
  - `npm --workspace apps/desktop run build`

## In Progress

Phase 6 is in progress. The first vertical slice is complete: Python `/agent/turn` is wired as the preferred path, while the legacy TypeScript agent loop remains as a fallback.

The second vertical slice is complete: Python runtime parity tests and Python HTTP handler tests are in place, and `npm test` now runs them.

Phase 7 is in progress. The first vertical slice is complete: Python tool registry/config loading has been extracted into `packages/amadeus/tool_runtime`, and the Python agent loop now applies a simple repeated-failure guardrail during tool execution.

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
- Added `ToolLoopGuardrail` for repeated exact failed tool calls inside a single turn.
- Wired the guardrail into Python tool execution before running each tool call.
- Added focused tests for:
  - registry config alias behavior
  - guardrail threshold blocking
  - agent-level repeated failing tool call blocking
- Verified:
  - `npm test`
  - Python source compile check
  - `npm run typecheck`

## Completed Subphase

### Phase 5 Continued: Memory UI and Tool Feedback

Status: complete for MVP.

- Added `memoryMessages` to `server.hello`.
- Desktop shows memory status, for example `Memory: 8 messages`.
- Added `memory.updated` server event so the desktop memory count updates after messages are persisted, not only on connect/reset.
- Desktop shows tool activity:
  - `Tool running: ...`
  - `Tool finished: ...`
- Added `Reset` button in the chat panel.
- Reset clears the visible chat, stops speech/lipsync, resets tool status, sends `session.reset`, and updates memory status after server confirmation.
- Server responds to reset with a fresh `server.hello` containing `memoryMessages: 0`.
- Increased the desktop chat panel height so user and assistant messages remain visible after adding Memory/Tool/Voice status rows.
- Verified memory update flow over WebSocket:
  - before message: `server.hello:10`
  - after message persistence: `memory.updated:12`
- Verified reset flow over WebSocket:
  - before reset: `server.hello:8`
  - after reset: `server.hello:0`
- Verified:
  - `npm run typecheck`
  - `npm --workspace apps/desktop run build`

## Completed Subphase

### Phase 5 Continued: Tool Calling Refactor

Status: complete for MVP.

- Defined an OpenAI-compatible `get_current_time` tool schema.
- Added a non-streaming model decision call with `tools` and `tool_choice: auto`.
- Parses `tool_calls` from the model response.
- Executes `get_current_time` locally on the server.
- Emits existing desktop-visible `tool.started` and `tool.finished` events.
- Sends tool result back into the chat context as a `role: tool` message.
- Streams the model's final natural-language answer to the desktop.
- Removed the active keyword-trigger branch from user-message handling.
- Strengthened the system prompt so current time/date questions must use `get_current_time`.
- Verified model-triggered tool call over WebSocket:
  - Prompt: `请用工具告诉我现在几点？`
  - Event flow includes `tool.started` and `tool.finished`
  - Response: `工具查询完毕，当前时间是：**2026年5月31日星期日 13:02:46**。`
- Verified:
  - `npm run typecheck`
  - `npm --workspace apps/desktop run build`

## Completed Subphase

### Phase 5 Continued: Tool Registry Cleanup

Status: complete for MVP.

- Added a `toolRegistry` object in the server runtime.
- Registered `get_current_time` with:
  - OpenAI-compatible schema
  - display name
  - execution handler
- `tools` sent to the model are now derived from the registry.
- `executeToolCall` now dispatches by `toolRegistry[toolName]`.
- Removed the old keyword-trigger helper and old direct-answer time helper path.
- Verified registry-dispatched tool call over WebSocket:
  - Event flow includes `tool.started` and `tool.finished`
  - Response: `工具查询结果：当前时间是 **2026年5月31日星期日 13:14:43**。`
- Verified:
  - `npm run typecheck`
  - `npm --workspace apps/desktop run build`

## Completed Subphase

### Phase 5 Continued: Permission-Aware Tools

Status: complete for MVP.

- Added `allow`, `ask`, and `deny` permission metadata to tool registry entries.
- `get_current_time` remains `allow`, so current-time questions can run without interrupting the user.
- Added `roll_dice` as the first low-risk `ask` tool.
- Updated `configs/tools.yaml` to mirror the current enabled tools and permission levels.
- Server now blocks disabled or denied tools before execution.
- Server now emits `tool.permission.request` before running an `ask` tool.
- Desktop now displays an inline permission prompt with `Allow` and `Deny` buttons.
- Desktop sends `tool.permission.response` back to the runtime.
- Denied or timed-out permission requests return a tool error to the model instead of executing.
- Permission requests time out after 30 seconds.
- Verified allow flow over WebSocket:
  - Prompt: `请用工具帮我掷2个6面骰子，然后告诉我结果。`
  - Event flow includes `tool.started`, `tool.permission.request`, and `tool.finished`.
- Verified deny flow over WebSocket:
  - Prompt: `请用工具帮我掷1个6面骰子。`
  - Event flow includes `tool.permission.request` followed by a failed `tool.finished`.
- Verified:
  - `npm run typecheck`
  - `npm --workspace apps/desktop run build`

## Completed Subphase

### Phase 5 Continued: Tool Config Loader

Status: complete for MVP.

- Added a small server-side config loader for `configs/tools.yaml`.
- Applies configured `enabled` and `permission` values before exposing tools to the model.
- Supports the legacy `time` config key as an alias for the registered `get_current_time` tool.
- Validates unknown tool names, invalid boolean values, invalid permission values, and duplicate aliases with startup warnings.
- Adds loaded tool permission state to `server.hello`.
- Desktop shows loaded tool state in the diagnostics area, for example `Tools: get_current_time allow, roll_dice ask`.
- Verified:
  - `npm run typecheck`
  - `npm --workspace apps/desktop run build`

## Completed Subphase

### Phase 5 Continued: Tools Package Extraction

Status: complete for first pass.

- Added TypeScript bridge exports inside `@amadeus-agent/amadeus`.
- Moved tool types, default registry creation, `get_current_time`, `roll_dice`, config loading, enabled schema selection, and permission-state projection into `packages/amadeus/tools.ts`.
- Updated `apps/server` to consume `@amadeus-agent/amadeus/tools` while keeping WebSocket permission prompts and runtime execution flow in the server app.
- Updated root typecheck to include `packages/amadeus`.
- Verified:
  - `npm install`
  - `npm run typecheck`
  - server restart and `/health`

## Completed Subphase

### Phase 5 Continued: Python Runtime Foundation

Status: scaffolded.

- Added `packages/amadeus` as the local Python runtime sidecar.
- Added peer runtime modules for `agent`, `memory`, `model`, `tools`, `skills`, `live2d`, and `audio`.
- Added `GET /health`, `POST /tools/execute`, and memory endpoints.
- Implemented Python versions of `get_current_time` and `roll_dice`.
- Added SQLite-backed Python memory store matching the existing `messages` table.
- Added optional Python backend execution in `packages/amadeus/tools.ts`.
- Server now defaults tool execution to `AMADEUS_PYTHON_RUNTIME_URL`, with `http://127.0.0.1:8790` as the default local sidecar URL.
- Root `npm run dev` now starts Python runtime, server, and desktop together.
- Verified:
  - `npm run typecheck`
- Not yet verified in this shell:
  - Python process execution was blocked by a local sandbox spawn refresh during this session.

## Completed Subphase

### Phase 5 Continued: GPT-SoVITS Provider Discovery

Status: environment checked, provider not wired yet.

- Located the local GPT-SoVITS project at `D:\OtherProject\LearningLLM\GPT-SoVITS`.
- Confirmed the API entrypoint exists at `api_v2.py`.
- Confirmed the Vivian fine-tuned GPT/SoVITS weights exist:
  - Chinese GPT: `D:\OtherProject\LearningLLM\dataset\薇薇安_zh\薇薇安-e10.ckpt`
  - Chinese SoVITS: `D:\OtherProject\LearningLLM\dataset\薇薇安_zh\薇薇安_e10_s1040_l32.pth`
  - English GPT: `D:\OtherProject\LearningLLM\dataset\薇薇安_en\薇薇安-e10.ckpt`
  - English SoVITS: `D:\OtherProject\LearningLLM\dataset\薇薇安_en\薇薇安_e10_s1010_l32.pth`
- Confirmed each language has one reference wav under `reference_audios`.
- Checked GPT-SoVITS startup log: current output only shows CUDA fallback warnings, not a complete traceback.
- Found the real setup blocker: `D:\OtherProject\LearningLLM\GPT-SoVITS\GPT_SoVITS\pretrained_models` is missing required base assets such as BERT, HuBERT, and v2 base weights.
- Recommended install command on this machine, where `pwsh` is unavailable and Windows PowerShell should be used:
  - `powershell -ExecutionPolicy Bypass -File .\install.ps1 -Device CU126 -Source ModelScope`
- Next integration work should start only after GPT-SoVITS API can generate `vivian_zh_test.wav` and `vivian_en_test.wav` successfully.

## Completed Subphase

### Phase 5 Continued: Practical Ask Tools

Status: local file search complete.

- Added `local_file_search` as a model-triggered `ask` tool.
- The tool searches filenames and small text files inside the project workspace.
- Search results include workspace-relative paths, optional line numbers, previews, and match type.
- The implementation exists in both the TypeScript fallback and Python runtime.
- Enabled `local_file_search` in `configs/tools.yaml`.
- Updated the server system prompt so project file, docs, code, configuration, and notes search requests call `local_file_search`.
- Verified:
  - `npm run typecheck`
  - Python compile check for `packages/amadeus/tools.py` and `packages/amadeus/server.py`
  - Python tool smoke test for `local_file_search`

## Completed Subphase

### Phase 5 Continued: Audio Runtime Interface

Status: complete for first pass.

- Documented the Python-first audio direction.
- Standardized the local audio asset layout:
  - `packages/amadeus/assets/audio/voices`
  - `packages/amadeus/assets/audio/sfx`
  - `packages/amadeus/assets/audio/cache`
- Documented `audio.tts-ready` as the runtime-to-desktop event used for Python-provided audio playback.
- Added the local audio asset directories.
- Added a Python `AudioRuntime` and `TtsProvider` abstraction.
- Added `POST /audio/speak` to the Python runtime.
- Added `GET /audio/files/{relativePath}` to serve local audio files safely from `packages/amadeus/assets/audio`.
- Clarified that `voices/` stores fixed clips only; arbitrary assistant speech needs a real TTS provider.
- Updated server event types with `audio.tts-ready`.
- Updated the server bridge to call Python `/audio/speak` after assistant replies and emit `audio.tts-ready` only when Python returns a real `audioUrl`.
- Updated the desktop renderer to play runtime-provided audio URLs and cancel the system voice fallback when runtime audio arrives.
- Kept Electron/browser `speechSynthesis` as the fallback path until Python audio can generate an `audioUrl`.
- Verified:
  - `npm --workspace packages/amadeus run typecheck`
  - `npm --workspace apps/server run typecheck`
  - `npm --workspace apps/desktop run typecheck`
  - `npm --workspace apps/desktop run build`
  - Python source compile check without writing pyc files

## Next Recommended Phase

### Phase 7 Continued: ToolRuntime Hardening

Goal: turn the first ToolRuntime slice into a production-grade tool execution layer.

Planned tasks:

- Add `ToolContext` and `ToolResult` objects so tools receive session, cwd, cancellation, and audit metadata explicitly.
- Add tool duration, timeout, cancellation, and structured failure codes.
- Emit or persist audit records for tool started/finished/denied/blocked decisions.
- Add no-progress loop detection beyond exact repeated failures.
- Keep desktop WebSocket integration tests on the Python path before deleting the TypeScript fallback model/tool loop.
- Keep GPT-SoVITS provider work parked until its pretrained base models are installed.

The broader upgrade plan is documented in `docs/agent-maturity-upgrade-plan.md`.

## Later Phases

### Phase 8: Agent Memory Optimization

Not started.

- Add conversation summary storage.
- Add simple user profile facts and preferences.
- Feed summaries and profile facts into the model context.
- Add SQLite FTS session search.
- Add focused tests for memory persistence, reset behavior, and context assembly.

### Phase 11: Proactive Agent

Not started.

- Add scheduled reminders.
- Add daily brief.
- Add idle-time check-ins.
- Add background task state display.

### Phase 12: Advanced Agent Features

Not started.

- Add MCP bridge.
- Add long-task planning.
- Add sub-agent/task worker abstraction.
- Add context compression.
- Add human approval checkpoints.

## Known Issues

- The desktop app currently uses a remote Live2D test model URL. A local model should be added under `models/live2d`.
- Live2D behavior mapping is currently alias-based and depends on the available motions/expressions in the loaded model.
- The debug panel now shows model-declared motions and expressions, but some models may still omit metadata or expose very short motions that are hard to notice.
- The current Live2D model and Cubism runtime are loaded from remote URLs, so network failures can still prevent the model from appearing. A local model bundle should be added next.
- TTS currently falls back to browser/Electron `speechSynthesis`, so voice quality and available voices depend on the OS until Python audio output is fully wired.
- GPT-SoVITS integration is blocked until required pretrained base models are downloaded into `D:\OtherProject\LearningLLM\GPT-SoVITS\GPT_SoVITS\pretrained_models`.
- Lipsync is currently a simple timed mouth loop, not phoneme-accurate.
- SQLite uses Node 24's experimental built-in `node:sqlite`, so Node prints an experimental warning at server startup.
- Three local tools exist right now: `get_current_time`, `roll_dice`, and `local_file_search`. More useful tools should be added next.
- `.npmrc` uses `electron_mirror` for Electron downloads. npm prints a warning that this custom config may stop working in a future npm major version.

## Useful Commands

```bash
npm install
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

Environment:

```text
OPENAI_BASE_URL=https://api.deepseek.com
OPENAI_MODEL=deepseek-v4-flash
VITE_AGENT_WS_URL=ws://127.0.0.1:8788/ws
```

The API key is stored only in local `.env`, which is ignored by git.
