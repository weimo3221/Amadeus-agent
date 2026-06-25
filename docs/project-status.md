# Project Status

Last updated: 2026-06-26

This document is the live progress tracker for Amadeus Agent. Use it as the source of truth for what is implemented now. `docs/roadmap.md` is the forward-looking plan.

## Current Goal

Build a desktop Live2D interactive agent with a local runtime, starting from a small working MVP and expanding toward character behavior, voice, memory, tools, and later long-running agent features.

## Current Snapshot

Amadeus is now a working desktop MVP with a Python-first turn path, split Electron desktop surfaces, and a mostly landed runtime reliability foundation.

The current project phase is no longer initial MVP construction. The main MVP surfaces are present, Python owns the preferred runtime path, ToolRuntime is in late-stage hardening, Memory v2 has its core storage/review/context pieces in place, and the first Live2D/audio harness slices are active. The desktop product surface now has separate `companion` and `main-ui` renderer entries: Companion owns Live2D and lightweight desktop presence, while Main UI owns the larger workbench/chat surface. The next large product step is desktop/runtime stabilization: finish CLI/session switching, polish the two desktop surfaces, improve lipsync, continue TypeScript bridge shrinkage, and harden ToolRuntime/Memory only where real usage exposes gaps.

### Current Runtime Flow

Preferred path today:

1. A desktop surface (`companion` or `main-ui`) sends `user.message` over WebSocket with `surface`, `clientId`, and `sessionId` metadata.
2. `apps/server` routes the event inside the matching session room and first tries Python `POST /agent/turn`.
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
5. Desktop clients in the same session receive the events. Companion updates Live2D, lightweight streaming bubbles, audio playback, and permission UI; Main UI updates the larger chat/workbench surface.

Fallback path today:

- If Python `/agent/turn` is unavailable, `apps/server` now reports `python_runtime_unavailable` by default.
- The older TypeScript model/tool turn loop has been removed.

### Done Now

- Project scaffold is in place under `amadeus-agent`.
- Desktop app MVP is running with Electron and Vite multi-page renderer entries. `companion` is a transparent frameless desktop presence with Live2D, lightweight input, transient streaming reply bubbles, runtime audio playback, and a hybrid lipsync path: provider-native or runtime-planned phoneme/viseme lipsync cues when available, desktop amplitude-driven mouth movement for runtime audio otherwise, and the older timed mouth loop kept as fallback. `main-ui` is a larger chat/workbench surface without Live2D.
- Companion panel visibility is controlled by one rule: the Electron main process samples the global cursor, sends `desktop:global-cursor` with the cursor point and companion window bounds, and the renderer shows the panel only while the cursor is inside the companion window, then hides it 1.5 seconds after the cursor leaves.
- Companion Live2D model fit is configurable through `configs/runtime.yaml` under `desktop.companionLive2dScale`, `desktop.companionLive2dOffsetX`, and `desktop.companionLive2dOffsetY`; Python exposes those values through `/live2d/config` and the renderer applies them when fitting the model.
- Local runtime MVP is running in `apps/server` with HTTP health check and WebSocket events.
- WebSocket connection management supports multiple clients per session through `sessionId -> clients[]`, with validated `surface` values (`main-ui`, `companion`, `cli`) and per-client `clientId` metadata.
- DeepSeek/OpenAI-compatible chat path is connected and supports streaming assistant replies.
- Character behavior events can drive Live2D state, expression, motion, and pointer-following reactions.
- SQLite message memory is implemented in `data/amadeus.sqlite`.
- Desktop shows memory count, tool status, tool config status, voice status, visible chat messages, and has a Reset Session button.
- `apps/server` no longer owns a separate local message-count/reset SQLite path; it now reads `GET /memory/count` and forwards `POST /memory/reset` to the Python runtime while keeping the desktop protocol unchanged.
- Tool calling is model-triggered through OpenAI-compatible `tools` / `tool_calls`, not keyword matching.
- Tool execution now goes through a formal registry with `allow`, `ask`, and `deny` metadata.
- `get_current_time` is registered as an `allow` tool.
- `roll_dice` is registered as an `ask` tool.
- `read_memory` is registered as an `allow` tool for stable Markdown memory reads.
- `update_memory` is registered as an `ask` tool for controlled stable memory updates.
- `search_memory` is registered as an `allow` tool for searching prior SQLite conversation memory.
- `search_memory_items` is registered as an `allow` tool for searching structured memory facts.
- `memory_add` is registered as an `ask` tool for adding durable structured memory facts after user approval.
- `memory_replace` and `memory_forget` are registered as `ask` tools for correcting or deleting durable structured memory facts after user approval.
- `search_files` is implemented as the preferred `allow` search tool in the Python runtime.
- `read_file` is implemented as an `allow` tool for reading bounded UTF-8 workspace files after search, with structured unsupported responses for image/PDF/binary/unknown file types.
- `patch` is implemented as an `ask` tool for safe single-file UTF-8 text replacement.
- `write_file` is implemented as an `ask` tool for creating or fully overwriting UTF-8 workspace text files.
- `AgentRuntime` maintains a per-session `workspace_epoch` for file-observing tool guardrails; successful `patch` / `write_file` mutations advance the epoch so repeated reads/searches after an edit are not treated as stale duplicates.
- Python tool implementations are split under `packages/amadeus/tools/`, with `amadeus.tools` kept as the public registry entrypoint.
- `configs/tools.yaml` is loaded at startup and controls effective tool enabled/permission state.
- `configs/runtime.yaml` controls runtime memory/context tuning such as token-budget compaction, summary thresholds, and memory review limits; it is loaded at startup and can be explicitly reloaded with `POST /runtime/config/reload`, while environment variables can still override these values for deployment.
- Desktop diagnostics show the Python runtime tool permission state through the server bridge.
- `packages/amadeus/tools.ts` now only keeps TypeScript tool types plus Python `/tools/list` and `/tools/execute` bridge helpers; it no longer mirrors the concrete tool registry or local tool handlers.
- Python `/agent/turn` is wired as the preferred turn path.
- Python now owns the preferred model/tool/memory/behavior path for a turn:
  - loads OpenAI-compatible provider config from environment or `.env`
  - assembles API-call-time context from summaries, accepted memory items, recent messages, and relevant FTS retrieval
  - makes the tool-decision call
  - executes Python tools
  - writes user/assistant messages to SQLite
  - emits desktop-compatible runtime events
  - emits `memory.context.used` diagnostics for per-turn memory source selection and keeps the most recent diagnostics per session in an in-memory ring buffer
  - requests Python audio output after the assistant message
- Ask-tool permission requests cross the Python runtime boundary:
  - Python emits `tool.permission.request`
  - the TypeScript bridge relays it to desktop
  - desktop sends `tool.permission.response`
  - the bridge forwards it back to Python `/tools/permission`
- Python audio interface now has a practical default on macOS: runtime TTS auto-selects GPT-SoVITS when configured, otherwise uses local `say`/`afconvert` and emits `audio.tts-ready`.
- Python audio now prefers provider-native lipsync payloads when a TTS provider returns them, normalizing `lipsyncCues` / `visemes` / `phonemes` JSON into runtime `audio.lipsync-cues`; the local phoneme planner remains the fallback when providers return audio without cue metadata.
- Desktop still keeps Electron/browser `speechSynthesis` fallback for provider failures or unsupported platforms.
- Python runtime parity tests are now wired through `npm test`.
  - missing API key returns a structured runtime error
  - simple turns persist user and assistant messages
  - `allow` tools execute without permission prompts
  - denied `ask` tools return a tool error to the model
  - tool config overrides apply to Python tool metadata
  - permission broker resolution behavior is covered
- Python runtime HTTP handler tests now cover the local sidecar boundary.
  - `/tools/list` exposes effective permission state and enabled schemas
  - `/runtime/health` exposes structured local health checks for runtime, model config, memory DB, tools, Live2D, audio, and effective config
  - `/runtime/feedback` records and returns Python-side harness feedback for per-client desktop capabilities, aggregate session capabilities, and audio playback state
  - `/tools/permission` returns unresolved for unknown request IDs
  - `/agent/turn` streams missing API key failures as desktop-compatible NDJSON events
- TypeScript bridge tests now cover the Python-first relay boundary.
  - `/agent/turn` NDJSON events are relayed to the desktop socket
  - `/tools/list` tool permissions are read from the Python runtime for server diagnostics
  - desktop capability and audio playback feedback events are forwarded to Python `/runtime/feedback`
  - Python-returned feedback events such as `character.behavior` and fallback `audio.lipsync-cues` are relayed back to the desktop socket
  - Python runtime connection failures return `false` from the relay so the caller can decide whether to report an error or use the explicit fallback
  - malformed Python events emit desktop-compatible `error` events without dropping later valid events
  - unresolved permission responses are forwarded to Python `/tools/permission`
- Server-level WebSocket integration tests now cover the Python-first desktop event path.
  - `user.message` over WebSocket reaches Python `/agent/turn`
  - streamed Python runtime events are returned to the WebSocket client
  - `tool.permission.response` over WebSocket is forwarded to Python
  - `desktop.capabilities` and `audio.playback-*` feedback events are accepted by the bridge feedback hook
  - multiple WebSocket clients can share a session while preserving independent `surface` / `clientId` metadata
  - Python-returned feedback harness events such as `character.behavior` are sent back to the desktop socket
- Desktop renderer harness tests now cover runtime UI behavior.
  - `server.hello` updates model, memory, connection, and tool config diagnostics
  - `desktop.capabilities` is sent after runtime hello and after Live2D model load
  - assistant deltas/messages update chat output and schedule speech fallback
  - `tool.permission.request` shows Allow / Deny UI and sends `tool.permission.response`
  - chat form submission sends `user.message` over the active socket
  - `audio.tts-ready` cancels speech fallback and plays runtime audio instead
  - runtime audio start/end/error sends playback feedback for harness coordination
  - `tool.finished` clears permission prompts and updates tool status
- Desktop Electron smoke coverage now builds the packaged desktop app, starts the Electron main process, and verifies that the renderer finishes loading.
- Desktop Electron E2E coverage now includes a deterministic local-runtime UI path: the packaged desktop connects to a stub bridge, submits a chat message through the real form, receives streamed assistant events, and renders the assistant reply without requiring a live model provider.
- Desktop Electron E2E coverage now includes the dual-window desktop shape: Companion can open Main UI, both windows attach to the same companion session, and closing Main UI does not terminate Companion.
- Desktop Electron E2E coverage now includes the Companion lightweight surface: controls are hidden by default, become visible from the global-cursor visibility path, and streaming assistant output renders outside the input panel as transient bubbles.
- Desktop Electron E2E coverage now includes deterministic Live2D local model loading and switching: the packaged desktop reads local model config/list endpoints, loads the configured model through the renderer, switches models through the real select control, calls `/live2d/select`, and verifies harness config persistence.
- Production Live2D HTTP ownership is now narrower in `apps/server`: the bridge proxies `/live2d/config`, `/live2d/models`, `/live2d/select`, and `/live2d/models/...` to the Python runtime, rewrites returned model URLs back to the bridge origin, and no longer owns the real model-library scan or harness-config mutation path.
- Desktop Electron E2E coverage now includes deterministic runtime audio playback feedback: the packaged desktop receives `audio.tts-ready`, plays mock runtime audio, and reports both success feedback (`audio.playback-started` / `audio.playback-ended`) and failure feedback (`audio.playback-started` / `audio.playback-error`) to the bridge.
- Desktop Electron E2E coverage now includes deterministic permission prompt flows: the packaged desktop receives `tool.permission.request`, shows the real Allow / Deny UI, and reports `tool.permission.response` back to the bridge for both approval and denial.
- The legacy TypeScript fallback loop has been removed.
  - Python runtime failures now produce an explicit desktop error
  - `apps/server` no longer owns provider calls, tool execution, memory writes, or audio trigger logic for user turns
- First-pass Python model/provider boundary is active.
  - `packages/amadeus/model.py` now owns OpenAI-compatible provider config from `configs/providers.yaml` plus environment variables, JSON chat-completion requests, stream parsing, and classified provider error normalization.
  - Provider errors now preserve kind, HTTP status, body, retry-after, provider, and model metadata for later retry/fallback decisions.
  - `packages/amadeus/agent.py` still owns turn orchestration, tool decisions, summaries, memory review, and final response timing.
- First-pass harness boundary is active.
  - `packages/amadeus/harness` now provides a base contract, registry, and Live2D harness.
  - `configs/harnesses.yaml` controls the initial Live2D harness.
  - The Live2D harness maps `assistant.state` events into `character.behavior` events instead of keeping that mapping hardcoded in the agent loop.
- Local Live2D model storage is active.
  - Local models live under `models/live2d`, with `hiyori-free` as the current default and `hiyori-pro` available for switching.
  - `configs/harnesses.yaml` selects the active model by id/path.
  - Python runtime now owns the local Live2D model library, including configured-model reads, model listing, manifest reads, and `/live2d/select` persistence.
  - `apps/server` keeps the desktop-facing `8788` HTTP origin by proxying `/live2d/*` to Python and rewriting returned model URLs back to bridge-relative URLs, so the renderer can stay on the same origin it already uses for WebSocket traffic.
  - The old TypeScript local Live2D library fallback has been removed from `apps/server`; Live2D HTTP ownership now lives only in Python runtime plus bridge proxying.
- Session memory count/reset now follows the same ownership split:
  - Python runtime owns the real SQLite-backed session count and reset behavior through `GET /memory/count` and `POST /memory/reset`.
  - `apps/server` only proxies those behaviors into `server.hello` and `session.reset`; it no longer opens its own local `messages` table.
- First-pass real TTS provider boundary is active.
  - `packages/amadeus/audio.py` now includes a config-gated GPT-SoVITS HTTP provider and a macOS `say` provider.
  - The default `tts.default` is `auto`, preferring GPT-SoVITS when configured and falling back to macOS `say` when available.
  - Binary/generated audio responses are cached under the local audio library and surfaced through the existing `audio.tts-ready` event path.
- Phase 7 ToolRuntime first slice is in place.
  - Python tool registry/config loading now lives under `packages/amadeus/tool_runtime`.
  - Agent tool execution dispatches through `ToolRegistry` instead of direct helpers.
  - Tool execution now returns structured `ToolResult` metadata with success state, duration, and stable failure codes.
  - Tool execution now emits `tool.audit` events and persists audit records to SQLite for started/finished/denied/blocked/failed decisions, including metadata such as workspace epoch for normal agent-loop tool calls.
  - Tool execution now has a first-pass timeout boundary and returns `tool_timeout` for slow tool calls.
  - `ToolContext` now carries a cooperative cancellation signal; pre-cancelled calls return `tool_cancelled`, and timeout sets the cancellation signal for context-aware tools.
  - Large successful tool outputs are compacted before being written back into model context, while full output remains available on `ToolResult`.
  - Stable memory now lives in auditable Markdown files (`MEMORY.md` and `USER.md`) and is injected into the frozen system prompt at runtime startup.
  - `ContextAssembler` now injects summaries, accepted structured memory, and sanitized SQLite FTS retrieval as API-only context, with `memory.context.used` diagnostics retained in a per-session in-memory ring buffer.
  - `search_memory` now has a per-tool model-output policy that keeps memory match metadata while capping model-context result count and snippet length.
  - `search_memory_items` now has a per-tool model-output policy that keeps structured fact metadata while capping model-context item count and content length.
  - `search_files` now has a per-tool model-output policy that keeps query metadata while limiting returned result count and preview length.
  - `read_file` now uses explicit line-window reads with line numbers and `hasMore`, instead of hidden runtime compression, and reports non-text file kinds without decoding them.
  - `patch` now supports safe single-file UTF-8 text replacement with unique-match default, optional `replaceAll`, generated-directory restrictions, and diff output.
  - `write_file` now supports safe UTF-8 text file creation and explicit whole-file overwrite with generated-directory restrictions, text-extension checks, size limits, parent directory creation, and diff output.
  - A per-turn `ToolLoopGuardrail` blocks repeated exact failing tool calls and repeated completed calls that do not make progress, with semantic no-progress policies for repeated empty/same searches, repeated read windows, repeated patch failures, and repeated write failures. File-observing signatures include session `workspace_epoch` so successful file edits invalidate stale no-progress counts.
  - Unit tests cover registry config aliases, cancellation behavior, persisted audit records and metadata, result policy behavior, guardrail threshold behavior, semantic no-progress policies, workspace epoch invalidation, agent-level repeated failure blocking, and agent-level no-progress blocking.
- Local GPT-SoVITS project and Vivian model weights have been located for the first concrete TTS provider test.
- Desktop shows inline Allow / Deny prompts for `ask` tools.
- `configs/tools.yaml` mirrors the current intended tool permissions.
- Typecheck, desktop build, allow-path WebSocket test, and deny-path WebSocket test have passed.

### Still Needed

- Continue consolidating the desktop UI now that Main UI and Companion are split. The current priority is to keep Companion lightweight and make Main UI the place for richer context, skills, diagnostics, permissions, and future session switching.
- Implement the real CLI entry as an independent session client, defaulting to its own session ID unless explicitly attached elsewhere.
- Add Main UI session switching and an explicit attach/view flow for Companion sessions.
- Keep Electron end-to-end coverage aligned with that UI pass so layout and interaction regressions are caught while the surface is being simplified.
- Keep improving lipsync from the current provider-native plus phoneme-planned path, especially broader provider cue compatibility and better non-Latin mapping, while keeping desktop playback/rendering as the adapter and routing policy through harness events.
- Continue shrinking TypeScript bridge scaffolding now that the legacy turn loop is gone. `apps/server` should remain a transport/proxy layer, not an owner of agent, model-library, tool, memory, or audio turn logic.
- `tool.permission.response` is now always forwarded through the bridge to Python; the old “maybe a local TypeScript tool loop owns this request” branch has been removed from the production server path.
- Add more practical `ask` tools such as opening URLs or reminders.
- Finish late ToolRuntime hardening only where real usage exposes gaps, such as richer context propagation, more diagnostic surfaces, or additional no-progress policies for new tools.
- Finish Memory v2 consolidation around context assembly quality, summary/profile policy, review quality, and overflow compaction behavior.
- After the UI pass, the next product step for skills should be a real import/install flow that runs `scripts/validate_skills.py` during add/import, then refreshes runtime discovery without forcing a full manual restart.
- Turn placeholder runtime boundaries into real modules where needed:
  - future audio harness module
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
- The current default model is served from the local `models/live2d/hiyori-free` bundle, with `hiyori-pro` available for switching.

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
- Added runtime TTS playback through `audio.tts-ready`, with macOS `say` and GPT-SoVITS provider support behind `packages/amadeus/audio.py`.
- Added hybrid lipsync: provider-native or runtime-planned phoneme/viseme cues first, desktop amplitude-driven mouth movement for runtime audio second, and the timed mouth loop only as fallback.
- Added desktop voice status diagnostics.

### Phase 5: Memory and Tools

Status: MVP memory, model-triggered tools, registry, config loading, and permission prompts complete.

- Added SQLite-backed message persistence in the Python runtime memory store.
- Desktop now shows memory and tool feedback.
- Added model-triggered tool calling.
- Added the first formal tool registry and tool config loader.
- Added `allow` / `ask` / `deny` tool permissions.
- Added inline desktop Allow / Deny prompts.
- Extracted TypeScript tool metadata and config loading into `packages/amadeus/tools.ts`; this was later replaced by the Python-owned `/tools/list` bridge.
- Added `search_files` as the project-search tool with `target: all | files | content`.
- Added `read_file` so the agent can inspect a bounded UTF-8 workspace file after finding it.
- Split Python tool implementations from the old single `tools.py` file into `packages/amadeus/tools/` modules while keeping the `amadeus.tools` import surface stable.

## In Progress

### Phase 6: Python Runtime Ownership

Status: functionally landed; cleanup remains.

The second vertical slice is complete: Python runtime parity tests, Python HTTP handler tests, TypeScript bridge relay tests, server-level WebSocket integration tests, and desktop renderer harness tests are in place, and `npm test` now runs them. The remaining work is mainly shrinking TypeScript bridge scaffolding and extracting provider/model boundaries out of `agent.py`, not proving the Python-first path from scratch.

### Phase 7: ToolRuntime and Guardrails

Status: mostly landed; late hardening remains.

The foundation is now substantially implemented: Python tool registry/config loading has been extracted into `packages/amadeus/tool_runtime`, tool execution returns structured `ToolResult` metadata, audit records persist to SQLite, timeout/cancellation paths are covered, model-context output policies are in place for high-volume tools, and the Python agent loop applies repeated-failure and semantic no-progress guardrails during tool execution.

Remaining Phase 7 work should be treated as incremental hardening: richer context propagation, better diagnostics on top of audit records, and new no-progress/result policies as additional tools are added.

### Phase 8: Memory v2

Status: core system landed; consolidation remains.

Memory v2 is no longer just planned. Stable Markdown memory, SQLite-backed message history and FTS retrieval, structured memory facts, explicit memory tools, memory review candidates, accept/reject flows, automatic review gates, runtime memory config, schema metadata, and safety filters are in place.

Remaining Phase 8 work is about making the memory behavior mature in practice: better summary/profile/retrieval policy, provider overflow compact-and-retry confidence, review quality tuning, and lightweight diagnostics endpoints to understand why a fact was retrieved, proposed, accepted, rejected, or suppressed.

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
- Added first-pass `tool.audit` events and SQLite-backed audit records for tool started/finished/denied/blocked/failed decisions.
- Added first-pass tool timeout handling with structured `tool_timeout` failures.
- Added first-pass cooperative cancellation handling with structured `tool_cancelled` failures.
- Added first-pass result preview/compression so large successful tool outputs do not flood model context.
- Added first-pass per-tool result policy for `search_files`, keeping search metadata while capping model-context result count and preview length.
- Changed `read_file` to explicit `startLine` / `lineLimit` window reads with line numbers, `totalLines`, and `hasMore`; it no longer uses hidden runtime model-output compression and now identifies image/PDF/binary/unknown files with structured unsupported responses.
- Added first-pass `patch` tool for safe single-file text replacement, following the Hermes/Deepagents pattern of exact old/new text with unique-match default and diff output.
- Added `write_file` as the companion whole-file write tool for creating UTF-8 text files and explicit full-file replacement.
- Split concrete Python tools into focused modules under `packages/amadeus/tools/`, with shared definitions in `tools/base.py` and registry exports in `tools/__init__.py`.
- Added first-pass no-progress loop detection with structured `no_progress_loop` failures.
- Added focused tests for:
  - registry config alias behavior
  - structured tool execution results
  - structured timeout failures
  - structured cancellation failures and timeout cancellation signaling
  - model-context compression for large tool results
  - `search_files` result policy behavior
  - audit event/log behavior and SQLite persistence for allow, deny, and guardrail paths
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
- Server tool diagnostics now query Python `/tools/list`; when Python is unavailable, the desktop gets an explicit disabled `python_runtime_unavailable` tool status instead of a stale TS mirror.
- `npm test` covers deterministic Python runtime behavior, local Python HTTP handlers, TypeScript bridge relay behavior, server-level WebSocket integration behavior, and desktop renderer runtime UI behavior.

Current limitations:

- `apps/server` no longer contains the legacy TypeScript fallback loop or local TypeScript tool registry mirror.
- Test coverage now includes Python runtime units, local Python HTTP handlers, TypeScript bridge relay behavior, server-level WebSocket integration behavior, desktop renderer runtime UI behavior, and an Electron startup smoke. Full Live2D/interaction end-to-end coverage is still missing.
- `packages/amadeus/model.py` is active as the first-pass provider boundary, but richer provider profile/fallback behavior is still future work.
- `skills.py` now implements a first-pass runtime skill catalog with an always-on system-prompt catalog plus `skill_view`-driven full activation; the bridge proxies read-only skills APIs, and the desktop renderer exposes a multi-select suggested-skills picker with local search/filtering, a short inline summary, and persisted selections. `live2d.py` owns the local model library but not the desktop renderer adapter.
- `packages/live2d-stage` is still not the real desktop implementation package; current Live2D behavior lives in `apps/desktop/src/renderer/companion/main.ts`.

## Next Recommended Phase

### Runtime/Harness Operational Polish

Goal: keep ToolRuntime and Memory v2 in consolidation mode while improving the operational surfaces around the Python runtime, Live2D/audio harnesses, and desktop integration.

Planned tasks:

- Keep `GET /runtime/health`, `GET /tools/audit`, memory review jobs, and context diagnostics as developer-facing observability surfaces rather than user-facing memory UI.
- Extend cancellation beyond the current cooperative signal only if future tools need stronger process-level interruption.
- Extend result preview/compression and no-progress detection only as new high-volume tools expose real gaps.
- Use Python-side harness feedback state to drive richer Live2D/audio policy decisions so they can react to actual renderer/audio state.
- Keep expanding Electron end-to-end coverage on the Python path, especially Live2D loading, model switching, audio playback, and real user/runtime interactions.
- Keep GPT-SoVITS high-quality voice work parked until its pretrained base models and API configuration are settled.

The broader upgrade plan is documented in `docs/agent-maturity-upgrade-plan.md`.

## Later Phases

### Phase 7: ToolRuntime and Guardrails

In progress.

Notes:

- The first Python `tool_runtime` slice exists with registry/config loading, permission-aware schema selection, dispatch, cooperative cancellation, audit persistence, result compaction, repeated-failure guardrails, semantic no-progress guardrails, session workspace epoch invalidation for file-observing tools, and a `search_files` result policy. `read_file` uses explicit line-windowing instead of hidden compression and reports unsupported non-text file kinds; `patch` and `write_file` provide targeted-edit and whole-file write paths.
- The remaining work is the mature runtime layer: richer context propagation, additional per-tool result policies for future high-volume tools, and continued tuning of semantic no-progress policies as new tools land.

### Phase 8: Agent Memory Optimization

Started.

- SQLite FTS-backed session search is implemented for raw conversation messages.
- Python runtime exposes `GET /memory/search`.
- `search_memory` lets the model search current-session memory, with optional all-session search.
- Automatic memory prefetch injects relevant prior snippets into the current user message as non-persistent `<memory-context>`.
- Stable long-term memory is implemented with bounded Markdown files under `data/memory/`.
- `read_memory` / `update_memory` expose controlled read and add/replace/remove operations for agent facts and user preferences.
- Conversation summary storage and load APIs are implemented with persisted SQLite records and `GET /memory/summary` / `POST /memory/summary`.
- Conversation summaries now track covered message ranges, are injected as reference-only context, and can be refreshed by automatic threshold compaction or manual `POST /memory/compact`.
- Python exposes recent in-memory context assembler diagnostics through `GET /memory/context/diagnostics`, scoped by session and bounded by `context.diagnosticsLimit`.
- Structured `memory_items` now persist durable `user` / `agent` / `project` facts, expose `GET /memory/items`, `POST /memory/items`, and `POST /memory/items/delete`, and inject the active top items into model context.
- Explicit structured memory tools are now in place: `search_memory_items` reads durable facts without approval, while `memory_add`, `memory_replace`, and `memory_forget` mutate one durable fact only through the `ask` permission path.
- Memory review candidates now provide a human-controlled promotion queue: `GET/POST /memory/review/candidates` manages pending candidates, `POST /memory/review/accept` promotes one into `memory_items`, and `POST /memory/review/reject` rejects one without writing durable memory.
- Background memory review runner can now be triggered with `POST /memory/review/run` or automatically after a completed turn when the threshold/cooldown gates allow it; it asks the provider to propose candidates from recent messages and only writes pending `memory_review_candidates`, never durable `memory_items`.
- Memory review safety filters now block secret-like content, temporary debug/run state, uncertain claims, overly specific local/cache/generated paths, and obvious `user` / `agent` / `project` scope mismatches before candidates are persisted.
- Rejected memory review candidates suppress later identical suggestions for the same session/scope/content.
- Desktop now exposes the human review loop: it lists pending candidates, lets the user trigger review manually, and sends Accept / Reject actions over the WebSocket bridge.
- Memory review job observability is now persisted in SQLite `memory_review_jobs`: every manual/automatic review records `running`, `completed`, `skipped`, or `failed` state, trigger, skip reason/error, source message range/count, proposed/saved/suppressed candidate counts, and duration.
- Python exposes `GET /memory/review/jobs`, the TypeScript bridge relays it as `memory.review.jobs`, and the desktop memory review panel shows the latest job summary next to the pending candidate count.
- Summary compaction is now token-budget-aware: runtime estimates context tokens before provider calls and after turns, loads its defaults from `configs/runtime.yaml`, supports explicit HTTP reload and environment overrides such as `AMADEUS_CONTEXT_MAX_TOKENS`, dynamically reduces the recent-message keep window, and retries once after provider context-overflow errors.
- Next: keep Memory v2 in consolidation mode and move focus to runtime/harness operational polish.

### Phase 9: Live2D and Audio Harnesses

Started.

- First harness slice exists in `packages/amadeus/harness`, with `configs/harnesses.yaml` selecting Live2D model config and playback-state behavior mapping.
- Local Live2D model storage is active through `models/live2d`, and the Python runtime now owns `/live2d/config`, `/live2d/models`, `/live2d/select`, and `/live2d/models/...`; the bridge proxies those endpoints back to the desktop-facing origin.
- Runtime audio provider selection and cache are active through `packages/amadeus/audio.py`, with GPT-SoVITS config support, macOS `say` fallback, `/audio/speak`, and `audio.tts-ready`.
- Desktop capability and runtime audio playback feedback now reach Python through `POST /runtime/feedback` and are stored by `HarnessFeedbackPolicy`.
- Live2D maps `audio.playback-started`, `audio.playback-ended`, and `audio.playback-error` into playback-state-driven `character.behavior` events returned to the desktop; these mappings are configurable through `live2d.audioPlaybackBehaviors`.
- Remaining work: richer audio harness boundary, richer Live2D commands, speaking-state reconciliation, broader provider cue compatibility, and stronger non-Latin phoneme mapping.

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

- The desktop app now uses local Live2D model bundles by default; the remote model path should only remain as a defensive fallback.
- Live2D behavior mapping is currently alias-based and depends on the available motions/expressions in the loaded model.
- The Live2D Cubism runtime still depends on renderer-side package/runtime availability, so full Live2D startup needs deeper Electron coverage.
- GPT-SoVITS high-quality voice integration still requires a running GPT-SoVITS API and model assets; until then macOS `say` provides the local practical TTS loop.
- Lipsync is no longer only a timed mouth loop: runtime now prefers provider-native or locally planned phoneme/viseme cues, with desktop amplitude analysis and the timed loop retained as fallbacks.
- Current tests cover Python runtime-unit behavior, local HTTP handlers, TypeScript bridge relay behavior, server-level WebSocket integration behavior, desktop renderer runtime UI behavior, and Electron startup smoke behavior. Full Live2D/interaction end-to-end coverage is still missing.
- Partial boundaries still need more depth or cleanup: richer skill management/orchestration, the future audio harness, richer Live2D adapter packaging, and `packages/live2d-stage`.

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
