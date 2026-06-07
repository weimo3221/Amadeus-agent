# Amadeus Agent

Amadeus Agent is a desktop virtual character agent designed around a Live2D presence, real-time interaction, and a local-first agent runtime.

The first goal is not to build a full AIRI clone. The goal is to create a smaller project with clear boundaries:

- `apps/desktop`: desktop Live2D shell and user interaction surface
- `apps/server`: TypeScript bridge between the desktop and Python runtime
- `packages/amadeus`: Python agent loop, model adapters, memory, tools, skills, Live2D/audio command interfaces
- `packages/live2d-stage`: desktop Live2D rendering adapter

## Design References

- `../airi`: primary reference for desktop Live2D, Electron, character UI, audio, and runtime packaging.
- `../hermes-agent`: reference for tool systems, memory, skills, scheduled tasks, and long-running agent behavior.
- `../deepagents`: reference for long-horizon task planning, sub-agents, filesystem tools, and context management.

## Initial Direction

Build the project in phases:

1. Desktop Live2D window.
2. Text chat with a local agent runtime.
3. Character persona, emotion, expression, and motion mapping.
4. Voice input/output and lipsync.
5. Memory and tools.
6. Proactive desktop assistant behavior.

See [docs/architecture.md](docs/architecture.md) and [docs/roadmap.md](docs/roadmap.md).
