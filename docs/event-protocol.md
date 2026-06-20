# Event Protocol

This file documents the current desktop/server event protocol that is actually implemented in the codebase. The source of truth for the typed current event set is `packages/amadeus/events.ts`.

All events are JSON objects with this shape:

```ts
export interface RuntimeEvent<TType extends string = string, TPayload = unknown> {
  id: string
  type: TType
  sessionId: string
  timestamp: string
  payload: TPayload
}
```

## Current Desktop to Server Events

### user.message

```json
{
  "type": "user.message",
  "payload": {
    "text": "What should I focus on today?",
    "inputMode": "text"
  }
}
```

### session.reset

```json
{
  "type": "session.reset",
  "payload": {}
}
```

### tool.permission.response

```json
{
  "type": "tool.permission.response",
  "payload": {
    "requestId": "permission-request-id",
    "approved": true
  }
}
```

## Current Server to Desktop Events

### server.hello

```json
{
  "type": "server.hello",
  "payload": {
    "name": "amadeus-agent-server",
    "model": "deepseek-v4-flash",
    "memoryMessages": 12,
    "toolPermissions": []
  }
}
```

### memory.updated

```json
{
  "type": "memory.updated",
  "payload": {
    "memoryMessages": 13
  }
}
```

### assistant.delta

```json
{
  "type": "assistant.delta",
  "payload": {
    "text": "Let's start with"
  }
}
```

### assistant.message

```json
{
  "type": "assistant.message",
  "payload": {
    "text": "Let's start with the highest-impact task."
  }
}
```

### assistant.state

```json
{
  "type": "assistant.state",
  "payload": {
    "state": "thinking"
  }
}
```

Allowed states in the shared type:

- `idle`
- `listening`
- `thinking`
- `speaking`
- `tool-running`
- `error`

Note:

- `listening` exists in the shared type but is not currently a meaningful active state in the desktop/server flow.

### character.behavior

```json
{
  "type": "character.behavior",
  "payload": {
    "emotion": "focused",
    "expression": "smile",
    "motion": "nod",
    "intensity": 0.7
  }
}
```

### tool.started

```json
{
  "type": "tool.started",
  "payload": {
    "toolName": "search_files",
    "displayName": "Searching files"
  }
}
```

### tool.finished

```json
{
  "type": "tool.finished",
  "payload": {
    "toolName": "search_files",
    "ok": true,
    "durationMs": 12,
    "failureCode": null,
    "resultPreview": "truncated JSON preview",
    "outputTruncated": true
  }
}
```

`durationMs`, `failureCode`, `resultPreview`, and `outputTruncated` are optional for compatibility with older runtime events. Python ToolRuntime emits duration and failure metadata when execution reaches the structured `ToolResult` path. It emits `resultPreview` and `outputTruncated` only when a successful tool result was compacted before being written back into model context, either by a per-tool policy such as `search_files` or by the global size fallback. `read_file` uses explicit line-windowing instead of hidden runtime compression, reports unsupported image/PDF/binary/unknown file kinds directly, and `patch` returns its bounded diff result directly. Permission-denied, disabled, unknown, cancelled, timed out, or guardrail-blocked decisions emit stable failure codes where available.

### tool.audit

```json
{
  "type": "tool.audit",
  "payload": {
    "recordId": "audit-record-id",
    "timestamp": "2026-06-19T00:00:00.000000+00:00",
    "sessionId": "default",
    "toolName": "search_files",
    "decision": "finished",
    "ok": true,
    "durationMs": 12,
    "failureCode": null
  }
}
```

Current decisions:

- `started`
- `finished`
- `denied`
- `blocked`
- `failed`

Current behavior:

- Python emits audit events during `/agent/turn`.
- The runtime keeps an in-process audit log for the current runtime instance and persists the same records to SQLite for diagnostics after restart.
- Persisted audit records are queryable through `GET /tools/audit` with optional filters: `sessionId`, `toolName`, `decision`, `ok`, `failureCode`, and `limit`.

### tool.permission.request

```json
{
  "type": "tool.permission.request",
  "payload": {
    "requestId": "permission-request-id",
    "toolName": "search_files",
    "displayName": "Searching local files",
    "reason": "Allow Amadeus to search local project files?"
  }
}
```

Current flow:

- Python emits this request during `/agent/turn`.
- `apps/server` relays it to desktop.
- Desktop shows the inline Allow / Deny prompt.
- Desktop responds with `tool.permission.response`.
- `apps/server` forwards that response to Python `/tools/permission`.

### audio.tts-ready

```json
{
  "type": "audio.tts-ready",
  "payload": {
    "audioUrl": "http://127.0.0.1:8790/audio/files/cache/session-1-message-3.wav",
    "durationMs": 3200
  }
}
```

Current behavior:

- Desktop prefers runtime audio when this event arrives.
- If no runtime audio arrives, desktop schedules local `speechSynthesis` fallback after `assistant.message`.
- There is no separate emitted runtime fallback event today.

### error

```json
{
  "type": "error",
  "payload": {
    "code": "provider_error",
    "message": "The model provider did not respond."
  }
}
```

## Current Bridge to Python Runtime API

Current active endpoints used by the bridge/runtime:

```text
GET /health
GET /tools/list
GET /tools/audit
POST /agent/turn
POST /tools/execute
POST /tools/permission
GET /memory/count
GET /memory/messages
GET /memory/summary
POST /memory/messages
POST /memory/summary
POST /memory/reset
POST /audio/speak
GET /audio/files/{relativePath}
```

Python runtime responses from `/agent/turn` are streamed as NDJSON using the same event names where possible.

## Planned Protocol Extensions

These are discussed elsewhere in the docs but are not part of the current implemented event set in `packages/amadeus/events.ts`:

### Planned desktop to server events

- `desktop.capabilities`
- `character.capabilities`
- `audio.capabilities`
- `desktop.pointer`
- `desktop.character.click`
- `audio.playback-started`
- `audio.playback-ended`
- `audio.playback-error`
- `user.voice-start`
- `user.voice-chunk`
- `user.voice-end`

### Planned server to desktop events

- `character.lipsync`
- `audio.tts-fallback`
- `audio.lipsync-cues`

### Planned bridge/runtime endpoints

- `POST /agent/cancel`
- `POST /agent/message`

These planned items should be documented as current only after they are added to the active shared event types and wired in code.
