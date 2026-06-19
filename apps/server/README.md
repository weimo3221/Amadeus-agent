# Server App

Local TypeScript bridge for the desktop app.

## Current role

`apps/server` is no longer an agent owner. Today it is the local transport bridge:

- exposes `/health` and WebSocket `/ws`
- receives desktop runtime events
- relays user turns to Python `POST /agent/turn`
- relays streamed NDJSON runtime events back to desktop
- forwards desktop `tool.permission.response` events to Python `POST /tools/permission`

## Responsibilities today

- WebSocket and HTTP endpoints for the desktop app
- Desktop event parsing and validation
- Python runtime relay
- Desktop permission-response routing

## Endpoints

- `GET /health`
- WebSocket `ws://127.0.0.1:8788/ws`

## Notes

- The preferred user-turn path is now Python-first.
- If Python `/agent/turn` is unavailable, the server reports a runtime error instead of running a second model/tool loop.
