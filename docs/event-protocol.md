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

### desktop.capabilities

Sent after the desktop adapter initializes enough to describe available device features.

```json
{
  "type": "desktop.capabilities",
  "payload": {
    "live2d": true,
    "audioPlayback": true,
    "speechSynthesis": true,
    "voiceInput": false
  }
}
```

### character.capabilities

Sent after a Live2D model loads.

```json
{
  "type": "character.capabilities",
  "payload": {
    "modelId": "default",
    "expressions": ["default", "smile"],
    "motions": ["Idle", "TapBody"]
  }
}
```

### audio.capabilities

Sent after the desktop audio adapter initializes.

```json
{
  "type": "audio.capabilities",
  "payload": {
    "playback": true,
    "speechSynthesis": true,
    "input": false
  }
}
```

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

### user.voice-chunk

Reserved for push-to-talk or streaming ASR.

```json
{
  "type": "user.voice-chunk",
  "payload": {
    "mimeType": "audio/webm",
    "chunkBase64": "..."
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

### desktop.character.click

```json
{
  "type": "desktop.character.click",
  "payload": {
    "x": 300,
    "y": 500,
    "button": "left"
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

### audio.playback-started

```json
{
  "type": "audio.playback-started",
  "payload": {
    "audioUrl": "http://127.0.0.1:8790/audio/files/cache/reply.wav"
  }
}
```

### audio.playback-ended

```json
{
  "type": "audio.playback-ended",
  "payload": {
    "audioUrl": "http://127.0.0.1:8790/audio/files/cache/reply.wav"
  }
}
```

### audio.playback-error

```json
{
  "type": "audio.playback-error",
  "payload": {
    "audioUrl": "http://127.0.0.1:8790/audio/files/cache/reply.wav",
    "message": "Playback failed"
  }
}
```

## Server to Desktop

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

### character.lipsync

Reserved for runtime-provided lipsync values when desktop playback is not deriving mouth movement locally.

```json
{
  "type": "character.lipsync",
  "payload": {
    "mouthOpen": 0.62,
    "durationMs": 50
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

### tool.permission.request

```json
{
  "type": "tool.permission.request",
  "payload": {
    "requestId": "permission-request-id",
    "toolName": "local_file_search",
    "displayName": "Searching local files",
    "reason": "Allow Amadeus to search local project files?"
  }
}
```

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

If the Python audio runtime cannot provide an audio URL, the desktop may fall back to system `speechSynthesis`.

### audio.tts-fallback

```json
{
  "type": "audio.tts-fallback",
  "payload": {
    "reason": "tts_provider_unavailable:none",
    "fallback": "speechSynthesis"
  }
}
```

### audio.lipsync-cues

```json
{
  "type": "audio.lipsync-cues",
  "payload": {
    "audioUrl": "http://127.0.0.1:8790/audio/files/cache/reply.wav",
    "cues": [
      { "offsetMs": 0, "mouthOpen": 0.1 },
      { "offsetMs": 50, "mouthOpen": 0.7 }
    ]
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
