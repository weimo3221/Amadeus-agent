# Amadeus Runtime

Python-side Amadeus runtime.

This package is intended to become the real agent core. The TypeScript apps should gradually become transport and device adapters:

- `apps/desktop`: Electron window, Live2D rendering, local UI.
- `apps/server`: WebSocket bridge between desktop events and the Python runtime.
- `packages/amadeus`: agent loop, model provider adapters, memory, tools, skills, and device-interface contracts.

Runtime modules:

- `agent`: conversation orchestration and event planning.
- `memory`: SQLite-backed conversation memory and later profile/summary retrieval.
- `model`: hosted/local model adapter boundary.
- `tools`: concrete tool implementations.
- `skills`: higher-level reusable behaviors built from tools/model/memory.
- `live2d`: interface contract for character state, expressions, motions, and lipsync cues.
- `audio`: interface contract for TTS/ASR/audio output.

TypeScript bridge exports:

- `@amadeus-agent/amadeus/events`: desktop/server event protocol types.
- `@amadeus-agent/amadeus/tools`: tool schema metadata, permission metadata, config loading, and Python runtime bridge.

Run:

```bash
python packages/amadeus/server.py
```

Default endpoint:

```text
http://127.0.0.1:8790
```

Current HTTP API:

- `GET /health`
- `POST /tools/execute`
- `GET /memory/count?sessionId=default`
- `GET /memory/messages?sessionId=default&limit=40`
- `POST /memory/messages`
- `POST /memory/reset`
