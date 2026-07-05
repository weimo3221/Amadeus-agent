<div align="center">

<img src="https://copilot-cn.bytedance.net/api/ide/v1/text_to_image?prompt=anime%20style%20desktop%20virtual%20companion%2C%20cute%20Live2D%20girl%20mascot%20floating%20over%20a%20sleek%20modern%20desktop%2C%20soft%20pastel%20gradient%20background%2C%20glassmorphism%20chat%20panels%2C%20futuristic%20local%20AI%20assistant%2C%20clean%20minimal%20UI%2C%20high%20quality%20digital%20illustration%2C%20wide%20cinematic%20banner&image_size=landscape_16_9" alt="Amadeus Agent" width="100%" />

# Amadeus Agent

**A desktop virtual character agent built around a Live2D presence, real-time interaction, and a local-first runtime.**

[![Python](https://img.shields.io/badge/Python-3776AB?style=flat-square&logo=python&logoColor=white)](https://www.python.org/)
[![TypeScript](https://img.shields.io/badge/TypeScript-3178C6?style=flat-square&logo=typescript&logoColor=white)](https://www.typescriptlang.org/)
[![Vue 3](https://img.shields.io/badge/Vue_3-4FC08D?style=flat-square&logo=vuedotjs&logoColor=white)](https://vuejs.org/)
[![Electron](https://img.shields.io/badge/Electron-47848F?style=flat-square&logo=electron&logoColor=white)](https://www.electronjs.org/)
[![Vite](https://img.shields.io/badge/Vite-646CFF?style=flat-square&logo=vite&logoColor=white)](https://vitejs.dev/)
[![Live2D](https://img.shields.io/badge/Live2D-FF6699?style=flat-square&logoColor=white)](https://www.live2d.com/)
[![Status](https://img.shields.io/badge/status-MVP%20working-brightgreen?style=flat-square)](docs/project-status.md)

</div>

---

## Overview

Amadeus Agent is not just a chat window. The character reacts through facial expression, motion, speaking state, idle behavior, contextual actions, tools, memory, and audio. It runs a **Python-first agent runtime** with thin TypeScript/Electron adapters wrapped around it, so long-term memory, tool execution, and provider-specific model logic all live in one place while the desktop stays lightweight.

> The project is in a **Python-first migration stage**: the Python runtime owns the preferred turn path, while the TypeScript bridge shrinks toward being a pure transport layer.

## Highlights

| | Feature | Description |
|---|---|---|
| 🎭 | **Live2D companion** | Transparent, frameless, always-on-top desktop presence with gaze tracking, motion, and expression driven by agent state. |
| 🗣️ | **Voice in & out** | Local `faster-whisper` ASR for microphone input and `auto`-selected TTS (GPT-SoVITS or macOS `say`) with hybrid lipsync. |
| 🧠 | **Memory v2** | SQLite-backed history, FTS retrieval, structured facts, human-reviewed memory promotion, and API-call-time context assembly. |
| 🛠️ | **ToolRuntime** | Formal registry with `allow` / `ask` / `deny` permissions, audit trail, timeouts, cancellation, and repeated-call guardrails. |
| 💭 | **Reasoning-aware** | DeepSeek V4 thinking mode handled through a provider-aware reasoning layer that replays `reasoning_content` only where required. |
| ⏰ | **Proactive agent** | Scheduled companion messages, persistent session todos, and in-process task workers with retry and stale-run recovery. |
| 🔌 | **MCP bridge** | Discover and execute remote HTTP JSON-RPC MCP tools with full permission and audit coverage. |

## Architecture

The desktop layer stays thin. It never owns long-term memory, tool execution, provider-specific LLM logic, or agent planning.

```mermaid
flowchart TD
    U([User<br/>text · voice · cursor]) --> D

    subgraph Desktop["apps/desktop · Electron"]
        D[Companion<br/>Live2D · voice · bubbles]
        M[Main UI<br/>chat · tasks · config]
    end

    D <-->|WebSocket / IPC| B
    M <-->|WebSocket / HTTP| B

    subgraph Bridge["apps/server · TypeScript bridge"]
        B[Session rooms<br/>surface-aware broadcast<br/>runtime HTTP proxy]
    end

    B <-->|HTTP / JSON| P

    subgraph Runtime["packages/amadeus · Python runtime"]
        P[agent loop]
        P --> MEM[(memory · SQLite)]
        P --> TR[tool_runtime]
        P --> MD[model provider]
        P --> AU[audio · ASR/TTS]
        P --> L2[live2d library]
        P --> SK[skills]
        P --> SC[scheduling]
    end
```

### Modules

| Path | Role |
|---|---|
| [`apps/desktop`](apps/desktop) | Electron shell with **Companion** (Live2D, lightweight chat, voice) and **Main UI** window orchestration. |
| [`apps/desktop-ui-next`](apps/desktop-ui-next) | Production Main UI workspace — Vue 3 + Vite + Tailwind v4 for chat, sessions, tasks, timed messages, skills, memory, and config. |
| [`apps/server`](apps/server) | Thin TypeScript bridge: WebSocket fanout plus Live2D / audio / runtime HTTP proxying. |
| [`packages/amadeus`](packages/amadeus) | The agent brain: turn path, provider boundary, Memory v2, ToolRuntime, scheduling, todos, ASR/TTS/Live2D helpers, and the runtime HTTP API. |
| [`packages/live2d-stage`](packages/live2d-stage) | Intended Live2D rendering adapter boundary (not yet the active implementation). |

## Runtime flow

```mermaid
sequenceDiagram
    participant D as Desktop
    participant S as apps/server
    participant P as Python runtime
    D->>S: user.message (surface, clientId, sessionId)
    S->>P: POST /agent/turn
    P->>P: load history · assemble memory context
    P->>P: bounded tool-call loop · execute tools
    P-->>S: stream assistant.delta / tool.* / character.behavior
    P-->>S: audio.tts-ready (optional)
    S-->>D: relay NDJSON events to session clients
    D->>D: update chat · Live2D · audio · permissions
```

**Voice input path:** Companion records mic audio with `MediaRecorder` → uploads to bridge `POST /audio/transcribe` → forwarded to Python `POST /audio/transcribe` → `asr.default: auto` picks local `faster-whisper` when available → the transcript re-enters the normal `user.message` path.

## Quick start

**Prerequisites:** Node.js + npm, Python 3, and an API key for at least one provider (DeepSeek by default).

```bash
# 1. Install dependencies
npm install
python -m pip install -r requirements.txt

# 2. Configure your provider
cp .env.example .env
#   then set DEEPSEEK_API_KEY (or another provider key) in .env

# 3. Launch the full supervised stack
npm run dev
```

`npm run dev` starts the Python runtime, waits for `/runtime/health`, starts the TypeScript bridge, waits for `/health`, then launches the Electron desktop. If any required child process exits, the supervisor terminates the rest so the stack never silently half-runs.

> The first transcription may download the selected Whisper model. Tune it with `FASTER_WHISPER_MODEL_SIZE`, `FASTER_WHISPER_DEVICE`, `FASTER_WHISPER_COMPUTE_TYPE`, `FASTER_WHISPER_LANGUAGE`, or `FASTER_WHISPER_DOWNLOAD_ROOT`.

### Useful variants

```bash
npm run dev:stack -- --no-desktop      # run runtime + bridge without Electron
npm run dev:stack -- --reuse-existing   # attach to already-running local services
npm run dev:legacy                      # raw concurrent startup (no supervisor)
```

### Development commands

```bash
npm test          # Python unittest + server + desktop tests
npm run test:e2e  # Electron end-to-end smoke and flows
npm run typecheck # typecheck all TS/Vue workspaces + Python
```

## Local runtime

| Service | Address |
|---|---|
| TypeScript bridge | `http://127.0.0.1:8788` · `ws://127.0.0.1:8788/ws` |
| Python runtime | `http://127.0.0.1:8790` |

Default provider environment (stored only in local `.env`, git-ignored):

```env
AMADEUS_LLM_PROVIDER=deepseek
DEEPSEEK_API_KEY=your-key-here
DEEPSEEK_MODEL=deepseek-v4-pro
DEEPSEEK_THINKING_ENABLED=true
DEEPSEEK_REASONING_EFFORT=high
VITE_AGENT_WS_URL=ws://127.0.0.1:8788/ws
```

## Configuration

Runtime behavior is driven by YAML under [`configs/`](configs), loaded at startup and reloadable via `POST /runtime/config/reload`:

| File | Controls |
|---|---|
| [`providers.yaml`](configs/providers.yaml) | OpenAI-compatible model providers and defaults. |
| [`runtime.yaml`](configs/runtime.yaml) | Context/memory budgets, summary compaction, review limits, Companion Live2D fit. |
| [`tools.yaml`](configs/tools.yaml) | Effective tool enabled/permission state and MCP servers. |
| [`harnesses.yaml`](configs/harnesses.yaml) | Active Live2D model and playback-state behavior mapping. |
| [`character.yaml`](configs/character.yaml) | Character persona defaults. |

## Developer diagnostics

The Python runtime exposes structured, local observability surfaces:

- `GET /runtime/health` — health for runtime, model config, memory DB, tools, Live2D, audio, and effective config.
- `GET /memory/context/diagnostics?sessionId=default&limit=10` — recent per-session Memory v2 context assembly decisions.
- `GET /scheduled-jobs?sessionId=companion:default&activeOnly=false` — scheduled companion messages including terminal states.
- `GET /todos?sessionId=companion:default&activeOnly=true` — persistent session todo items.
- `POST /audio/transcribe?format=webm` — transcribe microphone input through the configured ASR provider.
- `GET /runtime/config` — model provider settings including `thinkingEnabled` and `reasoningEffort`.

## Documentation

| Doc | Purpose |
|---|---|
| [project-status.md](docs/project-status.md) | Live source of truth for what's implemented now. |
| [architecture.md](docs/architecture.md) | Current and target architecture. |
| [roadmap.md](docs/roadmap.md) | Forward-looking plan. |
| [event-protocol.md](docs/event-protocol.md) | Runtime event contract between desktop, bridge, and Python. |
| [implementation-notes.md](docs/implementation-notes.md) | Notes on key implementation decisions. |
| [agent-maturity-upgrade-plan.md](docs/agent-maturity-upgrade-plan.md) | Long-term maturity plan. |

## Design references

- `../airi` — primary reference for desktop Live2D, Electron, character UI, audio, and runtime packaging.
- `../hermes-agent` — reference for tool systems, memory, skills, scheduled tasks, and long-running agent behavior.
- `../deepagents` — reference for long-horizon task planning, sub-agents, filesystem tools, and context management.

---

<div align="center">
<sub>Built with a Live2D body, a Python brain, and a local-first heart.</sub>
</div>
