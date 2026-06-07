# Architecture

## Product Shape

Amadeus Agent is a desktop companion agent with a Live2D body. The character is not just a chat UI; it should react through facial expression, motion, speaking state, idle state, contextual behavior, tools, memory, and audio.

The target architecture is a Python-owned agent runtime with TypeScript/Electron adapters around it:

- Desktop layer: renders the character and handles direct user interaction.
- TypeScript bridge layer: exposes WebSocket/HTTP transport to the desktop and forwards runtime work.
- Python runtime layer: owns the agent loop, model calls, memory, tools, skills, and device-interface planning.

The desktop layer should stay thin. It should not own long-term memory, tool execution, provider-specific LLM logic, or agent planning.

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
user.voice-end
desktop.pointer
desktop.hotkey
session.reset
tool.permission.response
```

Server to desktop:

```text
server.hello
memory.updated
assistant.delta
assistant.message
assistant.state
character.behavior
tool.started
tool.finished
tool.permission.request
audio.tts-ready
error
```

Bridge to Python runtime:

```text
GET /health
POST /agent/message
POST /tools/execute
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
- The current migration target is audio output, because it has a clean device-adapter boundary: Python chooses or generates audio; desktop plays it.

More complex systems such as sub-agents, vector memory, MCP, and active scheduling should be added only after the basic desktop experience feels stable.
