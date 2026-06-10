# Architecture

## Product Shape

Amadeus Agent is a desktop companion agent with a Live2D body. The character is not just a chat UI; it should react through facial expression, motion, speaking state, idle state, contextual behavior, tools, memory, and audio.

The target architecture is a Python-owned agent runtime with TypeScript/Electron adapters around it:

- Desktop layer: renders the character and handles direct user interaction.
- TypeScript bridge layer: exposes WebSocket/HTTP transport to the desktop and forwards runtime work.
- Python runtime layer: owns the agent loop, model calls, memory, tools, skills, and device-interface planning.
- Harness layer: installable runtime extensions for Live2D, audio, desktop presence, and future device interfaces.

The desktop layer should stay thin. It should not own long-term memory, tool execution, provider-specific LLM logic, or agent planning.

The detailed maturity plan is tracked in [agent-maturity-upgrade-plan.md](agent-maturity-upgrade-plan.md). This architecture file is the compact target shape; the maturity plan is the implementation sequence.

## Runtime Diagram

```text
User
  |
  | text / voice / mouse / desktop events
  v
apps/desktop
  |
  | WebSocket / local IPC events
  v
apps/server (TypeScript bridge)
  |
  | HTTP / JSON runtime calls
  v
packages/amadeus
  |
  +--> agent
  |      +--> conversation loop
  |      +--> planning
  |      +--> response/event streaming
  |
  +--> memory
  |      +--> raw conversation history
  |      +--> session summaries
  |      +--> user profile facts
  |      +--> retrieval
  |
  +--> model
  |      +--> hosted model adapters
  |      +--> local model adapters
  |
  +--> tools
  |      +--> concrete local tools
  |      +--> MCP bridge
  |      +--> scheduled tasks
  |
  +--> skills
  |      +--> reusable behaviors
  |      +--> composed workflows
  |
  +--> harness
  |      +--> Live2D harness
  |      +--> audio harness
  |      +--> desktop harness
  |      +--> future device/runtime adapters
  |
  +--> live2d
  |      +--> character command interface
  |      +--> expression/motion/lipsync cues
  |
  +--> audio
         +--> ASR/TTS command interface
         +--> local audio asset selection
         +--> generated TTS cache
         +--> audio playback metadata

apps/desktop
  |
  +--> packages/live2d-stage
         +--> actual Live2D model loading/rendering
         +--> expression control
         +--> motion control
         +--> pointer-following and click reaction
```

`packages/amadeus` also exposes small TypeScript bridge modules while the Python runtime takes ownership of behavior:

```text
apps/server
  |
  +--> packages/amadeus/events.ts
  |      +--> event protocol types
  |
  +--> packages/amadeus/tools.ts
         +--> tool schema metadata
         +--> permission metadata
         +--> Python runtime bridge
```

## Python Runtime

`packages/amadeus` is the long-term agent brain. Its internal modules are peers:

- `agent`: conversation loop, planning, tool-use policy, response/event streaming.
- `memory`: raw history, summaries, user profile facts, retrieval.
- `model`: OpenAI-compatible, local model, and future provider adapters.
- `tools`: concrete tool implementations.
- `skills`: reusable behaviors built from model, memory, and tools.
- `live2d`: interface contract for character state, expressions, motions, and lipsync cues.
- `audio`: interface contract for TTS, ASR, and audio output.

Live2D and audio are not the agent brain. They are device interfaces that the Python runtime can command, while the actual rendering/playback remains in desktop-side adapters.

In the mature architecture, Live2D and audio are first-class harnesses. A harness is not a normal model-called tool. It is a runtime extension that can contribute prompt fragments, observe runtime events, emit device commands, expose capabilities, and register optional tools. This keeps Amadeus' differentiating character and voice features modular while preserving a generic agent core.

## Main Modules

### apps/desktop

Desktop app responsibilities:

- Create an Electron window with transparent background and always-on-top option.
- Render Live2D model.
- Provide chat input, compact settings, and status indicators.
- Capture user events: text, voice, mouse hover, click, drag, and hotkeys.
- Display streaming replies.
- Play audio and drive lipsync until audio is fully behind the Python audio interface.
- Receive behavior commands from the agent runtime.

The desktop app communicates through the event protocol instead of importing runtime internals.

### apps/server

TypeScript bridge responsibilities:

- Expose WebSocket and HTTP endpoints to the desktop app.
- Translate desktop events into Python runtime requests.
- Forward Python runtime events back to the desktop.
- Own user-facing permission prompts while migration is in progress.
- Keep compatibility with the current OpenAI/tool loop until the Python agent loop replaces it.

This layer should shrink over time.

### packages/amadeus

Python runtime responsibilities:

- Own the agent loop.
- Own memory persistence and retrieval.
- Own concrete tool execution.
- Own model provider adapters.
- Own skills/workflows.
- Emit structured commands for Live2D/audio interfaces.
- Load and coordinate harnesses.
- Enforce tool permissions, tool timeouts, tool guardrails, and audit logging.
- Assemble context from persona, summaries, profile memory, retrieved memory, recent messages, task state, and harness prompt fragments.

The immediate migration target is to move `/agent/turn` into Python. After that, `apps/server` should only relay desktop events and Python runtime events.

### packages/amadeus/harness

Harness responsibilities:

- Define installable runtime extensions.
- Advertise capabilities such as supported event types, tools, prompts, and device outputs.
- Observe agent events such as `assistant.state`, `tool.started`, `assistant.message`, and `error`.
- Emit device-facing events such as `character.behavior`, `audio.tts-ready`, and `audio.lipsync-cues`.
- Keep Live2D/audio policy in Python while leaving rendering/playback in desktop adapters.

Initial harnesses:

- `live2d`: maps agent state, tool state, pointer/click input, and playback feedback into character behavior.
- `audio`: owns TTS provider selection, fallback policy, generated audio cache, ASR event contracts, and lipsync cue generation.
- `desktop`: future bridge for notifications, proactive prompts, and desktop presence.

### packages/live2d-stage

Live2D responsibilities:

- Load models from `models/live2d`.
- Support expression and motion commands.
- Track model state: idle, listening, thinking, speaking, tool-running, error.
- Provide lipsync parameter updates.
- Provide pointer-following and click reaction helpers.

This package is an adapter for the `amadeus/live2d` command interface. The Python runtime decides what should happen; the desktop adapter knows how to make the rendered model do it.

### amadeus/audio

Audio responsibilities:

- Voice activity state.
- Speech-to-text integration.
- Text-to-speech integration.
- Local audio asset lookup under `packages/amadeus/assets/audio`.
- Generated TTS cache management.
- Lipsync cue playback.

This module defines the audio command interface. Desktop-side playback remains an adapter concern: it plays runtime-provided `audioUrl` values and drives lipsync while playback is active. If Python audio cannot generate audio for a text request yet, the desktop may fall back to system `speechSynthesis`.

As the audio harness matures, desktop playback should send feedback events back to the runtime:

- `audio.playback-started`
- `audio.playback-ended`
- `audio.playback-error`

Those events let the Live2D harness coordinate speaking state and lipsync with real playback instead of relying on fixed timers.

Local files under `voices/` and `sfx/` are fixed clips. They should not be treated as a complete voice. Arbitrary assistant replies require a TTS provider that generates files into `cache/` or returns a playable URL.

### packages/amadeus/tools.ts

Tool responsibilities:

- Define OpenAI-compatible tool schema metadata.
- Support permission metadata.
- Bridge tool execution to the Python runtime in `packages/amadeus`.
- Keep TypeScript fallback tools only as temporary development scaffolding.

### packages/amadeus/events.ts

Shared responsibilities:

- Runtime event types.
- Config schemas.
- Common IDs and error shapes.

## Event Protocol

Use events between desktop, bridge, and Python runtime. Keep them explicit and serializable.

Desktop to server:

```text
user.message
user.voice-start
user.voice-chunk
user.voice-end
desktop.capabilities
character.capabilities
audio.capabilities
desktop.pointer
desktop.character.click
desktop.hotkey
session.reset
tool.permission.response
audio.playback-started
audio.playback-ended
audio.playback-error
```

Server to desktop:

```text
server.hello
memory.updated
assistant.delta
assistant.message
assistant.state
character.behavior
character.lipsync
tool.started
tool.finished
tool.permission.request
audio.tts-ready
audio.tts-fallback
audio.lipsync-cues
error
```

Bridge to Python runtime:

```text
GET /health
POST /agent/turn
POST /agent/cancel
POST /agent/message
POST /tools/execute
GET /tools/list
POST /tools/permission
GET /memory/count
GET /memory/messages
POST /memory/messages
POST /memory/reset
POST /audio/speak
GET /audio/files/{relativePath}
```

Python runtime to bridge responses should be serializable event batches or streams using the same semantic event names where possible.

## Implementation Principle

Migrate toward the Python runtime without breaking the desktop loop:

- Keep Live2D model loading/rendering in the desktop adapter.
- Move agent, memory, model adapters, tool execution, skills, and audio planning into Python.
- Keep TypeScript packages as transport/schema/adapter surfaces only where they earn their keep.
- Prefer small vertical migrations: move one capability fully across the boundary before moving the next.
- The current migration target is Python runtime ownership of `/agent/turn`, because the main architecture debt is that the TypeScript bridge still owns LLM calls, tool loop, memory writes, and behavior dispatch.

More complex systems such as sub-agents, vector memory, MCP, and active scheduling should be added only after the basic desktop experience feels stable.
