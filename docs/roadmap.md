# Roadmap

This file is the forward-looking plan. For live implementation status, use `docs/project-status.md`. For the detailed maturity blueprint, use `docs/agent-maturity-upgrade-plan.md`.

## How to read this roadmap

- The phases below are target deliverables, not a guarantee that every earlier deliverable is already complete.
- Some foundation work from later phases may land early if it helps the current migration.
- When roadmap wording and current code disagree, trust `docs/project-status.md`.

## Phase 0: Project Skeleton

Goal: establish the repository structure, startup docs, and initial config surfaces.

Target deliverables:

- Directory structure.
- Architecture notes.
- Runtime event protocol.
- Character/provider/tool config samples.
- Initial package boundaries.

## Phase 1: Desktop Live2D Shell

Goal: launch a desktop character window.

Target deliverables:

- Electron + Vite desktop app.
- Transparent window.
- Always-on-top toggle.
- Drag-to-move support.
- Live2D model loading and stage behavior.
- Idle animation.
- Manual expression and motion test panel.

Notes:

- The current MVP still loads a remote Live2D test model by default.
- Moving to a local model bundle under `models/live2d` is still follow-up work.

Reference:

- `../airi/apps/stage-tamagotchi`
- `../airi/packages/stage-ui-live2d`

## Phase 2: Local Agent Runtime

Goal: chat with the character through a local runtime.

Target deliverables:

- Local server process.
- WebSocket stream from server to desktop.
- OpenAI-compatible provider adapter.
- Basic chat history.
- Runtime states such as idle, thinking, speaking, tool-running, and error.

## Phase 3: Character Behavior

Goal: make replies drive the Live2D character.

Target deliverables:

- Persona prompt.
- Runtime-to-expression/motion mapping.
- Speaking and thinking motions.
- Click and hover reactions.
- Character behavior events that the desktop renderer can apply safely.

## Phase 4: Voice and Lipsync

Goal: voice interaction feels natural enough for daily use.

Target deliverables:

- Runtime audio interface.
- Audio playback in desktop app.
- Better lipsync than the current timed mouth loop.
- Optional ASR input.
- Optional push-to-talk hotkey.

Notes:

- Current MVP voice playback still relies primarily on desktop `speechSynthesis` fallback.
- Current lipsync is a timed mouth loop, not amplitude-driven or phoneme-aware.

## Phase 5: Memory and Tools

Goal: the agent remembers useful facts and can act.

Target deliverables:

- SQLite storage.
- Conversation summaries.
- User profile memory.
- Tool registry.
- Permission prompts for sensitive actions.
- Practical first tools.

Current tool baseline already delivered:

- `get_current_time`
- `roll_dice`
- `search_files`
- `read_file`
- `patch`
- `write_file`

Planned follow-up tools:

- `web_search`
- `open_url`
- `reminders`

## Phase 6: Python Runtime Ownership

Goal: move the real agent loop out of the TypeScript bridge and into `packages/amadeus`.

Target deliverables:

- Python `/agent/turn` endpoint.
- Python-owned model call path and streaming event generation.
- Python-owned tool loop and memory writes.
- TypeScript server reduced toward WebSocket/HTTP transport relay.
- Compatibility with current desktop events and permission prompts.
- Enough integration coverage to keep shrinking TypeScript bridge scaffolding confidently.

Notes:

- This phase is partially delivered already.
- The current preferred path is Python-first.
- The remaining work is cleanup and parity confidence, not first implementation from scratch.

## Phase 7: ToolRuntime and Guardrails

Goal: make tools reliable, auditable, and permission-enforced at runtime.

Target deliverables:

- Python `ToolSpec`, `ToolContext`, `ToolResult`, and mature registry boundaries.
- Python-owned loading for `configs/tools.yaml` as the long-term runtime source.
- Tool timeout, cancellation, duration, preview, and audit records.
- Guardrails for repeated failures and no-progress tool loops.
- `/tools/list` bridge for desktop/server diagnostics. The server now queries Python for tool permission state instead of maintaining a TypeScript mirror.

Notes:

- Some prerequisite work is already done: tool registry, config loading, permission metadata, and practical ask tools.
- This phase is not complete until the guardrail/audit/runtime layer exists formally.

## Phase 8: Memory v2

Goal: move beyond raw message replay.

Target deliverables:

- Conversation summaries.
- User profile facts and preferences.
- SQLite FTS session search.
- Memory write/search/forget tools.
- Background memory review after turns.
- Context assembler that combines persona, summaries, profile, retrieved memory, recent messages, task state, and harness prompt fragments.

## Phase 9: Live2D and Audio Harnesses

Goal: make Amadeus' Live2D/audio strengths installable runtime harnesses.

Target deliverables:

- `packages/amadeus/harness` base contract and registry.
- `configs/harnesses.yaml`.
- Live2D harness for state-to-expression/motion/lipsync behavior.
- Audio harness for TTS provider selection, fallback, cache, ASR contracts, and lipsync cues.
- Desktop capability events for Live2D/audio.
- Playback feedback events from desktop to runtime.

## Phase 10: Skills

Goal: add procedural memory and reusable workflows.

Target deliverables:

- `skills/<category>/<skill-name>/SKILL.md` layout.
- `skills_list`, `skill_view`, `skill_run`.
- Skill frontmatter for tools, platforms, harness dependencies, and env requirements.
- `skill_manage` with permission prompts and path safety.
- Initial Live2D/audio-aware companion skills.

## Phase 11: Proactive Agent

Goal: the character can help without waiting for every instruction.

Target deliverables:

- Scheduled tasks.
- Reminder notifications.
- Daily brief.
- Idle-time check-ins.
- Background task state display.

## Phase 12: Advanced Agent Features

Goal: support complex long-running tasks.

Target deliverables:

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
