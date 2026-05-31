# Event Protocol

This file defines the first draft of the desktop/server event protocol.

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

## Desktop to Server

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

### user.voice-start

```json
{
  "type": "user.voice-start",
  "payload": {
    "sampleRate": 48000
  }
}
```

### user.voice-end

```json
{
  "type": "user.voice-end",
  "payload": {}
}
```

### desktop.pointer

```json
{
  "type": "desktop.pointer",
  "payload": {
    "x": 300,
    "y": 500,
    "target": "character",
    "action": "click"
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

## Server to Desktop

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

Allowed states:

- `idle`
- `listening`
- `thinking`
- `speaking`
- `tool-running`
- `error`

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
    "toolName": "local_file_search",
    "displayName": "Searching files"
  }
}
```

### tool.finished

```json
{
  "type": "tool.finished",
  "payload": {
    "toolName": "local_file_search",
    "ok": true
  }
}
```

### audio.tts-ready

```json
{
  "type": "audio.tts-ready",
  "payload": {
    "audioUrl": "local://tts/session-1/message-3.wav",
    "durationMs": 3200
  }
}
```

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
