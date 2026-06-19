# Desktop App

Electron desktop shell for the Live2D character.

Responsibilities:

- transparent always-on-top window
- Live2D stage rendering
- chat input and streaming output
- pointer/click/drag interaction
- audio playback and lipsync
- local runtime connection

This app should communicate with `apps/server` through the event protocol in `docs/event-protocol.md`.

Runtime UI behavior is covered by `src/renderer/runtime-ui.test.ts`, which runs without launching Electron or loading Live2D assets.
