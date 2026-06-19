# Server App

Local TypeScript bridge for the desktop app.

## Current role

`apps/server` is no longer the preferred long-term agent owner. Today it does two jobs:

1. **Preferred path bridge**
   - exposes `/health` and WebSocket `/ws`
   - receives desktop runtime events
   - relays user turns to Python `POST /agent/turn`
   - relays streamed NDJSON runtime events back to desktop
   - forwards desktop `tool.permission.response` events to Python `POST /tools/permission`

2. **Legacy fallback runtime**
   - if Python `/agent/turn` is unavailable, `apps/server/src/index.ts` still runs the older TypeScript turn loop
   - that fallback still handles provider calls, SQLite writes, tool execution, permission prompts, behavior events, and Python `/audio/speak` integration

## Responsibilities today

- WebSocket and HTTP endpoints for the desktop app
- Desktop event parsing and validation
- Python runtime relay
- Desktop permission-response routing
- Legacy fallback chat/tool/memory/audio path while migration is still in progress

## Endpoints

- `GET /health`
- WebSocket `ws://127.0.0.1:8788/ws`

## Notes

- The preferred user-turn path is now Python-first.
- The TypeScript fallback loop remains only to preserve behavior until parity tests and integration coverage are strong enough to remove it.
