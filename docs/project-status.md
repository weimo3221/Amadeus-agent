# Project Status

Last updated: 2026-07-15

This document is the live progress tracker for Amadeus Agent. Use it as the source of truth for what is implemented now. `docs/roadmap.md` is the forward-looking plan.

## Current Goal

Build a desktop Live2D interactive agent with a local runtime, starting from a small working MVP and expanding toward character behavior, voice, memory, tools, and later long-running agent features.

## Current Snapshot

Amadeus is now a working desktop MVP with a Python-first turn path, split Electron desktop surfaces, local ASR/TTS voice I/O, scheduled triggers, persistent session todos, tracked background tasks, and a mostly landed runtime reliability foundation.

The current project phase is no longer initial MVP construction. The main MVP surfaces are present, Python owns the preferred runtime path, ToolRuntime is in late-stage hardening, Memory v2 has its core storage/review/context pieces in place, and the first Live2D/audio harness slices are active. The desktop product surface now has separate `companion` and `main-ui` entries: Companion owns Live2D, voice input, and lightweight desktop presence, while the Vue `desktop-ui-next` Main UI owns the larger workbench/chat surface. Main UI restores current session history, displays active and completed timed messages, exposes richer runtime configuration, and uses a light anime plus modern SaaS visual treatment. The next large product step is desktop/runtime stabilization: finish CLI/session switching, polish the two desktop surfaces, improve lipsync/ASR quality, continue TypeScript bridge shrinkage, and harden ToolRuntime/Memory only where real usage exposes gaps.

### Current Runtime Flow

Preferred path today:

1. A desktop surface (`companion` or `main-ui`) sends `user.message` over WebSocket with `surface`, `clientId`, and `sessionId` metadata. Companion voice input first records microphone audio, transcribes it through `/audio/transcribe`, then reuses this same message path.
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
- Desktop app MVP is running with Electron and Vite renderer entries. `companion` is a transparent frameless desktop presence with Live2D, lightweight text/voice input, transient streaming reply bubbles, runtime audio playback, and a hybrid lipsync path: provider-native or runtime-planned phoneme/viseme lipsync cues when available, desktop amplitude-driven mouth movement for runtime audio otherwise, and the older timed mouth loop kept as fallback. `desktop-ui-next` is now the default larger chat/workbench surface without Live2D, with a legacy Main UI fallback behind `AMADEUS_MAIN_UI_LEGACY`.
- Companion panel visibility is controlled by one rule: the Electron main process samples the global cursor, sends `desktop:global-cursor` with the cursor point and companion window bounds, and the renderer shows the panel only while the cursor is inside the companion window, then hides it 1.5 seconds after the cursor leaves.
- Companion Live2D model fit is configurable through `configs/runtime.yaml` under `desktop.companionLive2dScale`, `desktop.companionLive2dOffsetX`, and `desktop.companionLive2dOffsetY`; Python exposes those values through `/live2d/config` and the renderer applies them when fitting the model.
- Local runtime MVP is running in `apps/server` with HTTP health check and WebSocket events.
- WebSocket connection management supports multiple clients per session through `sessionId -> clients[]`, with validated `surface` values (`main-ui`, `companion`, `cli`) and per-client `clientId` metadata.
- DeepSeek/OpenAI-compatible chat path is connected and supports streaming assistant replies. DeepSeek now defaults to `deepseek-v4-pro` with provider-aware thinking support: the runtime sends `thinking` / `reasoning_effort` only to supported DeepSeek models, preserves `reasoning_content` for DeepSeek tool-call replay, and strips DeepSeek-only reasoning fields before requests to other providers.
- Character behavior events can drive Live2D state, expression, motion, and pointer-following reactions.
- SQLite message memory is implemented in `data/amadeus.sqlite`.
- Desktop diagnostics are split by surface: Companion keeps lightweight live status, voice/Live2D feedback, and transient chat bubbles, while the Vue Main UI owns the larger chat history, memory/task/tool/MCP diagnostics, runtime configuration, and session reset/archive flows.
- Main UI restores the current session's persisted chat history from Python `/memory/messages` when opened or switched by `sessionId`, renders assistant Markdown through the shared runtime Markdown renderer, and keeps assistant tool-call decision records visible as collapsed tool-call detail cards instead of blank assistant bubbles.
- Main UI includes a Timed Messages panel for listing scheduled triggers across active and terminal states. It listens for `scheduled.updated`, shows `已启用` / `执行中` / `已暂停` / `已完成` / `已取消` / `失败`, displays last-run plus completed-run counts, distinguishes message-only schedules from schedules that trigger background tasks, and can create either mode from the UI.
- Main UI plan items can now be promoted into background tasks from the plan panel. The created task records keep `source="plan"` and `planItemId`, and the Python task worker reflects execution back into the visible plan by marking linked items in progress, completed, pending after final failure, or cancelled.
- Main UI now treats model-generated plans as turn-scoped Agent progress instead of a global session header. `agent.turn.started` binds a runtime `turnId` to the latest user message, `task.plan.updated` refreshes an assistant-side plan panel for that turn, and `assistant.message` archives the panel as completed/collapsed or incomplete under the same user turn. Python also persists `plan_runs` with `turnId`, `userMessageId`, optional `assistantMessageId`, status, and plan snapshot so reloaded sessions can restore historical turn plans on the Agent side.
- Main UI task management now includes a task detail modal with execution metadata, source/relationship labels, result/error/typed-artifact display, `/tasks/{id}/events` timeline, cancellation for queued/running tasks, review approval / resume controls for blocked tasks, and re-run for terminal tasks through a new linked background task. Workspace overview shows recent terminal/blocked task notifications that link back to the Tasks view.
- Main UI includes a configuration center for model provider/API settings, model thinking mode and reasoning effort (`low` / `medium` / `high`), Live2D model import/selection/behavior mapping, macOS/GPT-SoVITS TTS settings, and runtime config persistence through Python endpoints.
- Main UI and Companion have a first-pass light anime plus modern SaaS visual refresh with softer typography, pastel surfaces, and higher-contrast interactive states.
- `apps/server` no longer owns a separate local message-count/reset SQLite path; it now reads `GET /memory/count` and forwards `POST /memory/reset` to the Python runtime while keeping the desktop protocol unchanged.
- Tool calling is model-triggered through OpenAI-compatible `tools` / `tool_calls`, not keyword matching.
- Tool execution now goes through a formal registry with `allow`, `ask`, and `deny` metadata.
- `get_current_time` is registered as an `allow` tool.
- `roll_dice` is registered as an `ask` tool.
- `terminal` is registered as an `ask` tool for bounded foreground shell commands inside the workspace.
- `process` is registered as an `ask` tool for local process listing/status/signaling.
- `web_search` is registered as an `allow` tool for lightweight public web search.
- `web_extract` is registered as an `ask` tool for fetching and extracting bounded HTTP(S) page text.
- `web-access` is installed as a project runtime skill under `skills/web-access`. It provides a CDP-backed browser workflow for real web access, including dynamic pages and logged-in browser context, and is available through the normal `skills_list` / `skill_view` activation path. Its generated local `config.env` stays ignored.
- Hermes-compatible `browser_*` tool names are registered but disabled by default, bridging to a configured HTTP browser backend or browser MCP server rather than embedding a second browser runtime.
- `vision_analyze` is registered as an `ask` tool; without a configured endpoint it returns safe local image metadata and setup guidance.
- `clarify` is registered as an `allow` tool for structured clarification requests.
- `execute_code` is registered as an `ask` tool for bounded Python code execution inside a workspace-contained cwd.
- Tool smoke tests confirm the local execution, extraction, browser bridge, vision bridge, and project skill activation paths work. Direct public search providers timed out from the current development network, so reliable web access should use an internal/provider-backed path or the installed `web-access` skill.
- Optional real-network smoke tests are available behind `AMADEUS_RUN_WEB_ACCESS_SMOKE=1`. They verify that Amadeus can load `web-access`, run its `check-deps.mjs`, use the CDP proxy to read `example.com`, and complete a more realistic paper lookup by finding `Attention Is All You Need` via arXiv API and cross-checking `arXiv:1706.03762` in the browser DOM.
- `read_memory` is registered as an `allow` tool for stable Markdown memory reads.
- `update_memory` is registered as an `ask` tool for controlled role-scoped stable memory updates.
- `update_current_role_identity` is registered as an `ask` tool for explicit current-role name/persona/style updates through role `SOUL.md`.
- `skills_list` and `skill_view` are registered as `allow` tools for runtime skill catalog inspection and progressive skill activation.
- `skill_manage` is registered as an `ask` tool for saving approved reusable workflow experience as a local runtime skill.
- `search_memory` is registered as an `allow` tool for searching prior SQLite conversation memory.
- `search_memory_items` is registered as an `allow` tool for searching structured memory facts.
- `memory_add` is registered as an `ask` tool for adding durable structured memory facts after user approval.
- `memory_replace` and `memory_forget` are registered as `ask` tools for correcting or deleting durable structured memory facts after user approval.
- `search_files` is implemented as the preferred `allow` search tool in the Python runtime.
- `read_file` is implemented as an `allow` tool for reading bounded UTF-8 workspace files after search, with structured unsupported responses for image/PDF/binary/unknown file types.
- `patch` is implemented as an `ask` tool for safe single-file UTF-8 text replacement.
- `write_file` is implemented as an `ask` tool for creating or fully overwriting UTF-8 workspace text files.
- `delegate_task` is implemented as a first restricted research/search delegate: max depth 1, max concurrency 2, memory search, file search, explicit bounded file reads, no write tools, no shell, no recursive delegation, and summary-only results to the parent agent.
- `create_task` / `list_tasks` / `cancel_task` are implemented as tracked background-task tools. Task records now carry execution metadata such as `kind`, `source`, `parentTaskId`, `rootTaskId`, `planRunId`, `planItemId`, `workerType`, `workerProfile`, acceptance criteria, context hints, toolset allow/deny metadata, checkpoint, handoff summary, review state, and artifacts so tasks can become the durable execution unit for heavier workflows. `planItemId` links a task to a visible plan step and lets task worker state transitions update the plan.
- `schedule_message` is implemented as an `allow` tool for reminders, alarms, countdowns, recurring check-ins, proactive companion messages, and scheduled background execution. Scheduled jobs are persisted in SQLite, emit `scheduled.updated` lifecycle events, default to `mode="message"` for timed assistant-message delivery, and support `mode="agent_task"` to create and submit a tracked background task at fire time.
- `todo` is implemented as an `allow` tool for persistent user-facing session todo lists. Active todos are injected into API-call-time context through `<active-todos>`.
- `AgentRuntime` maintains a per-session `workspace_epoch` for file-observing tool guardrails; successful `patch` / `write_file` mutations advance the epoch so repeated reads/searches after an edit are not treated as stale duplicates.
- Python tool implementations are split under `packages/amadeus/tools/`, with `amadeus.tools` kept as the public registry entrypoint.
- `configs/tools.yaml` is loaded at startup and controls effective tool enabled/permission state.
- `configs/runtime.yaml` controls runtime memory/context tuning such as token-budget compaction, summary thresholds, and memory review limits; it is loaded at startup and can be explicitly reloaded with `POST /runtime/config/reload`, while environment variables can still override these values for deployment.
- Desktop diagnostics show the Python runtime tool permission state through the server bridge.
- `packages/amadeus/tools.ts` now only keeps TypeScript tool types plus Python `/tools/list` and `/tools/execute` bridge helpers; it no longer mirrors the concrete tool registry or local tool handlers.
- Python `/agent/turn` is wired as the preferred turn path.
- Python now owns the preferred model/tool/memory/behavior path for a turn:
  - loads OpenAI-compatible provider config from environment or `.env`
  - assembles API-call-time context from the active runtime memory provider, recent messages, active plan/todo/task reference blocks, and diagnostics; the default `mem0_like_runtime` provider keeps the hybrid summary/current-session FTS/global FTS lanes, while typed long-term `memory_items` remain available through explicit tools when no external provider is configured
  - runs a bounded Hermes-style tool loop using OpenAI-compatible `tool_calls`
  - prepares provider-specific request payloads through the model boundary, including DeepSeek V4 thinking mode and safe `reasoning_content` replay for multi-step tool calls
  - executes Python tools until the model stops requesting tools or `agent.maxToolIterations` is reached
  - writes user/assistant messages to SQLite
  - emits desktop-compatible runtime events
  - emits `memory.context.used` diagnostics for per-turn memory source selection and keeps the most recent diagnostics per session in an in-memory ring buffer
  - requests Python audio output after the assistant message
- Python tracks the active running turn per session, emits `agent.turn.started` / `agent.turn.cancelled`, and exposes `POST /agent/cancel` for cooperative cancellation. This does not yet provide checkpoint/resume or forced provider-request termination.
- Python includes `turnId` in assistant stream/final events and `task.plan.updated` events produced during a turn, allowing desktop clients to associate live reasoning, assistant text, and plan progress with the correct user request.
- Python task worker now honors `reviewRequired`: successful worker output enters `blocked` with a review reason instead of `succeeded`, and `POST /tasks/{id}/approve` finalizes it. `POST /tasks/{id}/resume` returns blocked tasks to `queued`.
- Ask-tool permission requests cross the Python runtime boundary:
  - Python emits `tool.permission.request`
  - the TypeScript bridge relays it to desktop
  - desktop sends `tool.permission.response`
  - the bridge forwards it back to Python `/tools/permission`
- Python audio interface now has a practical default on macOS: runtime TTS auto-selects GPT-SoVITS when configured, otherwise uses local `say`/`afconvert` and emits `audio.tts-ready`.
- Python audio interface now includes local ASR. `asr.default: auto` selects `faster-whisper` when installed; Companion posts microphone audio to `/audio/transcribe`, then sends non-empty transcripts through the normal chat path.
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
  - `packages/amadeus/agent.py` still owns turn orchestration, the bounded tool-call loop, summaries, memory review, and final response timing.
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
  - Stable memory now lives in auditable Markdown files (`MEMORY.md` and `USER.md`) and is injected into the cached system prompt.
  - `ContextAssembler` now consumes a runtime memory manager. Stable Markdown memory stays in the prompt layer; the default `mem0_like_runtime` provider keeps the hybrid SQLite/session lanes and exposes derived artifacts such as summaries, sanitized FTS snippets, and default memory tools when no external provider is configured. Structured long-term `memory_items` are not preloaded into every turn; they are retrieved explicitly with `search_memory_items`. `configs/runtime.yaml` can switch back to `hybrid_runtime` or `builtin_runtime`, or disable cross-session fallback with `memory.globalRetrievalFallback: false`. If an external provider is configured, it replaces the runtime provider surface for prefetch and memory tools. Active plans, todos, task state, recent task results, and memory provider snippets are attached to the current user message as non-persistent reference context. `memory.context.used` diagnostics are retained in a per-session in-memory ring buffer and include retrieval provider metadata.
  - `search_memory` now has a per-tool model-output policy that keeps memory match metadata while capping model-context result count and snippet length.
  - `read_session_messages` is now available as a bounded, paginated transcript/log inspection tool with its own model-output policy.
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
- Added a supervised development stack entrypoint at `scripts/dev_stack.py`. `npm run dev` now starts the Python runtime, waits for `/runtime/health`, starts the TypeScript bridge, waits for `/health`, and then starts Electron; `npm run dev:legacy` preserves the old raw concurrent startup path.
- The dev supervisor fails fast on occupied runtime/bridge health endpoints by default, and supports `--reuse-existing` for intentionally attaching to already-running local services.
- Provider config tests now isolate `AMADEUS_LLM_PROVIDER` and provider-specific environment variables, so local `.env` credentials no longer break `tests.test_model`.
- Current validation passes: `python -m unittest tests.test_model`, `python -m py_compile scripts/dev_stack.py`, `npm run typecheck`, `npm test`, `npm run test:e2e`, and `python scripts/eval_runtime_contracts.py`. The supervised no-desktop stack was also verified on temporary ports `8890` / `8888`, with both health checks passing and ports released after shutdown.
- `apps/desktop-ui-next` is the production Main UI workbench. Electron loads it by default for Main UI, while the legacy vanilla renderer remains only behind `AMADEUS_MAIN_UI_LEGACY` and older E2E compatibility paths. The Vue workbench connects to the live WebSocket bridge and Python HTTP runtime for chat, session switching and Companion attach/view, turn-scoped plans, task details, timed messages, skills, memory diagnostics, MCP diagnostics, role-scoped runtime selection, and model/Live2D/TTS configuration.
- `apps/desktop-ui-next` replaces the legacy vanilla Main UI renderer, not the entire `apps/desktop` package. `apps/desktop` is still the Electron shell and owns Companion, native window lifecycle, IPC/preload wiring, global cursor tracking, desktop playback, and packaged Electron E2E entrypoints.

### Still Needed

- Continue consolidating the desktop UI now that Main UI and Companion are split. Companion should stay lightweight and Live2D/voice-focused; the Vue Main UI should remain the single production workbench for richer context, session switching, Companion attach/view, task details, permissions, memory, MCP, and configuration. Avoid adding parallel panels or reviving the legacy vanilla Main UI except as an explicit fallback.
- Migrate remaining Electron E2E assumptions off the legacy vanilla Main UI renderer, then remove `apps/desktop/src/renderer/main-ui` and its Vite entry. Keep `apps/desktop` itself as the Electron shell and Companion host.
- Implement the real CLI entry as an independent session client, defaulting to its own session ID unless explicitly attached elsewhere.
- Keep improving Main UI session switching and the explicit attach/view flow for Companion sessions now that current-session history restore is in place.
- Keep Electron end-to-end coverage aligned with that UI pass so layout and interaction regressions are caught while the surface is being simplified.
- Keep improving lipsync from the current provider-native plus phoneme-planned path, especially broader provider cue compatibility and better non-Latin mapping, while keeping desktop playback/rendering as the adapter and routing policy through harness events.
- Continue shrinking TypeScript bridge scaffolding now that the legacy turn loop is gone. `apps/server` should remain a transport/proxy layer, not an owner of agent, model-library, tool, memory, or audio turn logic.
- `tool.permission.response` is now always forwarded through the bridge to Python; the old “maybe a local TypeScript tool loop owns this request” branch has been removed from the production server path.
- Harden newly added practical tools only where real usage exposes gaps, such as automatic fallback from built-in `web_search` / `web_extract` to a provider-backed or `web-access` path, richer browser backend integration, safe URL opening, or user-approved desktop actions.
- Finish late ToolRuntime hardening only where real usage exposes gaps, such as richer context propagation, more diagnostic surfaces, or additional no-progress policies for new tools.
- Finish Memory v2 consolidation around context assembly quality, summary/profile policy, review quality, and overflow compaction behavior.
- After the UI pass, the next product step for skills should be a real import/install/editing flow that runs `scripts/validate_skills.py` during add/import, then refreshes runtime discovery without forcing a full manual restart. The current `skill_manage` path only covers approved local experience-skill saves, while `web-access` was imported manually as a project skill.
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

Memory v2 is no longer just planned. Stable Markdown memory, SQLite-backed message history and FTS retrieval, Mem0-like typed long-term memory items, explicit memory tools, memory review candidate audit records, accept/reject flows for exception-path candidates, automatic safe-candidate promotion, runtime memory config, schema metadata, item history, access stats, and safety filters are in place.

Remaining Phase 8 work is about making the memory behavior mature in practice: better summary/profile/retrieval policy, provider overflow compact-and-retry confidence, auto-promotion quality tuning, and lightweight diagnostics endpoints to understand why a fact was retrieved, proposed, accepted, rejected, or suppressed.

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
- `skills.py` now implements a runtime skill catalog with an always-on filtered system-prompt catalog, manifest-based cache invalidation, `skill_view`-driven full activation, and an approved `skill_manage` path for saving local experience skills. The bridge proxies read-only skills APIs, and the desktop renderer exposes a multi-select suggested-skills picker with local search/filtering, a short inline summary, and persisted selections. `live2d.py` owns the local model library but not the desktop renderer adapter.
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

- The first Python `tool_runtime` slice exists with registry/config loading, permission-aware schema selection, dispatch, cooperative cancellation, audit persistence, result compaction, repeated-failure guardrails, semantic no-progress guardrails, session workspace epoch invalidation for file-observing tools, and a `search_files` result policy. `read_file` uses explicit line-windowing instead of hidden compression and reports unsupported non-text file kinds; `patch` and `write_file` provide targeted-edit and whole-file write paths. Hermes-parity practical tools now cover terminal/process, web search/extract, browser bridge surface, vision endpoint bridge, clarify, and Python `execute_code`.
- The remaining work is mature runtime hardening: richer context propagation, additional per-tool result policies for high-volume outputs if needed, and continued tuning of semantic no-progress policies as real usage of the new tools lands.

### Phase 8: Agent Memory Optimization

Started.

- SQLite FTS-backed session search is implemented for raw conversation messages.
- Memory search now uses `jieba` tokenization plus bounded CJK n-gram fallback. The FTS index stores token-expanded message content for Chinese recall while results return the original transcript text.
- Memory retrieval tests now cover tokenizer expansion, Chinese and mixed-language FTS recall, session scoping, legacy FTS rebuild, structured-memory Chinese recall, and confidence ordering after token match.
- Python runtime exposes `GET /memory/search`.
- `search_memory` lets the model search current-session memory, with optional all-session search.
- `read_session_messages` lets the model read a bounded raw transcript window when exact session conversation wording is needed.
- Automatic memory prefetch injects relevant prior snippets into the current user message as non-persistent `<memory-context>`.
- Structured durable memory is no longer injected into every turn. Accepted `memory_items` are still built automatically, indexed, and retrieved explicitly through `search_memory_items`.
- `memory_provider.py` now has a runtime memory provider registry. The original `builtin_runtime` provider and previous `hybrid_runtime` provider are preserved, while the default `mem0_like_runtime` provider keeps the hybrid summary/FTS lanes and exposes structured memory tools. A configured external provider still replaces that prefetch/tool surface. Raw transcripts remain separate log data.
- Stable long-term memory is implemented with bounded role-scoped Markdown files under `data/roles/<roleId>/memory/`, with default-role migration fallback from the earlier `data/memory/` location.
- `read_memory` / `update_memory` expose controlled read and add/replace/remove operations for agent facts and user preferences.
- Conversation summary storage and load APIs are implemented with persisted SQLite records and `GET /memory/summary` / `POST /memory/summary`.
- Conversation summaries now track covered message ranges, are injected as reference-only context, and can be refreshed by automatic threshold compaction or manual `POST /memory/compact`.
- Python exposes recent in-memory context assembler diagnostics through `GET /memory/context/diagnostics`, scoped by session and bounded by `context.diagnosticsLimit`.
- Structured `memory_items` now persist durable `user` / `agent` / `project` facts in a Mem0-like format with `memoryType`, JSON `metadata`, `contentHash`, source ids, access stats, timestamps, soft deletion, and `memory_item_history`. They expose `GET /memory/items`, `GET /memory/items/history`, `POST /memory/items`, and `POST /memory/items/delete`, but do not inject active top items into model context by default.
- Explicit structured memory tools are now in place: `search_memory_items` reads durable facts without approval, uses hybrid vector/BM25/metadata ranking when BGE-M3 is configured, and falls back to BM25/SQL; `memory_add`, `memory_replace`, and `memory_forget` mutate one durable fact only through the `ask` permission path.
- Memory review candidates now provide durable audit records plus an exception queue: runtime review auto-promotes safe accepted candidates into `memory_items`, while `GET/POST /memory/review/candidates`, `POST /memory/review/accept`, and `POST /memory/review/reject` still manage pending/manual candidates.
- Background memory review runner can now be triggered with `POST /memory/review/run` or automatically after a completed turn when the threshold/cooldown gates allow it; it asks the provider to propose candidates from recent messages, applies safety/scope filters, stores candidate audit records, and automatically promotes safe candidates into durable `memory_items`.
- Memory review safety filters now block secret-like content, temporary debug/run state, uncertain claims, overly specific local/cache/generated paths, and obvious `user` / `agent` / `project` scope mismatches before candidates are persisted.
- Rejected memory review candidates suppress later identical suggestions for the same session/scope/content.
- Desktop still exposes the review loop for exception-path candidates: it lists pending candidates, lets the user trigger review manually, and sends Accept / Reject actions over the WebSocket bridge.
- Memory review job observability is now persisted in SQLite `memory_review_jobs`: every manual/automatic review records `running`, `completed`, `skipped`, or `failed` state, trigger, skip reason/error, source message range/count, proposed/saved/suppressed candidate counts, and duration.
- Python exposes `GET /memory/review/jobs`, the TypeScript bridge relays it as `memory.review.jobs`, and the desktop memory review panel shows the latest job summary next to the pending candidate count.
- Added the local BGE-M3 vector loop for Memory v2: Python exposes `GET /memory/embedding/config`, `POST /memory/embedding/deploy`, `POST /memory/embedding/cancel`, and `POST /memory/embedding/backfill` for `BAAI/bge-m3` local deployment, vector index coverage, and missing/stale `memory_items` backfill via FlagEmbedding. The Main UI Config Center Memory tab shows configuration, optional dependency state, model cache state, deployment phase, index ready/missing/stale counts, coverage percentage, and manual backfill controls. Runtime memory now defaults to `mem0_like_runtime`, preserving the hybrid FTS fallback lane while `search_memory_items` provides BGE-M3 vector + `memory_items_fts` BM25 + metadata-aware hybrid ranking for typed long-term memory items.
- Structured long-term memory now has its own `memory_items_fts` index. `memory_items` inserts/replaces/deletes keep that FTS index synchronized, durable fact search ranks BM25 matches over content/type/metadata, and `search_memory_items` plus `GET /memory/items` support simple metadata filters.
- Summary compaction is now token-budget-aware: runtime estimates context tokens before provider calls and after turns, loads its defaults from `configs/runtime.yaml`, supports explicit HTTP reload and environment overrides such as `AMADEUS_CONTEXT_MAX_TOKENS`, dynamically reduces the recent-turn keep window from a trigger-budget fraction, applies a capped floor for recent raw turns, and retries once after provider context-overflow errors.
- Tool-call transcripts now persist in SQLite messages: assistant tool-call decisions store `tool_calls`, tool results store `role=tool` with `tool_call_id` / `tool_name`, and later turns reload them as provider-safe OpenAI-style messages. History loading sanitizes tool pairs by dropping orphan tool results and adding stub results for retained assistant tool calls whose result was outside the loaded window. Summary compaction now aligns fold boundaries to avoid splitting `assistant(tool_calls)` from its matching `tool` results and includes tool-call metadata in summary source lines.
- Main UI transcript rendering now shows assistant tool-call decisions as Agent-side intermediate activity: tool calls render as collapsed name/argument cards by default, empty assistant bubbles are suppressed, Plan panels appear on assistant messages, and consecutive Agent messages within one user turn only show the avatar on the final Agent reply.
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

Started: the storage/API/UI foundation and in-process worker for session-scoped tasks are in place, with first-pass worker retry, stale-running recovery, the first task-graph persistence layer, dependency-aware runnable selection, isolated WorkerContext prompts, the first internal orchestrator skeleton, first-pass model-backed graph decomposition, first-pass graph repair, first-pass graph lifecycle events, first-pass root synthesis, first-pass graph runtime update publication, first-pass profile/toolset policy validation, an optional POSIX process runner, a single-task worker process entrypoint, and a subprocess launcher/supervisor runner with first-pass non-zero exit reclaim. Autonomous scheduling, richer graph UI semantics, deeper repair/eval loops, and full durable multi-process restart policy are not done.

- Added SQLite-backed `tasks` and `task_events`.
- Added the first long-task graph persistence slice: task rows now have optional root/plan-run/profile/acceptance/context/toolset/checkpoint/handoff fields, and SQLite stores `task_edges`, `task_attempts`, and normalized `task_artifacts` as first-class records while keeping existing task responses backward-compatible.
- Task statuses now use `queued`, `running`, `blocked`, `succeeded`, `failed`, and `cancelled`; legacy `done` rows are normalized to `succeeded`.
- Added Python runtime HTTP APIs: `GET /tasks`, `POST /tasks`, `GET /tasks/{id}/events`, and `POST /tasks/{id}/cancel`.
- Added task graph HTTP APIs: `GET /tasks/{id}/graph`, `GET /tasks/{id}/attempts`, `GET /tasks/{id}/artifacts`, controlled `POST /tasks/{id}/decompose` for applying a validated structured graph, `POST /tasks/{id}/dispatch` for submitting dependency-ready child tasks, and `POST /tasks/{id}/synthesize` for root task synthesis after children finish.
- Added task review/control APIs: `POST /tasks/{id}/resume` and `POST /tasks/{id}/approve`.
- Added model-facing `create_task`, `list_tasks`, and `cancel_task` tools for explicit session background work.
- Added a lightweight in-process task worker that claims queued tasks, runs the existing agent turn loop, writes `succeeded` / `failed` results, and cooperatively cancels running backing turns.
- Added Hermes-inspired reliability fields and transitions: `attemptCount`, `maxAttempts`, `nextRunAt`, retry scheduling back to `queued`, final failure after the attempt cap, and startup recovery of stale `running` tasks back to `queued`.
- Task runnable selection is now dependency-aware: queued tasks with unsatisfied incoming `task_edges` are not claimed by the worker until their dependencies reach the required status.
- Added the first isolated `WorkerContext` builder: in-process task workers now prompt the agent with task spec, acceptance criteria, root task summary, dependency artifacts, context hints, toolset bounds, and previous attempt history instead of using only task title/body or replaying the parent conversation.
- Task worker executions now create `task_attempts`, heartbeat the active attempt, finish attempts with result/error/checkpoint state, and write successful worker results as summary artifacts for downstream dependency handoff.
- Added first process execution slices: `TaskWorker` now runs behind a `TaskRunner` contract, the default remains the in-process thread-pool runner, `AMADEUS_TASK_RUNNER=process` can select an optional POSIX fork-backed runner, `amadeus.task_worker_entrypoint` can run one task in a dedicated process from `AMADEUS_TASK_ID` plus a SQLite database path, and `AMADEUS_TASK_RUNNER=subprocess` can launch that entrypoint as an external subprocess with bounded concurrency, `AMADEUS_TASK_RUN_ID`, `AMADEUS_WORKER_PROFILE`, parent-side wait/terminate handling, and first-pass non-zero exit reclaim back into the task retry/failure state machine.
- Added the first internal `OrchestratorService` skeleton. It can create root goals, ask the configured planning model for a fixed-shape JSON spec/task graph, repair one invalid model graph through the same fixed-shape JSON boundary, validate structured task graphs, reject dependency cycles and profile/toolset escalation, apply child tasks and edges into the existing task store, dispatch ready child tasks while respecting dependencies, review terminal child status, record durable graph lifecycle `task_events`, and synthesize terminal child results into the root task. Python HTTP exposes controlled decompose/dispatch/synthesize entrypoints that call this service; `auto: true` enables model-backed graph generation with repair-before-fallback and a conservative single-child fallback. Those HTTP graph operations also publish `task.updated` runtime events with graph-specific actions for WebSocket/desktop subscribers.
- Added `/runtime/events` NDJSON streaming plus TypeScript bridge subscription so worker `running` / `succeeded` / `failed` / `cancelled` updates are pushed to same-session WebSocket clients.
- Added TypeScript bridge proxying for task HTTP APIs.
- Main UI can restore and render active queued/running/blocked tasks.
- Main UI renders standard task artifact types (`file`, `diff`, `command_output`, `summary`, `link`) as typed cards instead of raw JSON where possible.

- Add scheduled reminders.
- Add daily brief.
- Add idle-time check-ins.
- Add richer scheduler/restart recovery beyond in-process stale-running reclaim.

### Phase 12: Advanced Agent Features

Started: the first restricted `delegate_task` research/search tool, session task worker, and Hermes-style prompt surface split are in place. Full sub-agent orchestration and richer durable multi-process restart policy are not done.

- Split the monolithic Python system prompt into a prompt assembler with per-role identity, core rules, dynamic tool routing hints, role workspace instructions, role-scoped stable memory, and skills catalog sections.
- Added optional `ToolSpec.prompt_hint` metadata and registry-driven prompt hint assembly so enabled tools contribute their own short routing guidance instead of relying on hard-coded agent prompt lines.
- Added role `workspacePath` support and prioritized workspace instruction loading with truncation and explicit lower-priority project-context framing. The runtime checks `.amadeus.md` / `AMADEUS.md`, then `AGENT.md` / `agents.md`, then `CLAUDE.md` / `claude.md`, then Cursor rules; roles without an explicit workspace default to the repository root. Project instructions are for architecture, conventions, constraints, status, and next-work context; user preferences belong in role-scoped `USER.md` memory, and agent identity belongs in role `SOUL.md`. Workspace instructions cannot override system safety, permissions, or runtime enforcement.
- Added Hermes-style role homes under `data/roles/<roleId>/`: `SOUL.md` is seeded on default/new roles and loaded before core runtime rules, while `MEMORY.md` and `USER.md` are scoped to the current session role. The `update_current_role_identity` ask-tool, `/roles/{roleId}/identity` API, and desktop Role editor `SOUL.md` field provide controlled identity updates.
- Verified the identity update path locally through the Python HTTP runtime: creating a new role, renaming it through `PUT /roles/{roleId}/identity`, and reading `data/roles/<roleId>/SOUL.md` confirmed the file content matches the API response.
- Added Role-scoped runtime selection: roles now persist `runtimeScope` with optional `tools`, `skills`, and `mcpServers` allowlists. The runtime uses the current session role to shrink tool schemas, tool routing hints, `<available_skills>`, skill lookup/activation, MCP server visibility, `/tools/list?sessionId=...`, `/skills/list?sessionId=...`, and direct `/tools/execute` enforcement. The Main UI Role settings page now exposes searchable multi-select controls backed by the current tools, skills, and MCP server inventories, with saved-but-currently-missing entries preserved as warning chips.
- Added MCP bridge first slice: `tools.mcp` config can discover HTTP JSON-RPC MCP servers through `tools/list`, expose remote tools as `mcp__<server>__<tool>`, and execute through `tools/call` while preserving normal ToolRuntime permission, timeout, cancellation, result-compaction, prompt-hint, and audit behavior.
- Added Main UI MCP management: the Main Console now has an MCP tab for enabling/disabling HTTP JSON-RPC MCP discovery, editing default permission, adding/removing server configs, saving to `configs/tools.yaml`, and immediately reloading the Python ToolRegistry so `/tools/list` reflects newly discovered `mcp__<server>__<tool>` tools without a manual runtime restart.
- Added MCP verification loop: `POST /tools/config/test` can test one HTTP JSON-RPC MCP server's `tools/list` discovery without saving it, the Main UI MCP tab exposes per-server Test diagnostics and discovered tool names, `scripts/dev_mcp_server.py` provides a deterministic local `echo` / `project_info` MCP server, and HTTP tests now cover config save, registry reload, `/tools/list`, and direct `/tools/execute` against a real in-test MCP server.
- Added Main UI MCP / ToolRuntime observability: the MCP tab now compares global discovery with `/tools/list?sessionId=...` effective role visibility, shows role-scope filtered tools, per-server discovered/visible counts, recent MCP failure codes and durations, all-tool audit records, denied/blocked permission outcomes, and persisted ToolRuntime metadata from `/tools/audit`.
- `scripts/dev_mcp_server.py --fixture hermes` now exposes a no-token Hermes-style HTTP JSON-RPC MCP fixture, mirroring local conversation/message read tools from `../hermes-agent/mcp_serve.py` (`conversations_list`, `conversation_get`, `messages_read`, `channels_list`) so Amadeus MCP discovery/execution can be tested with more realistic tool shapes without starting Hermes' stdio MCP server or configuring platform credentials.
- MCP schema names now normalize server/tool identifiers to model-safe snake-style names: spaces, dots, hyphens, and other non-identifier characters become `_`, so a server named `hermes-fixture` exposes tools such as `mcp__hermes_fixture__messages_read`.
- The task detail modal now surfaces retry/run timing, heartbeat/finish timing, localized event labels, typed artifacts, and existing cancel/rerun/approve/resume actions in one place, making task recovery and review flows easier to audit from Main UI.
- Added first task runner abstraction: `TaskWorker` now delegates execution to a `TaskRunner` boundary, with the existing thread-pool behavior represented by `InProcessTaskRunner`. A first optional POSIX `ProcessTaskRunner` is also available behind the same contract and can be selected with `AMADEUS_TASK_RUNNER=process`, while the default remains in-process. A single-task worker entrypoint is available at `amadeus.task_worker_entrypoint` for process-launched execution through `--task-id` / `AMADEUS_TASK_ID` and `--database` / `AMADEUS_MEMORY_DB`; `SubprocessTaskRunner` selects that path with `AMADEUS_TASK_RUNNER=subprocess`, passes run/profile env, and reclaims non-zero exits.
- Added first task worker durability slice: running tasks now persist `leaseOwner`, `leaseExpiresAt`, and `runnerKind` alongside the legacy `claimLock`; `TaskWorker` renews leases through a periodic heartbeat loop, startup recovery only requeues expired leases or legacy stale heartbeats, and terminal transitions clear lease state. Task artifacts are now normalized through the task domain module before storage/response, and Main UI task details expose worker lease diagnostics.
- Added task-state context: each turn can inject current queued/running/blocked session tasks into a reference-only `<active-tasks>` user-message block and recent succeeded/failed/cancelled outcomes into `<recent-tasks>`, with `taskLimit`, `recentTaskLimit`, and `taskResultChars` runtime config plus `memory.context.used` diagnostics sources.
- Added prompt-surface hardening: system prompt assembly now separates stable runtime rules from contextual workspace/tool/memory/skill sections, advertises enabled tool capabilities, includes runtime environment metadata, sanitizes context-like markup, and caches per-session prompt variants until tool/runtime config changes.
- Added first external memory provider boundary through `memory_provider.py`; a configured external provider becomes the active runtime memory provider for turn prefetch and memory tool exposure instead of stacking beside the built-in SQLite memory tools.
- Added `skill_manage` as an approval-gated local experience-skill save path, plus skill catalog filtering by platform/tool availability and manifest-based cache invalidation.
- Added deterministic runtime contract eval script at `scripts/eval_runtime_contracts.py` covering role identity, active/recent task context, task lifecycle, orchestrator graph repair/dispatch/synthesis, and MCP tool schema/execution contracts.
- Added supervised dev-stack startup through `scripts/dev_stack.py`, restoring the local P0 health signal and replacing the default raw concurrent `npm run dev` path with ordered startup plus health checks.
- Add full sub-agent orchestration abstraction.
- Add context compression.
- Add richer planning quality evals, child-agent runners, richer durable subprocess restart policy, explicit attempt-abandonment semantics, and worker-profile policy enforcement on top of the new task graph store, WorkerContext builder, internal orchestrator service, optional process runner, single-task worker entrypoint, and subprocess launcher.
- Add human approval checkpoints for destructive, sensitive, or low-confidence actions.
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
npm run dev:stack -- --no-desktop
npm run dev:stack -- --reuse-existing
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
OPENAI_MODEL=deepseek-v4-pro
DEEPSEEK_MODEL=deepseek-v4-pro
DEEPSEEK_THINKING_ENABLED=true
DEEPSEEK_REASONING_EFFORT=high
VITE_AGENT_WS_URL=ws://127.0.0.1:8788/ws
```

The API key is stored only in local `.env`, which is ignored by git.
