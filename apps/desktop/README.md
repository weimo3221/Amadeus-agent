# Desktop App

Electron desktop shell for Amadeus.

## Current Role

`apps/desktop` owns the native Electron surfaces and device-facing behavior. It
does not own the agent loop, memory, model calls, tool execution, runtime skills,
or audio provider selection.

## Surfaces

- Companion: transparent always-on-top Live2D presence with lightweight input,
  transient streaming bubbles, microphone input, runtime audio playback, lipsync,
  global-cursor visibility, pointer/gaze behavior, and permission prompts.
- Main UI: Electron-hosted workbench window that loads the Vue app in
  `apps/desktop-ui-next`.

## Responsibilities

- create and coordinate Companion and Main UI windows
- render Live2D only in Companion
- sample global cursor state for Companion visibility and gaze behavior
- relay user input over the `apps/server` WebSocket bridge
- show inline permission prompts for Python ToolRuntime requests
- play runtime audio and report `audio.playback-*` feedback
- apply runtime `character.behavior` and `audio.lipsync-cues` events
- keep browser/Electron speech synthesis as fallback when runtime audio fails

This app communicates with `apps/server` through the event protocol in
`docs/event-protocol.md`. The bridge then relays runtime work to Python under
`packages/amadeus`.

Runtime UI behavior is covered by `src/renderer/runtime-ui.test.ts`, which runs
without launching Electron or loading Live2D assets. Packaged desktop behavior is
covered by Electron E2E tests under `e2e/`; Main UI chat, skill selection,
permission, and Companion attach flows exercise the packaged Vue renderer.
