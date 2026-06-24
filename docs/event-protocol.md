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

### desktop.capabilities

```json
{
  "type": "desktop.capabilities",
  "payload": {
    "desktop": {
      "runtime": "electron",
      "protocolVersion": 1
    },
    "live2d": {
      "available": true,
      "modelId": "hiyori-free",
      "expressions": ["smile"],
      "motions": ["Idle", "TapBody"]
    },
    "audio": {
      "runtimeAudio": true,
      "speechSynthesis": true,
      "voiceCount": 12
    }
  }
}
```

Current behavior:

- Desktop sends this after `server.hello` and again after a Live2D model finishes loading.
- The bridge forwards it to Python `/runtime/feedback`, where `HarnessFeedbackPolicy` records it for runtime policy.

### audio.playback-started / audio.playback-ended / audio.playback-error

```json
{
  "type": "audio.playback-started",
  "payload": {
    "source": "runtime_audio",
    "audioUrl": "http://127.0.0.1:8790/audio/files/cache/tts.wav"
  }
}
```

```json
{
  "type": "audio.playback-error",
  "payload": {
    "source": "runtime_audio",
    "audioUrl": "http://127.0.0.1:8790/audio/files/cache/tts.wav",
    "reason": "audio_element_error"
  }
}
```

Current behavior:

- Desktop emits playback feedback for runtime audio start, end, and error.
- On runtime audio error or browser play rejection, desktop falls back to system `speechSynthesis`.
- Python records these events through `HarnessFeedbackPolicy`.
- The runtime audio layer can emit `audio.lipsync-cues` before `audio.tts-ready`. When a TTS provider returns native `lipsyncCues` / `visemes` / `phonemes` data, Python normalizes and forwards those cues directly; otherwise it falls back to a text-driven phoneme/viseme planner and can optionally modulate cue intensity from local cached `wav` envelope data. The Live2D harness still maps playback start/end/error into `character.behavior`, and may emit coarse fallback `audio.lipsync-cues` only when runtime cue playback is not already active.
- The mapping is configurable through `configs/harnesses.yaml` under `live2d.audioPlaybackBehaviors`.
- Desktop prefers runtime-provided `audio.lipsync-cues` when present, otherwise falls back to local Web Audio amplitude analysis, and finally to the older timed mouth loop.

### memory.review.*

Desktop can request memory review data and actions through the bridge:

- `memory.review.list`: list pending/recent candidates and jobs.
- `memory.review.run`: trigger a manual review for the current session.
- `memory.review.accept`: promote one candidate into durable `memory_items`.
- `memory.review.reject`: reject one candidate without writing durable memory.

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

### memory.review.candidates / memory.review.jobs / memory.review.updated

The bridge emits memory review state in response to desktop `memory.review.*` requests and after review actions. Candidates are pending proposals only; durable memory is written only after an accept action.

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

### audio.lipsync-cues

```json
{
  "type": "audio.lipsync-cues",
  "payload": {
    "source": "runtime_audio",
    "audioUrl": "http://runtime/audio.wav",
    "durationMs": 480,
    "cues": [
      { "offsetMs": 0, "mouthOpen": 0.2 },
      { "offsetMs": 90, "mouthOpen": 0.8 },
      { "offsetMs": 180, "mouthOpen": 0.3 }
    ]
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
    "failureCode": null,
    "metadata": {
      "turnId": "turn-id",
      "toolCallId": "call-id",
      "workspaceEpoch": 0,
      "workspaceEpochAfter": 1,
      "workspaceMutated": true
    }
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
- Audit records may include a `metadata` object. Normal agent-loop tool calls include `turnId`, `toolCallId`, and `workspaceEpoch`; finished file mutation calls also include `workspaceEpochAfter` and `workspaceMutated`.
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

### memory.context.used

```json
{
  "type": "memory.context.used",
  "payload": {
    "sourceCounts": {
      "conversation_summary": 1,
      "memory_item": 2,
      "retrieval": 1
    },
    "sourceCount": 4,
    "coveredThroughMessageId": 12,
    "sessionId": "default",
    "turnId": "turn-uuid",
    "phase": "turn_start",
    "timestamp": "2026-06-21T12:00:00+00:00",
    "sources": [
      {
        "kind": "memory_item",
        "sourceId": "7",
        "contentChars": 86,
        "reason": "accepted durable structured memory",
        "metadata": {
          "scope": "project",
          "confidence": 0.9
        }
      }
    ]
  }
}
```

Current behavior:

- Python emits this diagnostic after assembling per-turn model context.
- Summary and accepted structured memory are injected into the temporary system message.
- FTS retrieval snippets are injected into the temporary current user message as `<memory-context>`.
- Injected context is API-call-time only and is not written back to SQLite message history.
- The runtime also stores the most recent `context.diagnosticsLimit` diagnostics per session in an in-memory ring buffer for developer diagnostics. This buffer is not persisted across runtime restarts.

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
GET /runtime/health
GET /runtime/feedback
GET /tools/list
GET /tools/audit
POST /runtime/config/reload
POST /runtime/feedback
POST /agent/turn
POST /tools/execute
POST /tools/permission
GET /memory/count
GET /memory/messages
GET /memory/context/diagnostics
GET /memory/search
GET /memory/items
GET /memory/summary
GET /memory/review/candidates
GET /memory/review/jobs
POST /memory/messages
POST /memory/items
POST /memory/items/delete
POST /memory/review/candidates
POST /memory/review/accept
POST /memory/review/reject
POST /memory/review/run
POST /memory/summary
POST /memory/compact
POST /memory/reset
POST /audio/speak
GET /audio/files/{relativePath}
GET /live2d/config
GET /live2d/models
POST /live2d/select
GET /live2d/models/{relativePath}
```

`GET /runtime/health` is the structured local diagnostics endpoint. It returns an aggregate `status` plus per-subsystem checks for runtime, model config, memory DB, tools, Live2D, audio, and effective runtime config. It intentionally avoids live external provider calls so startup diagnostics stay fast and deterministic.

`POST /runtime/feedback` records desktop capability and runtime audio playback feedback into the Python-side harness policy. It can return harness-emitted runtime events, such as `character.behavior` for playback-driven Live2D state changes. `GET /runtime/feedback?sessionId=default` returns the latest per-session snapshot, including normalized `desktopCapabilities`, `audioPlayback`, and recent feedback events.

Python runtime responses from `/agent/turn` are streamed as NDJSON using the same event names where possible.

## Planned Protocol Extensions

These are discussed elsewhere in the docs but are not part of the current implemented event set in `packages/amadeus/events.ts`:

### Planned desktop to server events

- `character.capabilities`
- `audio.capabilities`
- `desktop.pointer`
- `desktop.character.click`
- `user.voice-start`
- `user.voice-chunk`
- `user.voice-end`

### Planned server to desktop events

- `character.lipsync`
- `audio.tts-fallback`

### Planned bridge/runtime endpoints

- `POST /agent/cancel`
- `POST /agent/message`

These planned items should be documented as current only after they are added to the active shared event types and wired in code.
