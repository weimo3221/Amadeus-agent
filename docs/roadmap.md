# Roadmap

This file is the forward-looking plan. For live implementation status, use `docs/project-status.md`. For the detailed maturity blueprint, use `docs/agent-maturity-upgrade-plan.md`.

## How to read this roadmap

- The phases below are target deliverables, not a guarantee that every earlier deliverable is already complete.
- Some foundation work from later phases may land early if it helps the current migration.
- When roadmap wording and current code disagree, trust `docs/project-status.md`.

## Current Execution Plan

The next implementation pass should proceed in this order:

1. Done: expand Electron end-to-end coverage beyond startup smoke with a deterministic local-runtime path. The packaged desktop now connects to a stub bridge, submits chat, receives streamed assistant events, and updates the visible chat UI without a live model provider.
2. Done: add deeper desktop E2E around Live2D local model loading and model-switch behavior. The packaged desktop now exercises local `/live2d/config`, `/live2d/models`, `/live2d/select`, renderer model loading, the model select control, and harness config persistence with deterministic local fixtures.
3. Done: add desktop E2E around runtime audio playback feedback. The packaged desktop now receives `audio.tts-ready`, plays deterministic mock runtime audio, and reports both `audio.playback-started` / `audio.playback-ended` and `audio.playback-started` / `audio.playback-error` back to the bridge.
4. Done: add desktop E2E around permission prompts for the remaining `ask` tools. The packaged desktop now receives deterministic `tool.permission.request` events, renders the real Allow / Deny UI, and reports `tool.permission.response` back to the bridge for both approval and denial flows.
5. Done: improve lipsync from the pure timed mouth loop to a hybrid mode. Runtime audio playback now drives `ParamMouthOpenY` from Web Audio amplitude analysis, while speech-synthesis and unsupported environments still fall back to the older timed loop.
6. Done: move the main runtime lipsync planner into `packages/amadeus/audio.py`. Runtime now emits text-driven phoneme/viseme `audio.lipsync-cues` before `audio.tts-ready`, uses local cached `wav` envelope data only as modulation, and leaves the feedback-side harness cue generation as fallback.
7. Done: accept provider-native lipsync payloads in `packages/amadeus/audio.py`. GPT-SoVITS-style JSON `lipsyncCues` / `visemes` / `phonemes` are now normalized into runtime `audio.lipsync-cues`, and the local phoneme planner remains the fallback when provider data is absent.
8. Done: move bridge-owned memory count/reset behavior to the Python runtime. `apps/server` no longer opens its own SQLite message table for session counts or resets; it now reads `GET /memory/count` and forwards `POST /memory/reset`, while the desktop protocol stays unchanged.
9. Done: remove the remaining TypeScript-owned Live2D library fallback and local tool-permission resolution hook from `apps/server`. `/live2d/*` now only goes through the explicit proxy handler, and `tool.permission.response` is always forwarded to Python.
10. Continue shrinking `apps/server` to transport/model-serving/feedback proxy responsibilities. Do not reintroduce TypeScript-owned agent, tool, memory, or audio turn logic.
11. Keep ToolRuntime and Memory v2 in consolidation mode. Extend them only for real gaps found while implementing desktop, Live2D, audio, and user-facing runtime flows.
12. Fix documentation drift when implementation boundaries move, especially package READMEs that still describe active runtime modules as placeholders.

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

- The current default model is local `models/live2d/hiyori-free`.
- Additional local models can be added under `models/live2d` and selected through `configs/harnesses.yaml` plus the bridge model-switch path.

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
- Better lipsync than the remaining timed-loop fallback.
- Optional ASR input.
- Optional push-to-talk hotkey.

Notes:

- Current MVP voice playback uses runtime audio on macOS through `tts.default: auto`, with desktop `speechSynthesis` retained as fallback.
- Current lipsync is hybrid: provider-native or runtime-planned phoneme/viseme cues when available, desktop amplitude-driven mouth movement for runtime audio otherwise, and the timed mouth loop only as fallback.

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
- `read_memory`
- `update_memory`
- `search_memory`
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

- This phase is functionally delivered for the current MVP.
- The current preferred path is Python-first.
- The remaining work is cleanup, provider/model boundary extraction, and continued bridge shrinkage, not first implementation from scratch.

## Phase 7: ToolRuntime and Guardrails

Goal: make tools reliable, auditable, and permission-enforced at runtime.

Target deliverables:

- Python `ToolSpec`, `ToolContext`, `ToolResult`, and mature registry boundaries.
- Python-owned loading for `configs/tools.yaml` as the long-term runtime source.
- Tool timeout, cancellation, duration, preview, and audit records.
- Guardrails for repeated failures and no-progress tool loops.
- `/tools/list` bridge for desktop/server diagnostics. The server now queries Python for tool permission state instead of maintaining a TypeScript mirror.

Notes:

- The main runtime layer now exists: registry/config loading, permission metadata, structured results, timeout/cancellation, audit persistence, output policies, and repeated-failure/no-progress guardrails are implemented.
- Remaining work is late hardening driven by new tools and real usage, such as richer context propagation, better diagnostics, and additional per-tool result/no-progress policies.

## Phase 8: Memory v2

Goal: move beyond raw message replay.

Target deliverables:

- Conversation summaries.
- User profile facts and preferences.
- SQLite FTS session search.
- Explicit structured memory search/add/replace/forget tools.
- Human-controlled memory review candidate queue.
- Background memory review after turns that proposes candidates instead of directly writing durable memory.
- Persisted memory review jobs with status, skip/error reason, source message range, candidate counts, duration, HTTP query API, WebSocket event, and desktop summary.
- Token-budget-aware summary compaction with dynamic recent-message retention and provider overflow compact-and-retry fallback.
- Context assembler that combines persona, summaries, profile, retrieved memory, recent messages, task state, and harness prompt fragments.

Current status:

- Core Memory v2 mechanics are now implemented: SQLite FTS retrieval, stable Markdown memory, structured memory facts, explicit memory tools, review candidates, accept/reject flows, automatic review gates, runtime memory config, schema metadata, and memory safety filters.
- Context assembly is now API-call-time only and emits `memory.context.used`; recent diagnostics are retained per session in an in-memory ring buffer and exposed through `GET /memory/context/diagnostics`.
- Remaining work is consolidation: context assembly quality, summary/profile policy, compact-and-retry confidence, review quality tuning, and operational surfaces discovered through real usage.

## Phase 9: Live2D and Audio Harnesses

Goal: make Amadeus' Live2D/audio strengths installable runtime harnesses.

Target deliverables:

- `packages/amadeus/harness` base contract and registry.
- `configs/harnesses.yaml`.
- Live2D harness for state-to-expression/motion/lipsync behavior.
- Audio harness for TTS provider selection, fallback, cache, ASR contracts, and lipsync cues.
- Desktop capability events for Live2D/audio.
- Playback feedback events from desktop to runtime.

Current status:

- First slice is implemented: `packages/amadeus/harness` exists with a base contract, registry, Live2D harness, and `configs/harnesses.yaml`.
- The Python agent now emits `assistant.state` and lets the Live2D harness add `character.behavior` events for state-to-expression/motion mapping.
- Desktop now reports `desktop.capabilities` after connection/model load and reports runtime audio playback start/end/error as `audio.playback-*` events to the bridge.
- Python now receives those feedback events through `POST /runtime/feedback`; `HarnessFeedbackPolicy` stores per-session desktop capabilities, audio playback state, and recent feedback events.
- Live2D now maps playback start/end/error into `character.behavior` events and the bridge sends those returned events back to desktop. The mapping is configurable in `configs/harnesses.yaml` through `live2d.audioPlaybackBehaviors`.
- Remaining work is to grow this into the full harness layer: audio harness, richer Live2D commands, speaking-state reconciliation, and eventual amplitude/phoneme-driven lipsync cues.

## Phase 10: Skills

Goal: add procedural memory and reusable workflows.

Target deliverables:

- `skills/<category>/<skill-name>/SKILL.md` layout.
- `skills_list`, `skill_view`, `skill_run`.
- Skill frontmatter for tools, platforms, harness dependencies, and env requirements.
- `skill_manage` with permission prompts and path safety.
- Initial Live2D/audio-aware companion skills.

Current status:

- V1 is now in place for `skills/<category>/<skill-name>/SKILL.md` discovery, simple frontmatter parsing, read-only `skills_list` / `skill_view`, optional `POST /agent/turn` `skills: []` injection, and bridge passthrough for future desktop selection UI.
- Two seed skills exist under `skills/development/`: `runtime-debug` and `desktop-e2e`.
- Remaining work is `skill_run`, richer metadata validation, support files, desktop/UI selection, and later skill management/orchestration.

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
