# Amadeus Agent Contributor Guide

This repository builds a desktop Live2D companion agent. The goal is not just a chat box: Amadeus should be able to talk, show state through a Live2D character, remember conversation history, and run local tools with explicit permissions.

Use this file as the first stop when another coding agent or human contributor opens the repo.

## Current State

The MVP is already working:

- `apps/desktop` runs an Electron + Vite transparent desktop window.
- The desktop renders a remote Live2D test model, chat UI, window controls, debug controls, voice toggle, memory status, tool status, and inline tool permission prompts.
- `apps/server` runs a local Node/TypeScript runtime with HTTP health check and WebSocket events.
- Chat uses an OpenAI-compatible provider configured through `.env`.
- Message history is persisted to SQLite at `data/amadeus.sqlite`.
- Tool calling is model-triggered through OpenAI-compatible `tools` and `tool_calls`.
- Tool execution goes through a formal server-side `toolRegistry`.
- Tool permissions support `allow`, `ask`, and `deny`.
- Tool enabled/permission settings are loaded from `configs/tools.yaml` at server startup.
- Tool execution is moving toward the Python runtime under `packages/amadeus`.
- Desktop diagnostics show the loaded tool permission state from the server.
- Current tools:
  - `get_current_time`: allowed automatically.
  - `roll_dice`: asks for desktop permission before execution.

For the latest detailed progress, read `docs/project-status.md`.

## Repository Map

- `apps/desktop`: Electron shell, renderer UI, Live2D stage, speech synthesis, lipsync MVP, runtime WebSocket client.
- `apps/server`: Local agent runtime, provider calls, session memory, SQLite persistence, and permission checks.
- `packages/amadeus`: Python-owned agent brain plus TypeScript bridge exports for event protocol and tool schema/permission metadata.
- `packages/live2d-stage`: Desktop-side Live2D rendering adapter.
- `configs`: Character, provider, and tool config drafts.
- `docs`: Architecture, event protocol, roadmap, implementation notes, and live project status.
- `models/live2d`: Intended location for local Live2D model bundles.

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
- Keep `apps/server` as the local runtime owner for sessions, memory, LLM calls, tool execution, and permission enforcement.
- Communicate between desktop and server through the shared event protocol in `packages/amadeus/events.ts`.
- Add new runtime events to `packages/amadeus/events.ts` first, then update both server and desktop handlers.
- Keep tool schema/permission bridge code in `packages/amadeus/tools.ts`; keep concrete Python implementations in `packages/amadeus`; `apps/server` should own runtime permission prompts and transport wiring.
- New tools must define:
  - OpenAI-compatible schema.
  - `displayName`.
  - `enabled`.
  - `permission`: `allow`, `ask`, or `deny`.
  - execution handler.
  - request description for `ask` tools when useful.
- Any tool that reads files, opens URLs/apps, searches the web, runs commands, or mutates local state should start as `ask`.

## Current Event Flow

Main client-to-server events:

- `user.message`
- `session.reset`
- `tool.permission.response`

Main server-to-client events:

- `server.hello`
- `memory.updated`
- `assistant.state`
- `assistant.delta`
- `assistant.message`
- `character.behavior`
- `tool.started`
- `tool.finished`
- `tool.permission.request`
- `error`

Keep event payloads serializable and small.

## How To Add A Tool

1. Add the tool schema and permission metadata to `createDefaultToolRegistry()` in `packages/amadeus/tools.ts`.
2. Add the Python implementation to `packages/amadeus`.
3. Add or update its effective config in `configs/tools.yaml`.
4. If the tool uses `ask`, ensure the desktop prompt text is understandable.
5. Run `npm run typecheck`.
6. Test the Python sidecar and then test over WebSocket or through the desktop UI.
7. Update `docs/project-status.md`.

The current next step is to add the next practical permission-aware tool now that config loading is wired.

## Known Gaps

- Live2D model and Cubism runtime still load from remote URLs. Add a local model bundle under `models/live2d`.
- TTS uses browser/Electron `speechSynthesis`; voice quality depends on the OS.
- Lipsync is a timed mouth loop, not audio-driven or phoneme-aware.
- SQLite uses Node 24 `node:sqlite`, which is still experimental and prints a warning.
- Memory is raw message history only. Long-term facts, profile memory, summaries, and retrieval are not implemented yet.
- `packages` has been trimmed to the active Python-runtime architecture; avoid reintroducing placeholder TS packages unless they have concrete adapter code.

## Next Recommended Work

Start with `Phase 5 Continued: Practical Ask Tools` from `docs/project-status.md`:

- Add a local file search tool with `ask` permission.
- Add an `open_url` tool that asks before opening external URLs.
- Keep newly added tools disabled in `configs/tools.yaml` until verified.
- Add focused checks for allow, ask, deny, disabled, and unknown-tool behavior.

After that, good follow-up work is:

- Add a local Live2D model bundle.
- Improve memory beyond raw message storage.
- Improve TTS and lipsync.
- Add proactive reminders and daily brief.

## Editing Notes

- Prefer small, scoped changes.
- Do not rewrite working MVP code into abstractions unless the extraction removes real complexity.
- Update `docs/project-status.md` whenever a phase completes or the next recommended step changes.
- Run `npm run typecheck` before handing off code changes.
- Run `npm --workspace apps/desktop run build` after desktop UI changes.
