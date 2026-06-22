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
  | text input in desktop UI
  v
apps/desktop
  |
  | WebSocket user.message
  v
apps/server
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
  +--> model tool-decision call
  +--> Python tool execution
  +--> runtime event streaming
  +--> optional audio.tts-ready
  |
  v
apps/server
  |
  | relay NDJSON events to desktop
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
  | text / mouse / local UI events
  v
apps/desktop
  |
  | WebSocket / local IPC events
  v
apps/server
  |
  | HTTP / JSON runtime calls
  v
packages/amadeus
  |
  +--> agent.py        (active preferred turn path)
  +--> memory.py       (active SQLite message store)
  +--> tools/          (active Python tools)
  +--> audio.py        (active audio interface; noop TTS by default)
  +--> server.py       (active HTTP runtime)
  +--> model.py        (placeholder boundary)
  +--> skills.py       (placeholder boundary)
  +--> live2d.py       (placeholder boundary)
```

`packages/amadeus` also exposes TypeScript bridge modules that are active today:

```text
apps/server
  |
  +--> packages/amadeus/events.ts
  |      +--> shared runtime event types
  |
  +--> packages/amadeus/tools.ts
         +--> tool schema metadata
         +--> permission metadata
         +--> config loading
         +--> Python runtime bridge
         +--> TS fallback tool implementations
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
  +--> model
  +--> tools
  |      +--> concrete local tools
  |      +--> MCP bridge
  |      +--> scheduled tasks
  |
  +--> tool_runtime
  |      +--> effective tool registry
  |      +--> permission/config overlays
  |      +--> guardrails and audit records
  |
  +--> skills
  +--> harness
  +--> live2d
  +--> audio
```

## Python Runtime

`packages/amadeus` is the long-term agent brain. Current module status:

- `agent.py`: active conversation loop, tool-use policy, response/event streaming.
- `context.py`: active API-call-time context assembler for conversation summaries, accepted structured memories, relevant FTS retrieval, source budgets, and diagnostics. `AgentRuntime` keeps recent context diagnostics per session in an in-memory ring buffer.
- `memory.py`: active SQLite-backed message history.
- `tools/`: active concrete Python tool implementations and public registry entrypoint.
- `tool_runtime`: active tool registry construction, permission/config overlays, execution dispatch, structured results, timeout/cancellation, audit persistence, result compaction, and repeated-call guardrails.
- `audio.py`: active TTS/audio interface with an `auto` provider selector, config-gated GPT-SoVITS HTTP provider, and macOS `say` provider that can cache generated wav audio under the local audio library.
- `server.py`: active Python HTTP runtime surface, including local audio file serving and local Live2D model config/static asset serving for direct runtime use.
- `model.py`: active first-pass OpenAI-compatible provider boundary for `configs/providers.yaml` plus environment-backed provider config, JSON chat-completion requests, stream parsing, and classified provider error normalization.
- `harness/`: active first-pass harness boundary with a registry and Live2D harness that maps `assistant.state` events to `character.behavior`.
- `skills.py`: future reusable behavior boundary; currently placeholder only.
- `live2d.py`: active local Live2D model library boundary plus character command dataclasses.

Live2D and audio are not the agent brain. They are device interfaces that the Python runtime can command, while the actual rendering/playback remains in desktop-side adapters.

In the mature architecture, Live2D and audio are first-class harnesses. A harness is not a normal model-called tool. It is a runtime extension that can contribute prompt fragments, observe runtime events, emit device commands, expose capabilities, and register optional tools. This keeps Amadeus' differentiating character and voice features modular while preserving a generic agent core.

## Main Modules

### apps/desktop

Desktop app responsibilities:

- Create an Electron window with transparent background and always-on-top option.
- Render Live2D model.
- Provide chat input, compact settings, and status indicators.
- Display streaming replies.
- Show inline tool permission prompts.
- Play runtime audio when available and otherwise fall back to browser/Electron `speechSynthesis`.
- Drive current lipsync behavior locally.
- Receive behavior commands from the agent runtime.

Current note:

- The actual desktop Live2D and renderer behavior logic currently lives in `apps/desktop/src/renderer/main.ts`.
- `packages/live2d-stage` is still an intended package boundary, not the active implementation.

### apps/server

TypeScript bridge responsibilities today:

- Expose WebSocket and HTTP endpoints to the desktop app.
- Translate desktop events into Python runtime requests.
- Forward Python runtime events back to the desktop.
- Route desktop `tool.permission.response` events back to Python `/tools/permission`.

This layer should shrink over time.

### packages/amadeus

Python runtime responsibilities today:

- Own the preferred turn path.
- Own SQLite-backed message persistence for the preferred path.
- Own concrete Python tool execution for the preferred path.
- Emit structured runtime events such as `assistant.state`, `assistant.delta`, `assistant.message`, `tool.started`, `tool.finished`, `tool.permission.request`, `character.behavior`, and `audio.tts-ready`.

Python runtime responsibilities later:

- Own model provider abstractions cleanly.
- Own skills/workflows.
- Load and coordinate harnesses.
- Extend tool runtime policy for richer context propagation, semantic no-progress detection, and more per-tool result policies.
- Assemble richer context from summaries, profile memory, retrieved memory, task state, and harness prompt fragments.

### packages/live2d-stage

Intended Live2D responsibilities:

- Load models from `models/live2d`.
- Support expression and motion commands.
- Track model state such as idle, thinking, speaking, tool-running, and error.
- Provide lipsync parameter updates.
- Provide pointer-following and click reaction helpers.

Current note:

- This package is not yet the real implementation package.
- The current working Live2D adapter logic is still embedded in `apps/desktop/src/renderer/main.ts`.
- Local model storage is active under `models/live2d`. The Node bridge server serves the configured model to the desktop through `/live2d/config` and `/live2d/models/...` on `8788`; the Python runtime has the same local library boundary for later direct runtime use.

### packages/amadeus/audio.py

Audio responsibilities:

- Voice activity state.
- Text-to-speech interface.
- Local audio asset lookup under `packages/amadeus/assets/audio`.
- Generated TTS cache management when a real provider is added.

Current behavior:

- Desktop-side playback remains the adapter concern.
- The runtime emits `audio.tts-ready` only when Python audio returns a real `audioUrl`.
- If Python audio cannot generate a file, the desktop falls back to `speechSynthesis`.
- The default runtime configuration uses `tts.default: auto`: GPT-SoVITS is preferred when `GPT_SOVITS_BASE_URL` is configured, otherwise macOS uses `say`/`afconvert` as the local practical default.

### packages/amadeus/tools.ts

Tool responsibilities:

- Define OpenAI-compatible tool schema metadata.
- Support permission metadata.
- Load effective config from `configs/tools.yaml`.
- Bridge tool execution to the Python runtime in `packages/amadeus`.
- Keep TypeScript tool metadata only as bridge diagnostics/development scaffolding while the Python runtime owns active execution.

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
memory.review.candidates
memory.review.jobs
memory.review.updated
error
```

Current bridge to Python runtime endpoints:

```text
GET /health
GET /runtime/health
GET /tools/list
GET /tools/audit
POST /runtime/config/reload
POST /agent/turn
POST /tools/execute
POST /tools/permission
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
GET /live2d/models/{relativePath}
```

Planned but not yet implemented as the active current protocol:

- `/agent/cancel`
- `/agent/message`
- `audio.tts-fallback`
- `audio.lipsync-cues`
- desktop playback feedback events such as `audio.playback-started`, `audio.playback-ended`, and `audio.playback-error`

## Implementation Principle

Migrate toward the Python runtime without breaking the desktop loop:

- Keep Live2D model loading/rendering in the desktop adapter.
- Keep desktop permission UI on the desktop.
- Keep `apps/server` as the transport bridge while the Python turn path is becoming complete and well-tested.
- Prefer small vertical migrations: move one capability fully across the boundary before moving the next.
- Treat the current work as Phase 6 cleanup: parity confidence, integration coverage, and continued shrinking of bridge-owned runtime scaffolding.

More complex systems such as sub-agents, vector memory, MCP, and active scheduling should be added only after the basic desktop experience feels stable.
