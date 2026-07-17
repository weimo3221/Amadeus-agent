# Roadmap

This file is the forward-looking plan. For live implementation status, use `docs/project-status.md`. For the detailed maturity blueprint, use `docs/agent-maturity-upgrade-plan.md`.

## How to read this roadmap

- The phases below are target deliverables, not a guarantee that every earlier deliverable is already complete.
- Some foundation work from later phases may land early if it helps the current migration.
- When roadmap wording and current code disagree, trust `docs/project-status.md`.

## Current Execution Plan

The next implementation pass should proceed in this order:

1. Current: continue hardening Vue Main UI as the primary workbench. Companion should stay lightweight, transparent, Live2D-focused, voice-capable, and transient; Main UI should own larger chat, session history, active/completed tasks, timed messages, skills, memory review, diagnostics, permissions, model/runtime configuration, Live2D/TTS configuration, MCP server management, and Role-scoped runtime selection.
   - `apps/desktop-ui-next` is now the sole Main UI renderer. The Config Center now starts with a Runtime Doctor tab backed by Python `GET /runtime/health`, giving users a single first-run view for model/API key readiness, memory, task supervisor, embedding, tools, Live2D, audio, and config state. Packaged Electron E2E covers chat, multi-skill selection, permission prompts, Companion session attach, and a real-process workflow through an isolated model fixture, the Python runtime, Node bridge, temporary SQLite state, session switching, persisted chat reload, and task review approval. `apps/desktop` remains the Electron shell and Companion host; the next UI work is secure credential storage/first-run wizard polish, real Cubism startup, desktop restart/recovery interaction coverage, and continued session/attach polish.
2. Current: make the task system heavier without adding competing task concepts. Plans describe turn-local intent/progress, scheduled jobs trigger work, and tasks remain the durable execution unit for retry/cancel/recovery/results. The task system now includes plan/task linkage, task details and controls, typed artifacts, leases and heartbeats, checkpoint-aware retry/resume, review and action-specific approval checkpoints, file-state resume policies, bounded multi-child graph orchestration with durable root ownership/concurrency/replan/cancel semantics, worker sandbox modes, subprocess workspace-copy isolation, an independent SQLite-leased durable supervisor, persistent process registry, worker adoption, per-run logs, process-group cancellation, wall/POSIX resource limits, and an OS-native backend boundary for Linux bubblewrap/macOS Seatbelt with explicit availability reporting and fail-closed required mode. The next concrete step is quality/long-running eval, followed by product-surface completion.
3. Current: turn MCP from a configured runtime bridge into a practical user-facing capability. The Main UI MCP tab now manages HTTP JSON-RPC servers, tests discovery, reloads the Python ToolRegistry, and shows discovery/audit observability: global discovered tools vs current-role visible tools, per-server visible/filtered counts, recent failure codes, permission/blocked decisions, call duration, and ToolRuntime metadata. `scripts/dev_mcp_server.py` provides local verification targets, including a no-token Hermes-style fixture. Next MCP work can evaluate stdio/SSE support after HTTP diagnostics prove stable.
4. Current: tighten the skills/tool/MCP UI semantics around "globally available", "role-selected", "suggested", and "active" so the desktop can keep context small without hiding diagnostic depth. Role settings now persist runtime-scope allowlists and expose searchable multi-select controls backed by current tool, skill, and MCP inventories; the MCP diagnostics view now makes role-selected filtering visible from the active session.
5. Next: keep shrinking `apps/server` to transport/model-serving/feedback proxy responsibilities. Do not reintroduce TypeScript-owned agent, tool, memory, or audio turn logic.
6. Next: keep ToolRuntime and Memory v2 in consolidation mode. Tool-call transcripts now persist across turns, summary compaction preserves assistant/tool-result pairing, budget-driven compaction uses trigger-budget recent-tail retention across turn-start/provider-overflow/turn-end paths, and long-term memory now uses a Mem0-like typed item format with metadata, history, access stats, `memory_items_fts` BM25 recall, metadata filtering, and optional local BGE-M3 vector backfill/ranking through `search_memory_items` rather than automatic context injection. Follow-up work should focus on observability, ranking quality, operational tuning, and selective old-tool-output pruning only when real high-volume tools need it.
7. CLI first slice is implemented as `npm run cli` / `scripts/amadeus_cli.py`: it defaults to `cli:default`, sends turns through the Python runtime, handles permission callbacks, and exposes runtime doctor/skills/memory/audio commands. Later work is packaging, richer TTY ergonomics, and optional shared-session attachment.
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
- `terminal`
- `process`
- `web_search`
- `web_extract`
- `vision_analyze`
- `clarify`
- `execute_code`
- disabled-by-default `browser_*` bridge tools

Planned follow-up tool work:

- automatic fallback from built-in `web_search` / `web_extract` to a provider-backed or `web-access` path
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
- Memory review candidate audit queue with human controls for exception-path candidates.
- Background memory review after turns that auto-promotes safe durable facts after safety/scope filtering.
- Persisted memory review jobs with status, skip/error reason, source message range, candidate counts, duration, HTTP query API, WebSocket event, and desktop summary.
- Token-budget-aware summary compaction with dynamic recent-turn retention and provider overflow compact-and-retry fallback.
- Context assembler that combines persona, summaries, profile, retrieved memory, recent messages, task state, and harness prompt fragments.

Current status:

- Core Memory v2 mechanics are now implemented: SQLite transcript FTS retrieval, stable Markdown memory, Mem0-like structured memory facts with their own BM25 FTS index, metadata filtering, explicit memory tools, review candidates, accept/reject flows, automatic review gates, runtime memory config, item history/access metadata, memory safety filters, and local BGE-M3 vector indexing/backfill for typed long-term memory.
- Context assembly is now API-call-time only and emits `memory.context.used`; recent diagnostics are retained per session in an in-memory ring buffer and exposed through `GET /memory/context/diagnostics`.
- Remaining work is consolidation: context assembly quality, summary/profile policy, compact-and-retry confidence, review quality tuning, vector ranking observability/tuning, and operational surfaces discovered through real usage.

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
- Runtime audio provider selection, fallback, cache, `/audio/speak`, provider/native-or-planned lipsync cue construction, and `audio.tts-ready` compatibility are implemented in `packages/amadeus/audio.py`. The first `AudioHarness` now observes final `assistant.message` events, calls that existing `AudioRuntime` boundary, and emits `audio.lipsync-cues` / `audio.tts-ready` through the harness registry instead of keeping TTS event generation as an agent-loop branch.
- The Python agent now emits `assistant.state` and lets the Live2D harness add `character.behavior` events for state-to-expression/motion mapping.
- Desktop now reports `desktop.capabilities` after connection/model load and reports runtime audio playback start/end/error as `audio.playback-*` events to the bridge.
- Python now receives those feedback events through `POST /runtime/feedback`; `HarnessFeedbackPolicy` stores per-session desktop capabilities, audio playback state, and recent feedback events.
- Live2D now maps playback start/end/error into `character.behavior` events and the bridge sends those returned events back to desktop. The mapping is configurable in `configs/harnesses.yaml` through `live2d.audioPlaybackBehaviors`.
- Remaining work is to grow this into the full harness layer: richer audio harness policy decisions, richer Live2D commands, speaking-state reconciliation, better non-Latin phoneme mapping, and broader provider cue compatibility.

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
- Seed/project skills now include `skills/development/runtime-debug`, `skills/development/desktop-e2e`, `skills/skill-creator`, and `skills/web-access`. `web-access` is the current project-local path for CDP-backed browser web access, with opt-in smoke tests for both a basic page read and a realistic arXiv paper lookup.
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
- First turn-scoped planning slice complete: model `update_plan` events carry `turnId`, Main UI attaches the live plan to an assistant-side turn message, Python persists `plan_runs`, and the plan panel restores/archives under the same user turn without making the plan look user-authored.
- Isolated delegation slice complete: `delegate_task` creates a tracked `delegated` child task, executes it through TaskWorker in a task-specific archived session and read-only WorkerRuntimeScope, propagates parent cancellation/timeout, prevents recursive delegation and parent-history pollution, and returns only the durable child summary/task metadata.
- First prompt-surface slice complete: per-role `SOUL.md` identity, core prompt assembly, per-tool `prompt_hint` routing, role `workspacePath`, repository-root default workspace assignment, workspace-level `AGENT.md` project context, role-scoped stable memory, and Role-scoped runtime selection for tools, skills, and MCP servers.
- First task-worker reliability slice complete: persisted task attempts, retry scheduling, stale `running` recovery, startup reclaim, and worker status event broadcasting.
- First blocked/review/artifact slice complete: `reviewRequired` tasks stop in `blocked`, approve/resume endpoints are exposed, typed artifacts render in the task detail modal, and Main UI shows recent task notifications.
- MCP bridge first slice complete: configured HTTP JSON-RPC MCP servers can expose `tools/list` tools as normalized `mcp__<server>__<tool>` schemas and execute them through `tools/call` under ToolRuntime permissions/audit. The local dev MCP server now includes both a minimal Amadeus fixture and a no-token Hermes-style fixture for add/test/execute verification.
- First task runner and independent durable-supervisor slices complete: `TaskRunner` separates scheduling/execution submission from the task state machine; in-process, synchronous, embedded subprocess, and optional POSIX fork runners remain available, while production uses `ExternalSupervisorTaskRunner` as a DB-backed client and `packages/amadeus/task_supervisor.py` as the subprocess owner. A SQLite single-primary lease and process registry support queued dispatch, worker PID adoption after supervisor restart, missing-process reclaim, cancellation/wall-time TERM-to-KILL handling, per-run logs, and POSIX worker limits. `/runtime/health` exposes external lease/process state and `scripts/dev_stack.py` starts the supervisor before the HTTP runtime. Worker scope, approval, checkpoint, artifact, file-manifest, and resume-policy behavior remains shared across runners.
- Worker ask-tool approval checkpoints now prefer action-specific authorization: terminal exact command keys, process keys scoped to PID/signal or normalized list query, and path/target keys are saved with risk metadata and configurable expiry from runtime config, then resume into one-run `WorkerRuntimeScope` action approvals; legacy tool-wide resume approval remains only for older checkpoints without an action key and no longer bypasses high-risk or blocking-label actions.
- Approval and file-resume override decisions now have first-class durable audit coverage: blocked/resumed/review-approved task events preserve approval action, risk, TTL/expiry, actor, and source metadata; override set/clear events preserve artifact policy and before/after state; successful worker-auto-approved tool audits retain the same action/risk/expiry correlation.
- First task-state context slice complete: queued/running/blocked session tasks are injected as reference-only `<active-tasks>` context and recent terminal outcomes are injected as `<recent-tasks>` with diagnostics.
- Richer graph UI/model-driven replan quality, native-backend deployment coverage beyond the current bubblewrap/Seatbelt implementations, longer supervisor/worker double-crash soak coverage, and broader approval/override policy tuning.
- Richer context compression: Hermes-style tool-call transcript persistence, pair-safe summary boundary alignment, trigger-budget recent-tail retention, and turn-start / overflow-retry / turn-end compaction paths are in place; remaining work is better compression observability, selective old-tool-output pruning, and quality audits for generated summaries.
- Long task plans.
- Broader human approval policy coverage for destructive, sensitive, or low-confidence worker actions beyond the current first-pass classifier and action-specific audit contract.
- Provider and harness profiles.
- Runtime contract eval now runs fourteen structured scenarios with per-check metrics through `npm test`, covering role/task context, cross-runner lifecycle consistency, tracked isolated child-agent/session/summary handoff, orchestrator repair/dependency/artifact handoff, bounded graph quality/replan behavior, MCP, subprocess fault recovery/cancellation/deduplication, durable supervisor restart/adoption/lost-process soak, workspace/symlink/path/write sandbox boundaries, Memory recall/safety policy, Audio fallback/lipsync timing, action-specific approval target/expiry scope, and durable approval/override audit metadata. Broaden next into model-backed planning/tool-choice quality, hour-scale crash/restart soak tests, Live2D renderer behavior, and richer guardrail behavior.
- Product-polish first slice: the command-line client reuses the same Python-owned turn, permission, Skills, MCP, Memory, and Audio endpoints as the desktop, giving the runtime a non-Electron operator path while keeping TypeScript as transport only.

Reference:

- `../hermes-agent`
- `../deepagents`
