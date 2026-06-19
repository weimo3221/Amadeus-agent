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
   - the older TypeScript turn loop has been isolated into `apps/server/src/legacy-fallback.ts`
   - it is disabled by default and can be temporarily enabled with `AMADEUS_ENABLE_TS_FALLBACK=true`
   - that fallback still handles provider calls, SQLite writes, tool execution, permission prompts, behavior events, and Python `/audio/speak` integration when explicitly enabled

## Responsibilities today

- WebSocket and HTTP endpoints for the desktop app
- Desktop event parsing and validation
- Python runtime relay
- Desktop permission-response routing
- Optional legacy fallback chat/tool/memory/audio path while migration is still in progress

## Endpoints

- `GET /health`
- WebSocket `ws://127.0.0.1:8788/ws`

## Notes

- The preferred user-turn path is now Python-first.
- If Python `/agent/turn` is unavailable, the server now reports a runtime error by default instead of silently running the TypeScript fallback.
- The TypeScript fallback loop remains only as an explicit escape hatch until Electron renderer/UI coverage is strong enough to delete it.
