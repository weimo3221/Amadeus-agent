# CLAUDE.md

This file gives Claude and Claude-like coding agents the working context for this repository.

## Project Summary

`amadeus-agent` is a desktop Live2D companion agent. It combines:

- Electron + Vite desktop UI.
- Live2D character rendering and behavior control.
- Local Node/TypeScript agent runtime.
- OpenAI-compatible LLM provider calls.
- SQLite message memory.
- Model-triggered local tools.
- Permission-aware tool execution.

The product direction is a desktop character agent, not a generic chatbot page. Keep user-visible work focused on an interactive desktop companion.

## Read First

Before making changes, read:

1. `docs/project-status.md`: live status, completed work, and next step.
2. `docs/architecture.md`: intended module boundaries.
3. `docs/event-protocol.md`: runtime event contract.
4. `AGENT.md`: broader contributor guide.

## Important Current Facts

- Desktop app: `apps/desktop`.
- Server runtime: `apps/server`.
- Shared events: `packages/shared/events.ts`.
- Tool registry currently lives in `apps/server/src/index.ts`.
- SQLite database path: `data/amadeus.sqlite`.
- Current session id is stable: `default`.
- Current tools:
  - `get_current_time`: `allow`.
  - `roll_dice`: `ask`.
- `configs/tools.yaml` mirrors intended settings but is not loaded by the server yet.
- The next recommended task is the Tool Config Loader.

## Common Commands

Run from repo root:

```bash
npm install
npm run typecheck
npm --workspace apps/server run dev
npm --workspace apps/desktop run dev
npm --workspace apps/desktop run build
npm run dev
```

Server:

```text
http://127.0.0.1:8788/health
ws://127.0.0.1:8788/ws
```

## Environment

Use `.env` locally. Never print or commit API keys.

Expected variables:

```text
OPENAI_BASE_URL
OPENAI_API_KEY
OPENAI_MODEL
VITE_AGENT_WS_URL
VITE_LIVE2D_MODEL_URL
```

`VITE_AGENT_WS_URL` usually points to:

```text
ws://127.0.0.1:8788/ws
```

## Boundaries To Preserve

- Desktop renders and interacts; server thinks, remembers, and executes tools.
- Do not put provider-specific LLM logic in `apps/desktop`.
- Do not execute tools from `apps/desktop`; desktop only asks the user for permission and sends responses.
- Keep all shared event type changes in `packages/shared/events.ts`.
- Keep tool permission checks on the server even if the desktop also shows UI.
- Use explicit WebSocket events instead of ad hoc JSON shapes.

## Tool Design Rules

All tools should go through the registry.

Tool entries should have:

- `name`
- `displayName`
- `enabled`
- `permission`
- OpenAI-compatible `schema`
- `execute`
- optional `describeRequest`

Permission policy:

- `allow`: safe, non-sensitive tools such as current time.
- `ask`: anything that reads local data, opens external resources, uses network, mutates state, schedules actions, or may surprise the user.
- `deny`: visible to config but unavailable at runtime.

Do not reintroduce keyword-matching tool execution. Tool use should be model-triggered through `tool_calls`.

## Desktop UI Notes

- The app is a compact desktop companion window, not a landing page.
- Avoid large marketing UI, hero sections, or decorative cards.
- Keep controls dense and useful.
- Text must fit in the compact window.
- Keep titlebar controls functional: Pin/Unpin, Minimize, Voice On/Off, Close.
- The inline tool permission prompt is part of the runtime safety model; keep it visible and direct.

## Server Notes

- `apps/server/src/index.ts` is currently doing too much, but do not split it without a concrete reason.
- The next extraction candidates are tool registry/config loading, memory, and provider adapter.
- `node:sqlite` currently prints an experimental warning. That is known.
- Keep the final assistant reply persisted, not raw tool messages.

## Next Task: Tool Config Loader

Implement this before adding more powerful tools:

1. Read `configs/tools.yaml` at server startup.
2. Validate `enabled` and `permission` values.
3. Warn or error clearly on unknown tool names.
4. Apply loaded settings to registry entries.
5. Keep secure defaults if config is missing or invalid.
6. Expose loaded tool status to desktop diagnostics if useful.
7. Update `docs/project-status.md`.
8. Verify with:

```bash
npm run typecheck
npm --workspace apps/desktop run build
```

## Documentation Rules

Update `docs/project-status.md` whenever:

- A phase or subphase is completed.
- The next recommended phase changes.
- A known issue is fixed or discovered.
- Tool behavior or permission behavior changes.

Keep this file short enough to be useful as startup context.
