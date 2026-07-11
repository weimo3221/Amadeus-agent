# Architecture

## Product Shape

Amadeus Agent is a desktop companion agent with a Live2D body. The character is not just a chat UI; it should react through facial expression, motion, speaking state, idle state, contextual behavior, tools, memory, and audio.

The long-term target architecture is a Python-owned agent runtime with TypeScript/Electron adapters around it:

- Desktop layer: renders the character and handles direct user interaction.
- TypeScript bridge layer: exposes WebSocket/HTTP transport to the desktop and forwards runtime work.
- Python runtime layer: owns the agent loop, model calls, memory, tools, skills, and device-interface planning.
- Harness layer: installable runtime extensions for Live2D, audio, desktop presence, and future device interfaces.

The desktop layer should stay thin. It should not own long-term memory, tool execution, provider-specific LLM logic, or agent planning.

The detailed maturity plan is tracked in [agent-maturity-upgrade-plan.md](agent-maturity-upgrade-plan.md). This file distinguishes between the current working architecture and the long-term target shape.

## Current Working Runtime Flow

Today the preferred path is already Python-first:

```text
User
  |
  | text / voice input in Companion / Main UI
  v
apps/desktop
  |
  | WebSocket user.message with surface/client/session metadata
  v
apps/server
  |
  | session room broadcast / Python relay
  |
  | POST /agent/turn
  v
packages/amadeus/server.py
  |
  | AgentRuntime.run_turn()
  v
packages/amadeus/agent.py
  |
  +--> SQLite history load/save
  +--> bounded model tool-call loop
  +--> Python tool execution until no more tool_calls
  +--> runtime event streaming
  +--> optional audio.tts-ready
  |
  v
apps/server
  |
  | relay NDJSON events to every client in the session
  v
apps/desktop
```

Current runtime failure behavior:

- `apps/server` reports `python_runtime_unavailable` when Python `/agent/turn` cannot be used.
- There is no second TypeScript model/tool loop.

So in current practice, `apps/server` is:

- the transport bridge for the Python path.

## Runtime Diagram

### Current implementation

```text
User
  |
  | text / global cursor / local UI events
  v
apps/desktop
  |
  +--> companion renderer (Live2D, transient bubbles, lightweight input)
  +--> desktop-ui-next     (larger Vue chat/workbench, no Live2D)
  |
  | WebSocket / local IPC events
  v
apps/server
  |
  +--> sessionId -> clients[] WebSocket rooms
  +--> surface-aware broadcast
  |
  | HTTP / JSON runtime calls
  v
packages/amadeus
  |
  +--> agent.py        (active preferred turn path)
  +--> memory.py       (active SQLite message, role, memory, task, and audit store)
  +--> scheduling.py   (active scheduled trigger parser/worker)
  +--> tools/          (active Python tools)
  +--> audio.py        (active ASR/TTS interface with auto provider selection)
  +--> server.py       (active HTTP runtime)
  +--> model.py        (active OpenAI-compatible provider boundary)
  +--> skills.py       (active skill catalog boundary)
  +--> live2d.py       (active local Live2D model library boundary)
```

`packages/amadeus` also exposes TypeScript bridge modules that are active today:

```text
apps/server
  |
  +--> packages/amadeus/events.ts
  |      +--> shared runtime event types
  |
  +--> packages/amadeus/tools.ts
       +--> TypeScript tool bridge types
       +--> Python /tools/list and /tools/execute helpers
       +--> desktop/server diagnostics helpers
```

### Long-term target

```text
User
  |
  | text / voice / mouse / desktop events
  v
apps/desktop
  |
  | WebSocket / local IPC events
  v
apps/server (thin transport bridge)
  |
  | HTTP / JSON runtime calls
  v
packages/amadeus
  |
  +--> agent
  +--> memory
  +--> memory_embeddings
  +--> model
  +--> tools
  |      +--> concrete local tools
  |      +--> MCP bridge
  |      +--> scheduled companion messages
  |      +--> persistent session todos
  |
  +--> tool_runtime
  |      +--> effective tool registry
  |      +--> permission/config overlays
  |      +--> guardrails, workspace epoch, and audit records
  |
  +--> skills
  +--> harness
  +--> live2d
  +--> audio
```

## Python Runtime

`packages/amadeus` is the long-term agent brain. Current module status:

- `agent.py`: active conversation loop, bounded Hermes-style tool-use policy, runtime memory manager wiring, response/event streaming, turn-id propagation for assistant/plan events, and cached per-session system prompt assembly.
- `context.py`: active API-call-time context assembler for memory-provider output, active todos, task state, source budgets, and diagnostics. Stable Markdown memory stays in the prompt layer. With the default `mem0_like_runtime` provider active, SQLite/session-derived artifacts include conversation summaries, query-filtered typed long-term memories, optional BGE-M3 vector-ranked memory items, and relevant FTS retrieval. Per-turn reference context such as active plans, todos, tasks, recent task outcomes, FTS snippets, and external memory is appended to the current user message as non-persistent reference data. `AgentRuntime` keeps recent context diagnostics per session in an in-memory ring buffer.
- `memory.py`: active SQLite-backed message history, roles/sessions, Mem0-like structured long-term memory, item history, derived memory item embeddings, task state, task events, task plan runs, scheduled messages, persistent todos, and audit records.
- `memory_embeddings.py`: active local embedding bridge for Memory v2. It resolves BGE-M3 configuration, creates the local FlagEmbedding provider, computes memory item embedding text, reports index coverage, and runs missing/stale vector backfill.
- `memory_query.py`: active memory query tokenizer. It uses `jieba` plus bounded CJK n-grams to expand Chinese/mixed-language queries and FTS index content while keeping returned transcript text unchanged.
- `memory_provider.py`: runtime memory provider layer. Exactly one provider is active at a time. The default `mem0_like_runtime` local provider exposes derived session memory artifacts, typed long-term memory items, optional BGE-M3 vector/text hybrid recall, and the SQLite memory tools; `hybrid_runtime` and `builtin_runtime` remain compatibility options. An external provider replaces that runtime prefetch/tool surface when configured. Raw session transcripts remain in `memory.py` and are read through explicit transcript tools/APIs.
- `scheduling.py`: active schedule parser plus in-process scheduled trigger worker. It supports one-shot durations/timestamps, recurring intervals, common daily/weekly/monthly cron shapes, repeat counts, lifecycle event publication, direct message delivery, and `agent_task` mode that creates tracked background tasks at fire time.
- `tools/`: active concrete Python tool implementations and public registry entrypoint.
- `tool_runtime`: active tool registry construction, permission/config overlays, execution dispatch, structured results, timeout/cancellation, audit persistence, result compaction, session workspace epoch propagation, and repeated-call guardrails.
- `audio.py`: active ASR/TTS/audio interface. TTS uses an `auto` provider selector, config-gated GPT-SoVITS HTTP provider, and macOS `say` provider that can cache generated wav audio under the local audio library. ASR uses an `auto` provider selector that chooses local `faster-whisper` when installed.
- `server.py`: active Python HTTP runtime surface, including local audio file serving and local Live2D model config/static asset serving for direct runtime use.
- `model.py`: active first-pass OpenAI-compatible provider boundary for `configs/providers.yaml` plus environment-backed provider config, JSON chat-completion requests, stream parsing, and classified provider error normalization.
- `harness/`: active first-pass harness boundary with a registry and Live2D harness that maps `assistant.state` plus configurable audio playback feedback behaviors to `character.behavior`.
- `skills.py`: active reusable behavior boundary; owns skill discovery, metadata/view APIs, filtered skill catalog prompt assembly, explicit turn-level skill prompt injection, and approved experience-skill creation.
- `live2d.py`: active local Live2D model library boundary plus character command dataclasses.

Live2D and audio are not the agent brain. They are device interfaces that the Python runtime can command, while the actual rendering/playback remains in desktop-side adapters.

In the mature architecture, Live2D and audio are first-class harnesses. A harness is not a normal model-called tool. It is a runtime extension that can contribute prompt fragments, observe runtime events, emit device commands, expose capabilities, and register optional tools. This keeps Amadeus' differentiating character and voice features modular while preserving a generic agent core.

## Main Modules

### apps/desktop

Desktop app responsibilities:

- Create separate Electron surfaces for Companion and Main UI.
- Render Live2D only in Companion.
- Keep Companion as a transparent frameless always-on-top desktop presence with lightweight input and transient streaming reply bubbles.
- Keep Main UI as the larger chat/workbench surface without Live2D.
- Accept voice input from Companion through a microphone orb, local `MediaRecorder`, bridge `/audio/transcribe`, and Python ASR.
- Show inline tool permission prompts.
- Play runtime audio when available and otherwise fall back to browser/Electron `speechSynthesis`.
- Drive current lipsync behavior locally.
- Receive behavior commands from the agent runtime.
- Sample the global desktop cursor in the Electron main process and send it to Companion for gaze tracking and panel visibility.

Current note:

- The actual Companion Live2D and lightweight renderer behavior logic currently lives in `apps/desktop/src/renderer/companion/main.ts`.
- The larger chat/workbench renderer is `apps/desktop-ui-next`; Electron loads it by default and keeps the legacy Main UI available behind `AMADEUS_MAIN_UI_LEGACY`.
- Companion panel visibility is not DOM hover-driven. The renderer shows the panel only when the global cursor point is inside the Companion window bounds and hides it 1.5 seconds after the cursor leaves.
- Live2D model fit is configured from `configs/runtime.yaml` through `/live2d/config` (`desktop.companionLive2dScale`, `desktop.companionLive2dOffsetX`, `desktop.companionLive2dOffsetY`).
- `packages/live2d-stage` is still an intended package boundary, not the active implementation.

### apps/desktop-ui-next

Production Main UI workspace:

- A Vue 3 + Vite + TypeScript + Tailwind CSS v4 workspace that now provides the default Electron Main UI.
- It connects to the TypeScript bridge WebSocket and Python HTTP runtime for real chat, session history, tasks, timed messages, skills, memory, runtime/model configuration, Live2D model management, and audio/TTS configuration.
- It owns the larger workbench experience while Companion remains the lightweight Live2D surface.
- Layout: `AppSidebar`, `AppHeader`, chat/workspace views, task and timed-message views, settings/config center, and reusable `Am*` UI components.
- Reusable component set under `src/components/ui` (`AmButton`, `AmInput`, `AmSelect`, `AmCard`, `AmTag`, `AmTabs`, `AmTable`, `AmModal`, `AmEmptyState`, `AmLoading`) and shared design tokens in `src/styles/main.css`.

### apps/server

TypeScript bridge responsibilities today:

- Expose WebSocket and HTTP endpoints to the desktop app.
- Parse and validate WebSocket `surface` and `sessionId` parameters.
- Track `sessionId -> clients[]` rooms and broadcast runtime events to every client in the same session.
- Translate desktop events into Python runtime requests.
- Forward Python runtime events back to the desktop.
- Forward per-client desktop capability and audio feedback metadata to Python for harness policy.
- Route desktop `tool.permission.response` events back to Python `/tools/permission`.
- Proxy runtime HTTP surfaces used by desktop clients, including `/live2d/*`, `/audio/*`, `/scheduled-jobs`, `/tasks`, `/skills`, and session/memory endpoints. Binary audio upload for ASR is forwarded as a `Uint8Array` request body.

This layer should shrink over time.

### packages/amadeus

Python runtime responsibilities today:

- Own the preferred turn path.
- Own SQLite-backed message persistence for the preferred path.
- Own session memory count/reset semantics for the preferred path, exposed through `/memory/count` and `/memory/reset`.
- Own roles, per-role `SOUL.md` identity files, role-scoped `MEMORY.md` / `USER.md`, role `workspacePath`, default workspace assignment to the repository root, and workspace instruction loading for per-session prompt assembly. Instruction file priority is `.amadeus.md` / `AMADEUS.md`, then `AGENT.md` / `agents.md`, then `CLAUDE.md` / `claude.md`, then Cursor rules. User-specific preferences stay in role-scoped `USER.md` memory rather than project instructions.
- Own concrete Python tool execution for the preferred path.
- Own persisted session tasks as the execution unit for longer-running work, with task metadata (`kind`, `source`, `parentTaskId`, `planItemId`, `workerType`, review/artifact fields), in-process worker execution, retry scheduling, stale-running recovery, blocked review gates, and typed artifacts. Tasks can be linked to visible plan items through `planItemId`; worker lifecycle updates move linked plan items to `in_progress`, `completed`, `pending`, or `cancelled`.
- Own the SQLite-backed latest session plan state used by `update_plan`, plus persisted `plan_runs` keyed by `turnId`. Desktop treats model plans as turn-scoped UI: runtime `turnId` binds `task.plan.updated` to the initiating user message, and the visible plan panel is archived/restored under that turn when the assistant final message arrives.
- Own persisted scheduled triggers and persistent session todos. Scheduled jobs default to `message` mode, which writes a timed assistant message, and also support `agent_task` mode, which creates a tracked background task and submits it to the task worker.
- Own scheduled-job terminal state (`completed`, `cancelled`, `failed`) and emit `scheduled.updated`; Main UI fetches all statuses so completed timed messages remain visible and shows whether a schedule delivered a message or triggered a task.
- Own ASR/TTS provider selection and expose `/audio/transcribe`, `/audio/speak`, `/audio/config`, `/audio/voices`, and local generated audio files.
- Emit structured runtime events such as `assistant.state`, `assistant.delta`, `assistant.message`, `task.plan.updated`, `tool.started`, `tool.finished`, `tool.permission.request`, `scheduled.updated`, `character.behavior`, and `audio.tts-ready`.

Python runtime responsibilities later:

- Extend tool runtime policy for richer context propagation, semantic no-progress detection, and more per-tool result policies.
- Mature skills/workflows beyond the current catalog, view, and approved `skill_manage` experience-save path.
- Mature long-running execution beyond the current in-process task and scheduled-message workers into durable leases, checkpoint/resume, and richer user-facing notification policy.
- Assemble richer context from task state, harness prompt fragments, and role/workspace instructions beyond the current summaries, structured memory, and retrieval path.

### packages/live2d-stage

Intended Live2D responsibilities:

- Load models from `models/live2d`.
- Support expression and motion commands.
- Track model state such as idle, thinking, speaking, tool-running, and error.
- Provide lipsync parameter updates.
- Provide pointer-following and click reaction helpers.

Current note:

- This package is not yet the real implementation package.
- The current working Live2D adapter logic is still embedded in `apps/desktop/src/renderer/companion/main.ts`.
- Local model storage is active under `models/live2d`. The Python runtime now owns the Live2D model library boundary, including configured-model reads, model listing, model selection persistence, and model asset serving. The Node bridge keeps the desktop-facing `8788` origin by proxying `/live2d/*` to Python and rewriting model URLs back to bridge-relative URLs.

### packages/amadeus/audio.py

Audio responsibilities:

- Voice activity state.
- Text-to-speech interface.
- Speech-to-text interface.
- Local audio asset lookup under `packages/amadeus/assets/audio`.
- Generated TTS cache management for provider-generated audio.

Current behavior:

- Desktop-side playback remains the adapter concern.
- The runtime emits `audio.tts-ready` only when Python audio returns a real `audioUrl`.
- If Python audio cannot generate a file, the desktop falls back to `speechSynthesis`.
- The default runtime configuration uses `tts.default: auto`: GPT-SoVITS is preferred when `GPT_SOVITS_BASE_URL` is configured, otherwise macOS uses `say`/`afconvert` as the local practical default.
- The default ASR configuration uses `asr.default: auto`: local `faster-whisper` is selected when installed. Companion records `webm/opus` microphone clips and submits them to `/audio/transcribe`; the transcribed text is fed back into the normal chat path.

### packages/amadeus/tools.ts

Tool responsibilities:

- Provide TypeScript bridge types and helper clients for Python `/tools/list` and `/tools/execute`.
- Keep desktop/server diagnostics on the Python-owned effective tool state.
- Avoid mirroring concrete tool schemas, permissions, or handlers in TypeScript. Active tool metadata and execution live in Python.

### packages/amadeus/events.ts

Shared responsibilities:

- Runtime event types.
- Common payload shapes.
- Shared server/desktop event contracts.

## Current Event Protocol

Use explicit, serializable events between desktop, bridge, and Python runtime.

Current desktop to server events:

```text
user.message
session.reset
tool.permission.response
desktop.capabilities
audio.playback-started
audio.playback-ended
audio.playback-error
memory.review.list
memory.review.run
memory.review.accept
memory.review.reject
```

Current server to desktop events:

```text
server.hello
memory.updated
memory.context.used
assistant.delta
assistant.message
assistant.state
character.behavior
audio.tts-ready
tool.started
tool.finished
tool.audit
tool.permission.request
task.plan.updated
task.updated
scheduled.updated
memory.review.candidates
memory.review.jobs
memory.review.updated
error
```

Current bridge to Python runtime endpoints:

```text
GET /health
GET /runtime/health
GET /runtime/feedback
POST /audio/transcribe
GET /tools/list
GET /tools/audit
GET /skills/list
GET /skills/view
GET /tasks
GET /tasks/{id}/events
GET /runtime/events
POST /runtime/config/reload
POST /runtime/feedback
POST /agent/turn
POST /agent/cancel
POST /tools/execute
POST /tools/permission
POST /tasks
POST /tasks/{id}/cancel
GET /memory/count
GET /memory/messages
GET /memory/context/diagnostics
GET /memory/search
GET /memory/items
GET /memory/summary
GET /memory/review/candidates
GET /memory/review/jobs
POST /memory/messages
POST /memory/items
POST /memory/items/delete
POST /memory/review/candidates
POST /memory/review/accept
POST /memory/review/reject
POST /memory/review/run
POST /memory/summary
POST /memory/compact
POST /memory/reset
POST /audio/speak
GET /audio/files/{relativePath}
GET /live2d/config
GET /live2d/models
POST /live2d/select
GET /live2d/models/{relativePath}
```

Planned but not yet implemented as the active current protocol:

- `/agent/message`
- `audio.tts-fallback`

## Implementation Principle

Migrate toward the Python runtime without breaking the desktop loop:

- Keep Live2D model loading/rendering in the desktop adapter.
- Keep desktop permission UI on the desktop.
- Keep `apps/server` as the transport bridge now that the Python turn path is complete for the active runtime.
- Prefer small vertical migrations: move one capability fully across the boundary before moving the next.
- Treat the current work as desktop/runtime stabilization: parity confidence, integration coverage, and continued shrinking of bridge-owned runtime scaffolding.

More complex systems such as MCP, durable multi-process task workers, richer sub-agents, and active scheduling should be added only after the basic desktop experience feels stable.
