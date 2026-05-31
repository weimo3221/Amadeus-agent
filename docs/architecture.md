# Architecture

## Product Shape

Amadeus Agent is a desktop companion agent with a Live2D body. The character is not just a chat UI; it should react through facial expression, motion, speaking state, idle state, and contextual behavior.

The system is split into two runtime layers:

- Desktop layer: renders the character and handles direct user interaction.
- Agent runtime layer: handles language model calls, memory, tools, and behavior decisions.

The desktop layer should stay thin. It should not own long-term memory, tool execution, or provider-specific LLM logic.

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
apps/server
  |
  +--> packages/agent-core
  |      +--> LLM provider
  |      +--> tool calling
  |      +--> response streaming
  |
  +--> packages/character
  |      +--> persona
  |      +--> emotion policy
  |      +--> behavior mapping
  |
  +--> packages/memory
  |      +--> session memory
  |      +--> profile memory
  |      +--> episodic memory
  |
  +--> packages/tools
  |      +--> local tools
  |      +--> MCP bridge
  |      +--> scheduled tasks
  |
  +--> packages/audio
         +--> ASR
         +--> TTS
         +--> lipsync cues

apps/desktop
  |
  +--> packages/live2d-stage
         +--> model loading
         +--> expression control
         +--> motion control
         +--> idle/speaking/listening states
```

## Main Modules

### apps/desktop

Desktop app responsibilities:

- Create an Electron window with transparent background and always-on-top option.
- Render Live2D model.
- Provide chat input, compact settings, and status indicators.
- Capture user events: text, voice, mouse hover, click, drag, and hotkeys.
- Display streaming replies.
- Play TTS audio and drive lipsync.
- Receive behavior commands from the agent runtime.

The desktop app should communicate through a small event protocol instead of importing server internals.

### apps/server

Local runtime responsibilities:

- Expose WebSocket and HTTP endpoints to the desktop app.
- Manage sessions.
- Run the agent loop.
- Persist memory.
- Execute tools with permission checks.
- Normalize model providers behind one interface.

In early versions this can be a Node.js process. If Python agent frameworks become necessary later, Python can be added as a worker process instead of replacing the desktop stack.

### packages/live2d-stage

Live2D responsibilities:

- Load models from `models/live2d`.
- Support expression and motion commands.
- Track model state: idle, listening, thinking, speaking, tool-running, error.
- Provide lipsync parameter updates.
- Provide pointer-following and click reaction helpers.

This package should borrow ideas from AIRI's `packages/stage-ui-live2d`, but keep the public API smaller at first.

### packages/character

Character responsibilities:

- Load persona config from `configs/character.yaml`.
- Convert assistant state into character behavior.
- Map semantic emotion to Live2D expressions and motions.
- Keep style rules separate from model provider code.

Example output:

```json
{
  "emotion": "curious",
  "expression": "smile",
  "motion": "tilt_head",
  "speakingStyle": "soft"
}
```

### packages/agent-core

Agent responsibilities:

- Build prompts from persona, memory, user input, and tool state.
- Stream LLM responses.
- Decide and execute tool calls.
- Emit structured side-channel events for emotion, action, and UI state.
- Keep provider-specific code behind adapters.

The first implementation should support OpenAI-compatible chat APIs. This covers OpenAI, OpenRouter, LM Studio, Ollama-compatible gateways, and many local servers.

### packages/memory

Memory responsibilities:

- Store raw conversation history.
- Summarize old sessions.
- Maintain user profile facts.
- Retrieve relevant memories for new conversations.

Start with SQLite. Add vector search only after simple retrieval is insufficient.

### packages/tools

Tool responsibilities:

- Define a typed tool registry.
- Support permission levels.
- Provide first-party tools:
  - current time
  - web search placeholder
  - local file search
  - open URL or app
  - screenshot placeholder
  - reminder placeholder
- Add MCP bridge after the local tool API stabilizes.

### packages/audio

Audio responsibilities:

- Voice activity state.
- Speech-to-text integration.
- Text-to-speech integration.
- Lipsync cue generation.

TTS can initially return audio plus rough amplitude-based mouth movement. Phoneme-level lipsync can come later.

### packages/shared

Shared responsibilities:

- Runtime event types.
- Config schemas.
- Common IDs and error shapes.
- Provider and tool type definitions.

## Event Protocol

Use events between desktop and server. Keep them explicit and serializable.

Desktop to server:

```text
user.message
user.voice-start
user.voice-end
desktop.pointer
desktop.hotkey
session.reset
```

Server to desktop:

```text
assistant.delta
assistant.message
assistant.state
character.behavior
tool.started
tool.finished
audio.tts-ready
error
```

## Implementation Principle

Keep the first version boring:

- One desktop app.
- One local runtime.
- One Live2D model.
- One OpenAI-compatible provider.
- A small tool registry.
- SQLite persistence.

More complex systems such as sub-agents, vector memory, MCP, and active scheduling should be added only after the basic desktop experience feels stable.
