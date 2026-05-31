# Roadmap

For live implementation status, see `docs/project-status.md`.

## Phase 0: Project Skeleton

Status: started.

Deliverables:

- Directory structure.
- Architecture notes.
- Runtime event protocol.
- Character/provider/tool config samples.
- Initial package boundaries.

## Phase 1: Desktop Live2D Shell

Goal: launch a desktop character window.

Deliverables:

- Electron + Vite desktop app.
- Transparent window.
- Always-on-top toggle.
- Drag-to-move support.
- Load one Live2D model from `models/live2d`.
- Idle animation.
- Manual expression and motion test panel.

Reference:

- `../airi/apps/stage-tamagotchi`
- `../airi/packages/stage-ui-live2d`

## Phase 2: Local Agent Runtime

Goal: chat with the character through a local runtime.

Deliverables:

- Local server process.
- WebSocket stream from server to desktop.
- OpenAI-compatible provider adapter.
- Basic chat history.
- Runtime states: idle, listening, thinking, speaking, error.

## Phase 3: Character Behavior

Goal: make replies drive the Live2D character.

Deliverables:

- `configs/character.yaml`.
- Persona prompt.
- Emotion classification from assistant output.
- Emotion-to-expression mapping.
- Speaking and thinking motions.
- Click and hover reactions.

## Phase 4: Voice and Lipsync

Goal: voice interaction feels natural enough for daily use.

Deliverables:

- TTS provider adapter.
- Audio playback in desktop app.
- Basic amplitude lipsync.
- Optional ASR input.
- Push-to-talk hotkey.

## Phase 5: Memory and Tools

Goal: the agent remembers useful facts and can act.

Deliverables:

- SQLite storage.
- Conversation summaries.
- User profile memory.
- Tool registry.
- First tools:
  - time
  - local file search
  - web search
  - open URL
  - reminders
- Permission prompts for sensitive actions.

## Phase 6: Proactive Agent

Goal: the character can help without waiting for every instruction.

Deliverables:

- Scheduled tasks.
- Reminder notifications.
- Daily brief.
- Idle-time check-ins.
- Background task state display.

## Phase 7: Advanced Agent Features

Goal: support complex long-running tasks.

Deliverables:

- MCP bridge.
- Sub-agent/task worker abstraction.
- Context compression.
- Long task plans.
- Human approval checkpoints.

Reference:

- `../hermes-agent`
- `../deepagents`
