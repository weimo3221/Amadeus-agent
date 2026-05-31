# Implementation Notes

## First Build Recommendation

Use a TypeScript monorepo first:

- Electron + Vite for `apps/desktop`
- Node.js WebSocket server for `apps/server`
- shared event types from `packages/shared`

This keeps the Live2D UI and local runtime easy to run on Windows. Python can still be added later for specific agent frameworks, but it should enter as a worker process with a narrow API.

## AIRI Code to Study First

When implementation starts, inspect these paths:

- `../airi/apps/stage-tamagotchi/src`
- `../airi/apps/stage-tamagotchi/electron.vite.config.ts`
- `../airi/packages/stage-ui-live2d/src`
- `../airi/packages/core-character/src`
- `../airi/packages/core-agent/src`
- `../airi/packages/model-driver-lipsync/src`

Do not copy the whole project. Pull over only the specific patterns needed for the MVP.

## MVP Technical Decisions

- Use one Live2D model in `models/live2d` during development.
- Use WebSocket for desktop/server events.
- Use OpenAI-compatible API shape for the first LLM provider.
- Use SQLite for memory.
- Keep tool execution disabled by default except safe tools such as current time.

## Permission Model

Tools should use one of three permission levels:

- `allow`: safe to run immediately.
- `ask`: requires explicit user approval.
- `deny`: unavailable.

Examples:

- current time: `allow`
- reading selected local folders: `ask`
- deleting files: `deny` until a stronger approval UI exists

## Desktop Behavior States

The desktop character should react to runtime state:

- `idle`: default breathing/idle animation
- `listening`: attentive expression
- `thinking`: focused expression or thinking motion
- `speaking`: talking motion and lipsync
- `tool-running`: focused/working state
- `error`: confused expression, then return to idle
