# Event Protocol

This file documents the current desktop/server event protocol that is actually implemented in the codebase. The source of truth for the typed current event set is `packages/amadeus/events.ts`.

All events are JSON objects with this shape:

```ts
export interface RuntimeEvent<TType extends string = string, TPayload = unknown> {
  id: string
  type: TType
  sessionId?: string
  clientId?: string
  surface?: 'main-ui' | 'companion' | 'cli'
  timestamp?: string
  payload: TPayload
}
```

WebSocket clients connect with URL parameters:

```text
/ws?surface=companion&sessionId=companion:default
/ws?surface=main-ui&sessionId=companion:default
/ws?surface=cli&sessionId=cli:default
```

The bridge validates `surface`, assigns a `clientId`, stores clients in `sessionId -> clients[]`, and broadcasts runtime events to all connected clients in the same session. `surface` describes the client that produced an event; it is not a routing target.

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

`inputMode` is `text` for typed messages and `voice` for transcribed microphone input. Companion records microphone audio locally, transcribes it through HTTP `POST /audio/transcribe`, then sends the returned text through the same `user.message` event path.

### HTTP audio transcription

Voice input is intentionally not sent over WebSocket. The renderer posts the raw audio blob to the bridge, and the bridge forwards the binary body to Python:

```text
POST /audio/transcribe?format=webm
Content-Type: audio/webm
```

Successful response:

```json
{
  "ok": true,
  "text": "提醒我十分钟后喝水",
  "provider": "faster_whisper",
  "language": "zh",
  "durationMs": 420
}
```

Current behavior:

- Companion uses `MediaRecorder` and a microphone orb in the glass composer.
- `apps/server` proxies `/audio/transcribe` to Python without JSON re-encoding the binary body.
- Python selects the configured ASR provider. `asr.default: auto` chooses local `faster-whisper` when available, otherwise returns a disabled/noop transcription result.
- A non-empty transcription is submitted as a normal user message.

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

- Desktop clients send this after `server.hello`; Companion sends it again after a Live2D model finishes loading.
- Main UI normally reports `live2d.available=false`; Companion reports `live2d.available=true` after the model is ready.
- The bridge forwards it to Python `/runtime/feedback` with `clientId` and `surface`.
- `HarnessFeedbackPolicy` stores per-client capabilities and exposes an aggregate session capability view. `live2d.available` is true for the session when any connected client reports Live2D availability.

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
    "model": "deepseek-v4-pro",
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

The bridge emits memory review state in response to desktop `memory.review.*` requests and after review actions. Runtime review auto-promotes safe candidates into durable memory and marks their candidate records as accepted; pending candidates remain for manual accept/reject flows.

### scheduled.updated

```json
{
  "type": "scheduled.updated",
  "payload": {
    "action": "created",
    "job": {
      "id": "scheduled-job-id",
      "sessionId": "companion:default",
      "title": "Four pings",
      "message": "我在",
      "mode": "message",
      "lastTaskId": null,
      "scheduleDisplay": "Every 10 seconds",
      "status": "scheduled",
      "repeatCount": 4,
      "completedRuns": 0,
      "nextRunAt": "2026-07-02T10:00:10+00:00"
    }
  }
}
```

Current behavior:

- Python emits `scheduled.updated` for scheduled trigger lifecycle changes such as created, running, fired, paused, resumed, cancelled, completed, and failed.
- `mode="message"` is the default behavior. When it fires, Python persists the assistant message and broadcasts a normal `assistant.message` to every client in the session.
- `mode="agent_task"` turns the schedule into a trigger. When it fires, Python creates a tracked background task with `kind="scheduled_prompt"` and `source="scheduled_job"`, submits it to the task worker, stores the task id in `lastTaskId`, and emits `scheduled.updated`.
- Main UI listens for `scheduled.updated` to refresh the Timed Messages panel and fetches all statuses (`activeOnly=false`) so completed, cancelled, and failed jobs remain visible with terminal status labels and trigger-mode indicators.

### task.updated

```json
{
  "type": "task.updated",
  "payload": {
    "action": "running",
    "task": {
      "id": "task-id",
      "sessionId": "companion:default",
      "title": "Run report",
      "body": "生成一份状态报告",
      "kind": "scheduled_prompt",
      "source": "scheduled_job",
      "parentTaskId": null,
      "planItemId": null,
      "workerType": "agent",
      "status": "running",
      "attemptCount": 1,
      "maxAttempts": 3,
      "artifacts": [{ "type": "scheduled_job", "jobId": "scheduled-job-id" }]
    }
  }
}
```

Task records are the durable execution unit. Plans describe intent, scheduled jobs describe when to trigger, and tasks own execution, retry/cancel/recovery, results, errors, review gates, and artifacts. Standard artifact types are `file`, `diff`, `command_output`, `summary`, and `link`; unknown entries are normalized as `summary`.

When a task has `planItemId`, the Python task worker keeps the visible plan aligned with execution:

- task starts: linked plan item becomes `in_progress`
- task succeeds: linked plan item becomes `completed`
- task exhausts retries and fails: linked plan item returns to `pending`
- task is cancelled: linked plan item becomes `cancelled`
- review-required task succeeds: task becomes `blocked` with `blockedReason`; linked plan item remains pending until approval

### assistant.delta

```json
{
  "type": "assistant.delta",
  "payload": {
    "turnId": "turn-uuid",
    "text": "Let's start with"
  }
}
```

### assistant.message

```json
{
  "type": "assistant.message",
  "payload": {
    "turnId": "turn-uuid",
    "text": "Let's start with the highest-impact task."
  }
}
```

Desktop behavior:

- Main UI keeps the normal chat history view.
- Main UI uses `turnId` to bind streamed assistant content and turn-local plan archival to the user message that started the turn.
- Companion streams `assistant.delta` into a transient desktop bubble and marks it complete on `assistant.message`; the completed bubble fades out after a short delay instead of staying inside the input panel.

### assistant.reasoning.delta

```json
{
  "type": "assistant.reasoning.delta",
  "payload": {
    "turnId": "turn-uuid",
    "text": "I need the current date before choosing the weather lookup arguments."
  }
}
```

Notes:

- Python emits this when a provider returns reasoning text separately from final assistant content, currently through DeepSeek `reasoning_content`.
- Main UI attaches it to the active assistant message and renders it in a collapsed "思考过程" panel.
- Companion intentionally ignores this event so transient desktop bubbles remain concise.
- Provider replay is handled server-side: DeepSeek thinking/tool-call rounds receive `reasoning_content` when required, while other providers have provider-specific reasoning fields stripped before API calls.

### assistant.state

```json
{
  "type": "assistant.state",
  "payload": {
    "turnId": "turn-uuid",
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
- `turnId` is present for state events emitted from a running Python agent turn.

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

### task.plan.updated

```json
{
  "type": "task.plan.updated",
  "payload": {
    "sessionId": "default",
    "turnId": "turn-uuid",
    "items": [
      {
        "id": "wire-context",
        "content": "Inject active plan into context assembly",
        "status": "in_progress"
      },
      {
        "id": "wire-ui",
        "content": "Show active plan in Main UI",
        "status": "pending"
      }
    ],
    "summary": {
      "total": 2,
      "pending": 1,
      "inProgress": 1,
      "completed": 0,
      "cancelled": 0
    },
    "updatedAt": "2026-06-30T00:00:00+00:00",
    "changed": true
  }
}
```

Current behavior:

- Python emits this after the `update_plan` tool successfully reads or writes the SQLite-backed session plan. During `/agent/turn`, the event includes `turnId`.
- Only `pending` and `in_progress` items are injected into model context as `<active-plan>`.
- Main UI renders runtime plan updates on an assistant-side message for the matching `turnId`, so repeated `update_plan` calls in one turn refresh one live Agent panel instead of creating multiple global panels or making the plan look user-authored. On `assistant.message`, the panel is archived under that user turn; completed plans collapse by default, incomplete plans remain marked incomplete.
- Main UI still restores the latest session plan with `GET /sessions/{id}/plan` as a compatibility fallback for reloaded sessions.
- Companion does not render the full plan in the Live2D bubble.

### agent.turn.started

```json
{
  "type": "agent.turn.started",
  "payload": {
    "sessionId": "default",
    "turnId": "turn-uuid",
    "startedAt": "2026-06-30T00:00:00+00:00"
  }
}
```

### agent.turn.cancelled

```json
{
  "type": "agent.turn.cancelled",
  "payload": {
    "sessionId": "default",
    "turnId": "turn-uuid",
    "phase": "after_tool"
  }
}
```

Current behavior:

- Python registers a running turn before entering the model/tool loop and clears it when the generator finishes.
- `POST /agent/cancel` sets the active turn's cooperative cancel event.
- Tool execution receives the same cancel event through `ToolContext`.
- Cancellation is cooperative: it is checked between model/tool phases and by tools that call `context.is_cancelled()`. It does not forcibly terminate an in-flight provider HTTP request.

### task.updated

```json
{
  "type": "task.updated",
  "payload": {
    "action": "created",
    "task": {
      "id": "7a4d6f0d5fd1450d95e8b8fb5e4b1e22",
      "sessionId": "default",
      "title": "Research task persistence",
      "body": "Check SQLite task storage.",
      "status": "queued",
      "priority": 5,
      "dueAt": null,
      "claimLock": null,
      "lastHeartbeat": null,
      "result": null,
      "error": null,
      "createdAt": "2026-06-30T00:00:00+00:00",
      "updatedAt": "2026-06-30T00:00:00+00:00",
      "finishedAt": null
    }
  }
}
```

Current behavior:

- Python persists lightweight `tasks` and `task_events` rows in SQLite.
- Task statuses are `queued`, `running`, `blocked`, `succeeded`, `failed`, and `cancelled`. Legacy `done` rows are normalized to `succeeded`.
- `POST /tasks` creates a queued task, records a `created` task event, and submits it to the in-process worker.
- The agent can also manage session tasks through `create_task`, `list_tasks`, and `cancel_task` tools. `create_task` is for explicit queued/asynchronous/tracked work, not ordinary immediate answers or internal planning.
- Successful `create_task` and `cancel_task` tool calls emit `task.updated` runtime events for desktop sync.
- The worker claims runnable queued tasks as `running`, stores heartbeat/claim metadata, records `succeeded` or `failed` terminal events from the backing agent turn, and publishes `task.updated` runtime events for worker status changes.
- Task rows include `attemptCount`, `maxAttempts`, and `nextRunAt`. Retryable worker failures record `retry_scheduled`, move the task back to `queued`, and publish `task.updated` with action `retry_scheduled`; once `attemptCount` reaches `maxAttempts`, the worker records terminal `failed`.
- Task attempt statuses are `running`, `succeeded`, `failed`, `cancelled`, and `abandoned`. `abandoned` is reserved for process-loss cases such as a subprocess worker exiting non-zero before it can finish its attempt; the owning task still moves through the normal retry or terminal failure state machine.
- On runtime startup, the worker reclaims stale `running` tasks with expired heartbeat metadata, records `recovered`, moves them back to `queued`, publishes `task.updated` with action `recovered`, and submits currently runnable queued tasks.
- `POST /tasks/{id}/cancel` marks active tasks as `cancelled`, records one `cancelled` task event, and asks the backing agent turn to cancel when one is running.
- `POST /tasks/{id}/resume` moves a blocked task back to `queued` and submits it again.
- `POST /tasks/{id}/approve` marks a blocked `reviewRequired` task as `succeeded` and syncs any linked plan item to completed.
- Main UI restores active and terminal tasks with `GET /tasks?sessionId={id}&activeOnly=false` and updates from `task.updated` runtime events.
- Plan runs are persisted separately from the latest session plan. `GET /sessions/{id}/plan-runs` returns turn-scoped plan snapshots keyed by `turnId` and `userMessageId` so Main UI can restore archived plan panels under the original user message.
- Python exposes `/runtime/events` as an NDJSON runtime event stream. The TypeScript bridge subscribes to it and broadcasts worker `task.updated` events to every WebSocket client in the same session.
- Clients should still use `GET /tasks/{id}/events` when they need the full persisted task event history.

### memory.context.used

```json
{
  "type": "memory.context.used",
  "payload": {
    "sourceCounts": {
      "conversation_summary": 1,
      "retrieval": 1
    },
	    "sourceCount": 2,
    "coveredThroughMessageId": 12,
    "sessionId": "default",
    "turnId": "turn-uuid",
    "phase": "turn_start",
    "timestamp": "2026-06-21T12:00:00+00:00",
    "sources": [
      {
	        "kind": "retrieval",
	        "sourceId": "42",
	        "contentChars": 120,
	        "reason": "FTS match for current user message",
        "metadata": {
	          "retrievalProvider": "fts_session",
	          "sessionId": "default"
        }
      }
    ]
  }
}
```

Current behavior:

- Python emits this diagnostic after assembling per-turn model context.
- Summary is injected into the temporary system message.
- Structured long-term memory is not injected automatically; it is available through `search_memory_items`.
- Active plan items may be injected into the temporary system message as `<active-plan>` and reported as an `active_plan` source.
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
GET /sessions/{id}/plan
GET /sessions/{id}/plan-runs
GET /tasks
GET /tasks/{id}/events
POST /tasks
POST /tasks/{id}/cancel
POST /tasks/{id}/resume
POST /tasks/{id}/approve
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

`GET /runtime/config` returns the active model provider settings, provider presets, Live2D config, and audio config payloads used by the Main UI configuration center. Model settings include `thinkingEnabled` and `reasoningEffort`; Python persists them to `.env` / `configs/providers.yaml` and translates them through provider-specific reasoning rules at request time.

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

Current task endpoints:

- `POST /agent/cancel`
- `GET /tasks`
- `POST /tasks`
- `GET /tasks/{id}/events`
- `POST /tasks/{id}/cancel`

Planned:

- `POST /agent/message`

These planned items should be documented as current only after they are added to the active shared event types and wired in code.
