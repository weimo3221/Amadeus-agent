# Roadmap

For live implementation status, see `docs/project-status.md`. For the detailed maturity blueprint, see `docs/agent-maturity-upgrade-plan.md`.

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

## Phase 6: Python Runtime Ownership

Goal: move the real agent loop out of the TypeScript bridge and into `packages/amadeus`.

Deliverables:

- Python `/agent/turn` endpoint.
- Python-owned model adapter and streaming event generation.
- Python-owned tool loop and memory writes.
- TypeScript server reduced to WebSocket/HTTP transport relay.
- Compatibility with current desktop events and permission prompts.

## Phase 7: ToolRuntime and Guardrails

Goal: make tools reliable, auditable, and permission-enforced at runtime.

Deliverables:

- Python `ToolSpec`, `ToolContext`, `ToolResult`, and registry.
- Python-owned loading for `configs/tools.yaml`.
- Tool timeout, cancellation, duration, preview, and audit records.
- Guardrails for repeated failures and no-progress tool loops.
- `/tools/list` bridge for desktop/server diagnostics.

## Phase 8: Memory v2

Goal: move beyond raw message replay.

Deliverables:

- Conversation summaries.
- User profile facts and preferences.
- SQLite FTS session search.
- Memory write/search/forget tools.
- Background memory review after turns.
- Context assembler that combines persona, summaries, profile, retrieved memory, recent messages, task state, and harness prompt fragments.

## Phase 9: Live2D and Audio Harnesses

Goal: make Amadeus' Live2D/audio strengths installable runtime harnesses.

Deliverables:

- `packages/amadeus/harness` base contract and registry.
- `configs/harnesses.yaml`.
- Live2D harness for state-to-expression/motion/lipsync behavior.
- Audio harness for TTS provider selection, fallback, cache, ASR contracts, and lipsync cues.
- Desktop capability events for Live2D/audio.
- Playback feedback events from desktop to runtime.

## Phase 10: Skills

Goal: add procedural memory and reusable workflows.

Deliverables:

- `skills/<category>/<skill-name>/SKILL.md` layout.
- `skills_list`, `skill_view`, `skill_run`.
- Skill frontmatter for tools, platforms, harness dependencies, and env requirements.
- `skill_manage` with permission prompts and path safety.
- Initial Live2D/audio-aware companion skills.

## Phase 11: Proactive Agent

Goal: the character can help without waiting for every instruction.

Deliverables:

- Scheduled tasks.
- Reminder notifications.
- Daily brief.
- Idle-time check-ins.
- Background task state display.

## Phase 12: Advanced Agent Features

Goal: support complex long-running tasks.

Deliverables:

- MCP bridge.
- Sub-agent/task worker abstraction.
- Context compression.
- Long task plans.
- Human approval checkpoints.
- Provider and harness profiles.
- Eval harness for tool choice, permission, memory, Live2D, audio, and guardrail behavior.

Reference:

- `../hermes-agent`
- `../deepagents`
