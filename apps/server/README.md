# Server App

Local TypeScript bridge for the desktop app.

## Current role

`apps/server` is no longer an agent owner. Today it is the local transport bridge:

- exposes `/health` and WebSocket `/ws`
- serves local Live2D config/model assets and model switching endpoints for the desktop renderer
- receives desktop runtime events
- relays user turns to Python `POST /agent/turn`
- relays streamed NDJSON runtime events back to desktop
- forwards desktop `tool.permission.response` events to Python `POST /tools/permission`
- proxies memory review list/run/accept/reject events to the Python runtime
- forwards desktop capability and audio playback feedback events to Python `/runtime/feedback`

## Responsibilities today

- WebSocket and HTTP endpoints for the desktop app
- Desktop event parsing and validation
- Python runtime relay
- Desktop permission-response routing
- Local Live2D model listing, selection, and static asset serving
- Desktop device feedback observation

## Endpoints

- `GET /health`
- `GET /live2d/config`
- `GET /live2d/models`
- `GET /live2d/models/{relativePath}`
- `POST /live2d/select`
- WebSocket `ws://127.0.0.1:8788/ws`

## Notes

- The preferred user-turn path is now Python-first.
- If Python `/agent/turn` is unavailable, the server reports a runtime error instead of running a second model/tool loop.
