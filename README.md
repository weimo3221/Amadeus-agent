# Amadeus Agent

Amadeus Agent is a desktop virtual character agent designed around a Live2D presence, real-time interaction, and a local-first runtime.

The project is now in a Python-first migration stage:

- `apps/desktop`: Electron desktop shell with Companion (Live2D, lightweight chat, voice input) and Main UI window orchestration
- `apps/desktop-ui-next`: production Main UI workspace (Vue 3 + Vite + Tailwind) for chat, sessions, tasks, timed messages, skills, memory, and configuration
- `apps/server`: thin TypeScript bridge between desktop surfaces and the Python runtime, including WebSocket fanout plus Live2D/audio/runtime HTTP proxying
- `packages/amadeus`: preferred Python agent turn path, provider boundary, Memory v2 context assembly, ToolRuntime, scheduled messages, persistent todos, ASR/TTS/Live2D runtime helpers, and runtime HTTP API
- `packages/live2d-stage`: intended desktop Live2D rendering adapter boundary, not yet the active implementation package

## Current flow

Preferred path today:

1. Desktop sends `user.message` over WebSocket.
2. `apps/server` relays the turn to Python `POST /agent/turn`.
3. Python runtime assembles API-call-time memory context, performs tool decisions, executes Python tools, streams assistant events, and may emit runtime audio. DeepSeek V4 thinking mode is handled through a provider-aware reasoning layer so `reasoning_content` is replayed only for providers that require it.
4. `apps/server` relays runtime events back to desktop.
5. Desktop updates chat, permission UI, audio playback, Live2D behavior, scheduled-task state, and configuration views.

Voice input path:

1. Companion records microphone audio with `MediaRecorder`.
2. The renderer uploads the audio blob to the bridge `POST /audio/transcribe`.
3. `apps/server` forwards the binary body to Python `POST /audio/transcribe`.
4. Python uses the configured ASR provider. `asr.default: auto` selects local `faster-whisper` when available and otherwise falls back to a disabled provider.
5. The transcribed text is submitted back through the normal `user.message` path.

Developer diagnostics:

- `GET /runtime/health` reports structured local health for runtime, model config, memory DB, tools, Live2D, audio, and effective config.
- `GET /memory/context/diagnostics?sessionId=default&limit=10` reports recent per-session Memory v2 context assembly decisions.
- `GET /scheduled-jobs?sessionId=companion:default&activeOnly=false` reports scheduled companion messages, including completed/cancelled/failed terminal jobs.
- `GET /todos?sessionId=companion:default&activeOnly=true` reports persistent session todo items.
- `POST /audio/transcribe?format=webm` transcribes microphone input through the configured ASR provider.
- `GET /runtime/config` reports model provider settings, including `thinkingEnabled` and `reasoningEffort` for providers that support explicit reasoning mode.

Runtime failure behavior:

- If Python `/agent/turn` is unavailable, `apps/server` reports a runtime error by default.

## Run

Development stack:

```bash
python -m pip install -r requirements.txt
npm run dev
```

This starts the Python runtime, waits for `/runtime/health`, starts the TypeScript bridge, waits for `/health`, then starts the Electron desktop. If a required child process exits, the supervisor terminates the rest of the stack so the local runtime does not silently split into half-running processes.

`requirements.txt` includes `faster-whisper` for local ASR. The first transcription may download the selected Whisper model. Set `FASTER_WHISPER_MODEL_SIZE`, `FASTER_WHISPER_DEVICE`, `FASTER_WHISPER_COMPUTE_TYPE`, `FASTER_WHISPER_LANGUAGE`, or `FASTER_WHISPER_DOWNLOAD_ROOT` to tune the local ASR provider.

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
