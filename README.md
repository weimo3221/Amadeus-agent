# Amadeus Agent

Amadeus Agent is a desktop virtual character agent designed around a Live2D presence, real-time interaction, and a local-first runtime.

The project is now in a Python-first migration stage:

- `apps/desktop`: desktop Live2D shell, chat UI, runtime audio playback, and permission UI
- `apps/server`: TypeScript bridge between the desktop and Python runtime, plus local Live2D/audio static serving for the renderer
- `packages/amadeus`: preferred Python agent turn path, provider boundary, Memory v2 context assembly, ToolRuntime, scheduled messages, persistent todos, audio/Live2D runtime helpers, and runtime HTTP API
- `packages/live2d-stage`: intended desktop Live2D rendering adapter boundary, not yet the active implementation package

## Current flow

Preferred path today:

1. Desktop sends `user.message` over WebSocket.
2. `apps/server` relays the turn to Python `POST /agent/turn`.
3. Python runtime assembles API-call-time memory context, performs tool decisions, executes Python tools, streams assistant events, and may emit runtime audio.
4. `apps/server` relays runtime events back to desktop.
5. Desktop updates chat, permission UI, audio playback, and Live2D behavior.

Developer diagnostics:

- `GET /runtime/health` reports structured local health for runtime, model config, memory DB, tools, Live2D, audio, and effective config.
- `GET /memory/context/diagnostics?sessionId=default&limit=10` reports recent per-session Memory v2 context assembly decisions.
- `GET /scheduled-jobs?sessionId=companion:default&activeOnly=true` reports active scheduled companion messages.
- `GET /todos?sessionId=companion:default&activeOnly=true` reports persistent session todo items.

Runtime failure behavior:

- If Python `/agent/turn` is unavailable, `apps/server` reports a runtime error by default.

## Run

Development stack:

```bash
python -m pip install -r requirements.txt
npm run dev
```

This starts the Python runtime, waits for `/runtime/health`, starts the TypeScript bridge, waits for `/health`, then starts the Electron desktop. If a required child process exits, the supervisor terminates the rest of the stack so the local runtime does not silently split into half-running processes.

Useful variants:

```bash
npm run dev:stack -- --no-desktop
npm run dev:legacy
```

## Design References

- `../airi`: primary reference for desktop Live2D, Electron, character UI, audio, and runtime packaging.
- `../hermes-agent`: reference for tool systems, memory, skills, scheduled tasks, and long-running agent behavior.
- `../deepagents`: reference for long-horizon task planning, sub-agents, filesystem tools, and context management.

## Docs

- Current implementation status: [docs/project-status.md](docs/project-status.md)
- Current/target architecture: [docs/architecture.md](docs/architecture.md)
- Forward-looking roadmap: [docs/roadmap.md](docs/roadmap.md)
- Runtime event contract: [docs/event-protocol.md](docs/event-protocol.md)
