# Amadeus Agent Contributor Guide

This repository builds a desktop Live2D companion agent. The goal is not just a chat box: Amadeus should be able to talk, show state through a Live2D character, remember conversation history, and run local tools with explicit permissions.

Use this file as the first stop when another coding agent or human contributor opens the repo.

## Current State

Amadeus is now a Python-first desktop agent runtime, not just an initial MVP:

- `apps/desktop` has two renderer surfaces: `companion` for the transparent Live2D desktop presence and `main-ui` for the larger chat/workbench.
- `apps/server` is a thin Node/TypeScript bridge. It owns WebSocket/session transport and proxies Python HTTP/runtime event APIs; it should not regain model, memory, or tool-loop ownership.
- `packages/amadeus` owns the agent loop, OpenAI-compatible model boundary, memory, tool runtime, skills, task worker, Live2D model library, and audio/TTS path.
- Python `/agent/turn` is the preferred and only active turn path. The older TypeScript model/tool fallback loop has been removed.
- SQLite persists sessions, messages, summaries, structured memory, roles, tasks, task events, memory review jobs, and tool audit records.
- Tool calling is model-triggered through OpenAI-compatible `tools` and `tool_calls`.
- Python `ToolRuntime` owns enabled schemas, `allow` / `ask` / `deny` permissions, timeout/cancellation handling, audit records, result compaction, and repeated-call guardrails.
- Role `workspacePath` selects the workspace root for project instructions and file tools. The Python server defaults missing role workspaces to the project repository root, where this `AGENT.md` lives.
- The runtime loads only `AGENT.md` from the active workspace as lower-priority project context: architecture, conventions, constraints, current status, and recommended next work.
- User-specific preferences belong in Role persona/style or memory, not in `AGENT.md`.
- Current Python tools include time, dice, stable memory, structured memory, memory search, file search/read, patch/write, plan updates, session tasks, skill listing/viewing, and restricted delegation.
- Session tasks are persisted in SQLite and executed by an in-process Python worker with `queued` / `running` / `succeeded` / `failed` / `cancelled` states, retry scheduling, and stale-running recovery.
- Local Live2D model storage is active under `models/live2d`, with Python owning `/live2d/*` and the bridge proxying those endpoints back to the desktop origin.
- Runtime audio can use GPT-SoVITS when configured or macOS `say` as a local fallback, and emits `audio.tts-ready` plus lipsync cues when available.

For the latest detailed progress, read `docs/project-status.md`.

## Repository Map

- `apps/desktop`: Electron shell, renderer UI, Live2D stage, speech synthesis, lipsync MVP, runtime WebSocket client.
- `apps/server`: Thin Node/TypeScript WebSocket and HTTP bridge to the Python runtime.
- `packages/amadeus`: Python-owned agent brain plus TypeScript bridge exports for event protocol and runtime helper clients.
- `packages/live2d-stage`: Intended desktop-side Live2D rendering adapter boundary; current renderer logic still lives in the desktop app.
- `configs`: Character, provider, tool, runtime, and harness configs.
- `docs`: Architecture, event protocol, roadmap, implementation notes, and live project status.
- `models/live2d`: Active local Live2D model bundles.

## Runtime Commands

From the repository root:

```bash
npm install
npm run typecheck
npm --workspace apps/server run dev
npm --workspace apps/desktop run dev
npm run dev
```

`npm run dev` starts server and desktop together. During debugging, it is often easier to run server and desktop in separate terminals.

Server endpoints:

```text
http://127.0.0.1:8788/health
ws://127.0.0.1:8788/ws
```

## Environment

Copy `.env.example` to `.env` and set:

```text
OPENAI_BASE_URL=...
OPENAI_API_KEY=...
OPENAI_MODEL=...
VITE_AGENT_WS_URL=ws://127.0.0.1:8788/ws
```

Do not commit `.env` or API keys.

## Architecture Rules

- Keep `apps/desktop` thin. It should render UI, Live2D, voice, and user interaction, but it should not own long-term memory, provider-specific LLM logic, or tool execution.
- Keep `apps/server` as a transport bridge. It owns WebSocket rooms, HTTP proxying, and desktop permission transport, but not model calls, memory semantics, tool execution, or agent planning.
- Keep `packages/amadeus` as the runtime owner for sessions, memory, model calls, tool execution, skills, tasks, Live2D model APIs, and audio/TTS planning.
- Communicate between desktop and server through the shared event protocol in `packages/amadeus/events.ts`.
- Add new runtime events to `packages/amadeus/events.ts` first, then update both server and desktop handlers.
- Keep TypeScript tool bridge helpers in `packages/amadeus/tools.ts`; keep concrete schemas, permissions, prompt hints, and implementations in Python under `packages/amadeus/tools` and `packages/amadeus/tool_runtime`.
- New tools must define:
  - OpenAI-compatible schema.
  - `displayName`.
  - `enabled`.
  - `permission`: `allow`, `ask`, or `deny`.
  - execution handler.
  - `prompt_hint` when useful for routing.
  - request description for `ask` tools when useful.
- Read-only bounded workspace inspection can be `allow`; persistent writes, shell/process execution, external apps/URLs, network actions, and sensitive data exposure should start as `ask`.

## Current Event Flow

Main client-to-server events:

- `user.message`
- `session.reset`
- `tool.permission.response`
- `desktop.capabilities`
- `audio.playback-started`
- `audio.playback-ended`
- `audio.playback-error`
- `memory.review.*`

Main server-to-client events:

- `server.hello`
- `memory.updated`
- `memory.context.used`
- `assistant.state`
- `assistant.delta`
- `assistant.message`
- `character.behavior`
- `audio.tts-ready`
- `tool.started`
- `tool.finished`
- `tool.audit`
- `tool.permission.request`
- `task.plan.updated`
- `task.updated`
- `memory.review.*`
- `error`

Keep event payloads serializable and small.

## How To Add A Tool

1. Add a focused Python implementation under `packages/amadeus/tools/`.
2. Define its `ToolSpec` next to the handler, including schema, permission, display name, handler, and a short `prompt_hint` when routing guidance is useful.
3. Register the spec from `packages/amadeus/tools/__init__.py`.
4. Add or update its effective config in `configs/tools.yaml`.
5. Keep risky actions as `ask`; constrain filesystem, network, process, and external side effects in code, not only in the prompt.
6. Add focused `tests/test_tool_runtime.py` coverage and agent/runtime coverage when the tool affects turn behavior.
7. Update `docs/project-status.md` and `docs/implementation-notes.md` when the capability changes project status or tool policy.

## Known Gaps

- Task execution is still in-process. It has retries and stale-running recovery, but not durable multi-process workers, checkpoints, or resume.
- Background task completion is pushed to UI as `task.updated`; it does not yet automatically wake the model to narrate completion.
- Live2D/audio are practical but still need richer harness decisions, better non-Latin phoneme mapping, and deeper packaged-desktop coverage.
- GPT-SoVITS requires an external configured service and model assets; macOS `say` remains the practical fallback.
- Skills are read-only runtime instructions today. Full skill editing, marketplace sync, and sub-agent orchestration are future work.
- Memory v2 is functional but should stay in consolidation mode: review quality, summary policy, overflow behavior, and diagnostics matter more than adding another storage primitive.

## Next Recommended Work

Focus on desktop/runtime stabilization rather than rebuilding MVP pieces:

- Add richer task UI for attempts, next retry time, and task history.
- Decide whether task completion should create a user-visible notification only, or also trigger a model-authored follow-up message.
- Harden task execution toward a durable scheduler/worker lease if long-running work becomes important.
- Continue shrinking `apps/server` into transport-only bridge code.
- Expand deterministic Electron E2E coverage for real desktop/runtime interactions.
- Keep `AGENT.md` current whenever architecture ownership or recommended next steps change, because it is now injected into the agent prompt as project context for roles pointed at this workspace.

## Editing Notes

- Prefer small, scoped changes.
- Do not rewrite working MVP code into abstractions unless the extraction removes real complexity.
- Update `docs/project-status.md` whenever a phase completes or the next recommended step changes.
- Run `npm run typecheck` before handing off code changes.
- Run `npm --workspace apps/desktop run build` after desktop UI changes.
