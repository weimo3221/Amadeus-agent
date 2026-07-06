# Roadmap

This file is the forward-looking plan. For live implementation status, use `docs/project-status.md`. For the detailed maturity blueprint, use `docs/agent-maturity-upgrade-plan.md`.

## How to read this roadmap

- The phases below are target deliverables, not a guarantee that every earlier deliverable is already complete.
- Some foundation work from later phases may land early if it helps the current migration.
- When roadmap wording and current code disagree, trust `docs/project-status.md`.

## Current Execution Plan

The next implementation pass should proceed in this order:

1. Current: continue hardening Vue Main UI as the primary workbench. Companion should stay lightweight, transparent, Live2D-focused, voice-capable, and transient; Main UI should own larger chat, session history, active/completed tasks, timed messages, skills, memory review, diagnostics, permissions, model/runtime configuration, Live2D/TTS configuration, and MCP server management.
2. Current: make the task system heavier without adding competing task concepts. Plans describe turn-local intent/progress, scheduled jobs trigger work, and tasks remain the durable execution unit for retry/cancel/recovery/results. Plan item -> Task, scheduled agent-task triggers, task detail timeline, cancel, re-run, Hermes-style turn-scoped plan display, persisted plan-run snapshots, blocked/review controls, typed artifacts, and task notifications are now wired; the next concrete step is longer-horizon worker durability and richer artifact producers.
3. Current: turn MCP from a configured runtime bridge into a practical user-facing capability. The Main UI MCP tab now manages HTTP JSON-RPC servers, tests discovery, and reloads the Python ToolRegistry; `scripts/dev_mcp_server.py` provides local verification targets, including a no-token Hermes-style fixture. MCP tool schema names now normalize external server/tool identifiers into model-safe underscore names. Next MCP work should improve persisted diagnostics/audit surfaces and then evaluate stdio/SSE support.
4. Current: tighten the skills UI semantics around "available", "suggested", and "active" so the desktop keeps exposing only lightweight user-facing state while the runtime logs keep the deeper activation details.
5. Next: keep shrinking `apps/server` to transport/model-serving/feedback proxy responsibilities. Do not reintroduce TypeScript-owned agent, tool, memory, or audio turn logic.
6. Next: keep ToolRuntime and Memory v2 in consolidation mode. Extend them only for real gaps found while implementing desktop, Live2D, audio, MCP, and user-facing runtime flows.
7. Later: implement the real CLI client only after the basic Main UI workflows are stable. It should default to its own session ID unless explicitly attached elsewhere.
8. Later: after the desktop UI shape settles, add a real skill import/install flow, run `validate_skills.py` as part of that flow, and support runtime refresh so newly added skills become available without a full manual restart.
9. Later: fix documentation drift when implementation boundaries move, especially package READMEs that still describe active runtime modules as placeholders.

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
- Transparent Companion window plus larger Main UI window.
- Always-on-top toggle.
- Drag-to-move support.
- Live2D model loading and stage behavior.
- Idle animation.
- Manual expression and motion test panel.

Notes:

- The current default model is local `models/live2d/hiyori-free`.
- Additional local models can be added under `models/live2d` and selected through `configs/harnesses.yaml` plus the bridge model-switch path.
- Live2D now appears only in Companion. The Companion input panel is controlled from global cursor position rather than DOM hover events, hides shortly after the cursor leaves, and model fit can be tuned from `configs/runtime.yaml`.

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
- Local ASR input.
- Optional push-to-talk hotkey.

Notes:

- Current MVP voice playback uses runtime audio on macOS through `tts.default: auto`, with desktop `speechSynthesis` retained as fallback.
- Current lipsync is hybrid: provider-native or runtime-planned phoneme/viseme cues when available, desktop amplitude-driven mouth movement for runtime audio otherwise, and the timed mouth loop only as fallback.
- Local ASR is now wired through Companion's microphone orb, browser `MediaRecorder`, bridge `POST /audio/transcribe`, and Python `faster-whisper` auto-provider selection. Remaining voice-input work is quality tuning, language/model configuration UX, and optional push-to-talk/global hotkey support.

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
- `search_memory_items`
- `memory_add`
- `memory_replace`
- `memory_forget`
- `search_files`
- `read_file`
- `patch`
- `write_file`
- `update_plan`
- `create_task`
- `list_tasks`
- `cancel_task`
- `schedule_message`
- `todo`
- `skills_list`
- `skill_view`
- `delegate_task`

Planned follow-up tools:

- `web_search`
- `open_url`
- richer safe desktop/user-action tools after the core companion workflows stabilize

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
- Runtime audio provider selection, fallback, cache, `/audio/speak`, `audio.tts-ready`, and provider/native-or-planned lipsync cue emission are implemented in `packages/amadeus/audio.py`.
- The Python agent now emits `assistant.state` and lets the Live2D harness add `character.behavior` events for state-to-expression/motion mapping.
- Desktop now reports `desktop.capabilities` after connection/model load and reports runtime audio playback start/end/error as `audio.playback-*` events to the bridge.
- Python now receives those feedback events through `POST /runtime/feedback`; `HarnessFeedbackPolicy` stores per-session desktop capabilities, audio playback state, and recent feedback events.
- Live2D now maps playback start/end/error into `character.behavior` events and the bridge sends those returned events back to desktop. The mapping is configurable in `configs/harnesses.yaml` through `live2d.audioPlaybackBehaviors`.
- Remaining work is to grow this into the full harness layer: richer audio harness decisions, richer Live2D commands, speaking-state reconciliation, better non-Latin phoneme mapping, and broader provider cue compatibility.

## Phase 10: Skills

Goal: add procedural memory and reusable workflows.

Target deliverables:

- `skills/<category>/<skill-name>/SKILL.md` layout.
- `skills_list`, `skill_view`, `skill_run`.
- Skill frontmatter for tools, platforms, harness dependencies, and env requirements.
- `skill_manage` with permission prompts and path safety.
- Initial Live2D/audio-aware companion skills.

Current status:

- V1 is now in place for `skills/<category>/<skill-name>/SKILL.md` discovery, simple frontmatter parsing, read-only `skills_list` / `skill_view`, an always-on system-prompt skills catalog, `skill_view`-driven turn-local full activation, bridge passthrough for `/skills/list` and `/skills/view`, and a desktop suggested-skills picker with local search/filtering, a short inline summary, and persisted selection state.
- Two seed skills exist under `skills/development/`: `runtime-debug` and `desktop-e2e`.
- Current priority is not to add more skill-management UI yet. The desktop surface should first be visually consolidated so future features do not keep adding one more panel or status row.
- Remaining work after that UI pass is a real import/install flow, validate-on-import, runtime refresh, optional enable/disable management, and only later heavier orchestration such as `skill_run`.

## Phase 11: Proactive Agent

Goal: the character can help without waiting for every instruction.

Target deliverables:

- First slice complete: SQLite `tasks` / `task_events`, task create/list/cancel HTTP APIs, bridge proxying, and Main UI active task display.
- First reliability slice complete: worker attempts, retry scheduling, stale `running` recovery, and task event broadcasting for recovered/retry states.
- First scheduled-message slice complete: SQLite `scheduled_jobs` / `scheduled_job_events`, schedule parsing, repeat counts, in-process scheduler worker, `schedule_message` tool, HTTP APIs, `scheduled.updated` events, assistant-message delivery, and Main UI timed-message controls.
- First persistent todo slice complete: SQLite `todo_items`, `todo` tool, HTTP APIs, active todo context injection, and bounds on item count/content.
- Richer reminder notifications.
- Daily brief.
- Idle-time check-ins.
- Richer task state display.

## Phase 12: Advanced Agent Features

Goal: support complex long-running tasks.

Target deliverables:

- First turn-control slice complete: session-scoped running turn state, `POST /agent/cancel`, cooperative tool cancellation, and `agent.turn.started` / `agent.turn.cancelled` events.
- First turn-scoped planning slice complete: model `update_plan` events carry `turnId`, Main UI attaches the live plan to the initiating user message, Python persists `plan_runs`, and the plan panel restores/archives under the same user turn.
- First delegation slice complete: restricted `delegate_task` research/search tool with depth 1, concurrency 2, no write tools, and summary-only parent results.
- First prompt-surface slice complete: per-role `SOUL.md` identity, core prompt assembly, per-tool `prompt_hint` routing, role `workspacePath`, repository-root default workspace assignment, workspace-level `AGENT.md` project context, and role-scoped stable memory.
- First task-worker reliability slice complete: persisted task attempts, retry scheduling, stale `running` recovery, startup reclaim, and worker status event broadcasting.
- First blocked/review/artifact slice complete: `reviewRequired` tasks stop in `blocked`, approve/resume endpoints are exposed, typed artifacts render in the task detail modal, and Main UI shows recent task notifications.
- MCP bridge first slice complete: configured HTTP JSON-RPC MCP servers can expose `tools/list` tools as normalized `mcp__<server>__<tool>` schemas and execute them through `tools/call` under ToolRuntime permissions/audit. The local dev MCP server now includes both a minimal Amadeus fixture and a no-token Hermes-style fixture for add/test/execute verification.
- First task runner abstraction complete: `TaskRunner` / `InProcessTaskRunner` separate scheduling/execution submission from the task state machine, leaving process-backed execution as a later implementation.
- First task-state context slice complete: queued/running/blocked session tasks are injected as reference-only `<active-tasks>` context and recent terminal outcomes are injected as `<recent-tasks>` with diagnostics.
- Durable multi-process task runner implementation.
- Richer context compression.
- Long task plans.
- Human approval checkpoints.
- Provider and harness profiles.
- First runtime contract eval complete for role identity, active/recent task context, task lifecycle, and MCP tool contracts. Broaden eval harness for tool choice, permission, memory, Live2D, audio, and guardrail behavior.

Reference:

- `../hermes-agent`
- `../deepagents`
