# CLAUDE.md

This file gives Claude and Claude-like coding agents the working context for this repository.

## Project Summary

`amadeus-agent` is a desktop Live2D companion agent. It combines:

- Electron + Vite desktop UI.
- Live2D character rendering and behavior control.
- A thin Node/TypeScript desktop bridge.
- A Python-first agent runtime.
- OpenAI-compatible LLM provider calls.
- SQLite-backed sessions, memory, tasks, schedules, audit records, and summaries.
- Model-triggered Python tools through ToolRuntime.
- Permission-aware tool execution, runtime skills, local ASR/TTS, and Live2D/audio harnesses.

The product direction is a desktop character agent, not a generic chatbot page. Keep user-visible work focused on an interactive desktop companion.

## Read First

Before making changes, read:

1. `docs/project-status.md`: live status, completed work, and next step.
2. `docs/architecture.md`: intended module boundaries.
3. `docs/event-protocol.md`: runtime event contract.
4. `AGENT.md`: broader contributor guide.

## Important Current Facts

- Desktop shell: `apps/desktop`.
- Production Main UI workbench: `apps/desktop-ui-next`.
- TypeScript bridge: `apps/server`.
- Python runtime: `packages/amadeus`.
- Shared events: `packages/amadeus/events.ts`.
- Tool bridge types and Python helper clients: `packages/amadeus/tools.ts`.
- Concrete tool specs, permissions, config loading, execution, audit, timeout, and guardrails live in Python under `packages/amadeus/tools` and `packages/amadeus/tool_runtime`.
- SQLite database path: `data/amadeus.sqlite`.
- Default Companion session id is `companion:default`.
- Python `/agent/turn` is the preferred and only active turn path. If Python is unavailable, `apps/server` reports a runtime error instead of running a TypeScript fallback loop.
- `configs/tools.yaml` is loaded by Python ToolRuntime and controls effective tool enabled/permission state.
- `configs/runtime.yaml` controls memory/context, summary compaction, review, and desktop/Live2D runtime tuning.
- `apps/server` should keep shrinking toward transport/proxy/feedback responsibilities.
- The current recommended work is desktop/runtime stabilization, not rebuilding MVP pieces.

## Common Commands

Run from repo root:

```bash
npm install
npm run typecheck
npm test
npm run test:e2e
npm --workspace apps/server run dev
npm --workspace apps/desktop run dev
npm --workspace apps/desktop-ui-next run build
npm run dev
```

Local endpoints:

```text
TypeScript bridge: http://127.0.0.1:8788/health
Bridge WebSocket: ws://127.0.0.1:8788/ws
Python runtime: http://127.0.0.1:8790/runtime/health
```

## Environment

Use `.env` locally. Never print or commit API keys.

Expected variables:

```text
AMADEUS_LLM_PROVIDER
DEEPSEEK_API_KEY
DEEPSEEK_MODEL
VITE_AGENT_WS_URL
```

`VITE_AGENT_WS_URL` usually points to:

```text
ws://127.0.0.1:8788/ws
```

## Boundaries To Preserve

- Desktop renders and interacts; Python thinks, remembers, executes tools, manages skills, and plans audio/Live2D behavior.
- `apps/server` transports WebSocket/HTTP events and proxies Python runtime surfaces.
- Do not put provider-specific LLM logic in `apps/desktop`.
- Do not put provider-specific LLM logic, tool execution, memory semantics, or audio/model-library ownership in `apps/server`.
- Do not execute tools from `apps/desktop`; desktop only asks the user for permission and sends responses.
- Keep all shared event type changes in `packages/amadeus/events.ts`.
- Keep tool permission checks in Python ToolRuntime even if the desktop also shows UI.
- Use explicit WebSocket events instead of ad hoc JSON shapes.

## Tool Design Rules

All tools should go through Python ToolRuntime.

Tool entries should have:

- `name`
- `displayName`
- `enabled`
- `permission`
- OpenAI-compatible `schema`
- execution handler
- optional `prompt_hint`
- optional permission request description

Permission policy:

- `allow`: safe, non-sensitive tools such as current time.
- `ask`: persistent writes, shell/process execution, external apps/URLs, page-content fetches, sensitive local exposure, or surprising side effects.
- `deny`: visible to config but unavailable at runtime.

Do not reintroduce keyword-matching tool execution. Tool use should be model-triggered through `tool_calls`.

## Desktop UI Notes

- The product has two desktop surfaces: Companion and Main UI.
- Companion is a compact transparent Live2D/voice presence with lightweight input and transient bubbles.
- `apps/desktop-ui-next` is the production Main UI workbench for chat history, sessions, tasks, timed messages, skills, memory, MCP, permissions, diagnostics, and configuration.
- Avoid large marketing UI, hero sections, or decorative cards.
- Keep controls dense and useful.
- Text must fit in the compact window.
- Keep Companion controls functional: Pin/Unpin, Minimize, Voice On/Off, Close.
- The inline tool permission prompt is part of the runtime safety model; keep it visible and direct.

## Server Notes

- `apps/server` is a bridge, not an agent owner.
- It may validate WebSocket clients, manage session rooms, proxy runtime HTTP surfaces, relay Python NDJSON events, and forward desktop feedback/permission responses.
- It should not regain model calls, memory writes, tool execution, Live2D model-library ownership, or audio provider decisions.

## Next Recommended Work

Focus on desktop/runtime stabilization:

- Continue hardening the Vue Main UI as the single production workbench.
- Keep Companion lightweight, Live2D/voice-focused, and transient.
- Improve session switching and explicit Companion attach/view flows.
- Add richer task execution UX only on top of the existing task model: attempts, retry timing, task history, terminal results, review/approval, artifacts, and notifications.
- Move toward a process-backed runner behind the existing `TaskRunner` contract if long-running work becomes important.
- Improve MCP HTTP diagnostics and role-scoped visibility before taking on stdio/SSE lifecycle management.
- Keep ToolRuntime and Memory v2 in consolidation mode: diagnostics, ranking quality, review quality, summary/profile policy, overflow behavior, and per-tool output policy.
- Expand deterministic Electron E2E coverage around real desktop/runtime interactions.
- Fix documentation drift when implementation boundaries move.

## Documentation Rules

Update `docs/project-status.md` whenever:

- A phase or subphase is completed.
- The next recommended phase changes.
- A known issue is fixed or discovered.
- Tool behavior or permission behavior changes.

Keep this file short enough to be useful as startup context.
