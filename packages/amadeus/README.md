# Amadeus Runtime

Python-side Amadeus runtime.

This package is intended to become the real agent core. The surrounding TypeScript apps are moving toward transport and device-adapter roles:

- `apps/desktop`: Electron window, Live2D rendering, local UI, runtime audio playback, and permission UI.
- `apps/server`: WebSocket bridge between desktop events and the Python runtime.
- `packages/amadeus`: preferred agent turn path, memory, tools, runtime HTTP API, and future model/skills/harness boundaries.

## Current active modules

- `agent.py`: active preferred turn flow.
- `memory.py`: active SQLite-backed message history.
- `tools.py`: active Python tool implementations.
- `audio.py`: active audio/TTS interface.
- `server.py`: active HTTP runtime.

## Current placeholder boundaries

These files exist as future module boundaries, but are not yet the main active implementation path:

- `model.py`
- `skills.py`
- `live2d.py`

## Current runtime behavior

- `agent.py` contains the real preferred turn logic today.
- The runtime loads recent SQLite history, saves user and assistant messages, makes the tool-decision call, executes Python tools, streams `assistant.delta`, emits `assistant.message`, and may emit `audio.tts-ready`.
- Tool permission requests are brokered through streamed `tool.permission.request` events plus `POST /tools/permission`.
- Audio is wired, but the default runtime still uses `NoopTtsProvider`, so Python TTS does not produce a real audio URL unless a real provider is added.

## Run

```bash
python packages/amadeus/server.py
```

Default endpoint:

```text
http://127.0.0.1:8790
```

## Current HTTP API

- `GET /health`
- `GET /tools/list`
- `POST /agent/turn`
- `POST /tools/execute`
- `POST /tools/permission`
- `GET /memory/count?sessionId=default`
- `GET /memory/messages?sessionId=default&limit=40`
- `POST /memory/messages`
- `POST /memory/reset`
- `POST /audio/speak`
- `GET /audio/files/{relativePath}`

## Notes

- `/agent/turn` returns an NDJSON event stream.
- Current test coverage is centered on `tests/test_python_agent_runtime.py`, which covers deterministic runtime behavior in `AgentRuntime` rather than full HTTP/bridge integration.
