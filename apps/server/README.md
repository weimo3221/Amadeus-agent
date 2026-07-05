# Server App

Local TypeScript bridge for the desktop app.

## Current role

`apps/server` is no longer an agent owner. Today it is the local transport bridge:

- exposes `/health` and WebSocket `/ws`
- proxies Live2D config/model assets and model switching endpoints from the Python runtime
- receives desktop runtime events
- relays user turns to Python `POST /agent/turn`
- relays streamed NDJSON runtime events back to desktop
- forwards desktop `tool.permission.response` events to Python `POST /tools/permission`
- proxies skills, sessions, tasks, agent, audio, and Live2D HTTP surfaces to the Python runtime
- proxies memory review list/run/accept/reject events to the Python runtime
- forwards desktop capability and audio playback feedback events to Python `/runtime/feedback`

## Responsibilities today

- WebSocket and HTTP endpoints for the desktop app
- Desktop event parsing and validation
- Python runtime relay
- Desktop permission-response routing
- Python-owned HTTP surface proxying for Live2D, audio, skills, sessions, tasks, and agent endpoints
- Desktop device feedback observation

## Endpoints

- `GET /health`
- `GET /live2d/config`
- `GET /live2d/models`
- `GET /live2d/models/{relativePath}`
- `POST /live2d/select`
- `GET /skills/list`
- `GET /skills/view`
- `GET /sessions`
- `GET /tasks`
- `POST /agent/cancel`
- `POST /audio/transcribe`
- WebSocket `ws://127.0.0.1:8788/ws`

## Notes

- The preferred user-turn path is now Python-first.
- The bridge does not infer or own the active LLM model; runtime model details come from Python `/runtime/config`.
- If Python `/agent/turn` is unavailable, the server reports a runtime error instead of running a second model/tool loop.
