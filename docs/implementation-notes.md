# Implementation Notes

## Current Build Direction

The project started as a TypeScript monorepo for fast Electron iteration, but the runtime direction is now Python-first:

- Electron + Vite for `apps/desktop`
- Node.js WebSocket bridge for `apps/server`
- Python runtime under `packages/amadeus`
- shared event types from `packages/amadeus/events.ts`

`apps/desktop` should remain a UI/device adapter. `apps/server` should remain a transport bridge. Agent, memory, model adapters, tools, skills, and audio planning should move into `packages/amadeus` behind narrow HTTP/event APIs.

The Python-first turn path is already in place: `/agent/turn` is implemented as an NDJSON event stream from Python to the TypeScript bridge. The bridge relays each runtime event to desktop and forwards `tool.permission.response` back to Python through `/tools/permission`. The older TypeScript model/tool loop has been removed, so `apps/server` reports a runtime error when Python cannot accept a turn instead of running a second agent loop.

`npm test` now runs Python `unittest` coverage for the Python runtime path and HTTP sidecar handlers, TypeScript tests for Python NDJSON relay, permission forwarding, server-level WebSocket relay, and a desktop renderer harness for runtime UI behavior. `npm run test:e2e` builds the desktop app and runs an Electron startup smoke that verifies the packaged main process can load the renderer. Keep these tests focused on deterministic behavior that does not require a live model provider. The main remaining UI gap is deeper Electron end-to-end coverage around Live2D loading and real user/runtime interactions.

Current implementation note:

- Active provider/model call logic still lives inline in `packages/amadeus/agent.py`.
- `packages/amadeus/model.py`, `skills.py`, and `live2d.py` are still future boundaries rather than mature active modules.
- `packages/live2d-stage` is still an intended package boundary; the working Live2D renderer logic currently lives in `apps/desktop/src/renderer/main.ts`.

Live2D and audio should be treated as installable harnesses. They can contribute prompt fragments and observe runtime events, but the actual rendering and playback stay in the desktop adapter.

## Tool Runtime Boundary

The Python runtime now separates concrete tool implementations from runtime tool policy:

- `amadeus.tools`: public tool registry entrypoint, concrete local tool handlers, and their default `ToolSpec` metadata.
- `amadeus.tool_runtime.registry`: effective registry construction, `configs/tools.yaml` overlays, enabled schema selection, permission-state projection, structured `ToolContext` / `ToolResult`, turn/tool-call and permission metadata propagation, duration/failure metadata, first-pass timeout/cancellation handling, result preview/compression for model context, per-tool model-output policies, and handler dispatch.
- `amadeus.tool_runtime.audit`: tool audit events plus SQLite persistence and filtered query APIs for started/finished/denied/blocked/failed decisions.
- `amadeus.tool_runtime.guardrails`: per-turn guardrails for repeated failed calls, repeated completed calls, and semantic no-progress patterns such as empty/same searches, repeated read windows, repeated patch failures, and repeated write failures.
- `amadeus.agent`: conversation loop, permission requests, event streaming, memory writes, and coordination with the tool runtime.
- `configs/runtime.yaml`: runtime memory/context defaults for token-budget compaction, summary windows/cooldowns, and memory review limits. Environment variables are still allowed as deployment overrides.
- `packages/amadeus/tools.ts`: TypeScript bridge types and Python tool HTTP clients only. It intentionally does not mirror concrete tool handlers or schemas; server diagnostics should call Python `/tools/list`.

Keep future tool hardening inside `tool_runtime` unless it needs model context or desktop events. The next additions should be additional per-tool result policies for new high-volume tools, richer diagnostics UI surfaces on top of `GET /tools/audit` if needed, and continued tuning of semantic no-progress policies as new tools land. Live2D and audio harnesses may register optional tools later, but they should not be implemented as ad hoc branches in the agent loop.

### Tool Inventory And Extension Path

Current active Python tools:

- `get_current_time`: `allow`; returns formatted current time for an IANA timezone.
- `roll_dice`: `ask`; rolls bounded dice counts/sides and returns rolls plus total.
- `read_memory`: `allow`; reads stable Markdown memory from `data/memory/MEMORY.md` or `data/memory/USER.md`.
- `update_memory`: `ask`; performs controlled `add` / `replace` / `remove` updates to stable Markdown memory, with exact-match replacement and size limits.
- `search_memory`: `allow`; searches prior SQLite conversation memory through an FTS-backed index, scoped to the current session by default, with a per-tool model-output policy for bounded snippets. The agent also prefetches a small sanitized FTS result set each turn and injects it as API-only `<memory-context>` on the current user message.
- `search_memory_items`: `allow`; searches durable structured `memory_items` facts by optional scope/query, with a per-tool model-output policy for bounded fact metadata.
- `memory_add`: `ask`; writes one durable structured memory fact after user approval, limited to `user` / `agent` / `project` scope, with duplicate detection and source-session metadata.
- `memory_replace`: `ask`; replaces one active durable structured memory fact after user approval.
- `memory_forget`: `ask`; deletes one active durable structured memory fact after user approval.
- Memory review candidates are stored separately from durable memory. `POST /memory/review/run` asks the provider to propose candidates from recent messages, candidates are exposed through `GET/POST /memory/review/candidates`, accepted through `POST /memory/review/accept`, and rejected through `POST /memory/review/reject`; only acceptance writes to `memory_items`. Automatic post-turn review is threshold/cooldown gated and still only creates pending candidates. Rejected candidates suppress identical future suggestions.
- The desktop review UI uses WebSocket events rather than talking to the Python sidecar directly: `memory.review.list`, `memory.review.run`, `memory.review.accept`, and `memory.review.reject` are handled by `apps/server`, which proxies the Python memory review APIs and returns `memory.review.candidates` / `memory.review.updated`.
- `search_files`: `ask`; searches workspace-relative filenames and/or small text file contents with `target: all | files | content`, path containment, skipped generated directories, result caps, and a per-tool model-output policy.
- `read_file`: `ask`; reads an explicit line-numbered window from a workspace-relative UTF-8 text file with path containment, file type/size limits, `startLine` / `lineLimit`, `totalLines`, `hasMore`, and a visible character cap. It intentionally avoids hidden runtime compression. Images, PDFs, binaries, and unknown extensions return structured `kind/supported/hint` metadata instead of being decoded.
- `patch`: `ask`; applies a single-file UTF-8 text replacement with workspace containment, generated-directory denylist, file size limits, unique `oldText` matching by default, optional `replaceAll`, and unified diff output.
- `write_file`: `ask`; creates or fully overwrites workspace-relative UTF-8 text files with workspace containment, generated-directory denylist, text-extension checks, size limits, explicit `overwrite=true` for replacement, parent directory creation inside the workspace, and unified diff output.

`local_file_search` remains registered as a disabled compatibility alias for older tool calls. New prompts, schemas, and docs should prefer `search_files`.

To add a simple tool, implement a JSON-serializable handler in a focused module under `packages/amadeus/tools/`, define its `ToolSpec` next to the handler, register that spec from `packages/amadeus/tools/__init__.py`, add the effective config entry in `configs/tools.yaml`, and cover it with focused ToolRuntime tests. Use `handler(args, context)` when the tool should observe cancellation or session/cwd metadata. Keep risky actions as `ask`, constrain filesystem/network behavior explicitly, and add a per-tool result policy in `tool_runtime/registry.py` when outputs can become large.

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

- Use WebSocket for desktop/server events.
- Use an OpenAI-compatible API shape for the first LLM provider.
- Use SQLite for memory.
- Keep tool execution behind explicit `allow` / `ask` / `deny` policy.
- Keep desktop voice playback as the current practical fallback while the Python audio interface matures.
- Keep Python as the preferred runtime owner and TypeScript as the bridge owner.
- Add new Live2D/audio behavior through harnesses, not through ad hoc server conditionals.
- Add a local Live2D bundle later; the current MVP still loads a remote test model.

## Harness Config Direction

Add a `configs/harnesses.yaml` file when the first harness implementation lands:

```yaml
harnesses:
  live2d:
    enabled: true
    adapter: desktop-live2d
    model:
      id: default
      path: models/live2d/default/default.model3.json
  audio:
    enabled: true
    tts:
      provider: none
      fallback: speechSynthesis
    lipsync:
      mode: timed
```

The runtime should load harnesses from this config and expose their effective state in `server.hello` or a later diagnostics event. Desktop-side code should report actual capabilities after model/audio initialization so the runtime can choose behavior that the adapter can execute.

## Audio Layout

Current fallback voice output uses Electron/browser `speechSynthesis`, so available voices depend on the OS.

The Python audio module owns the long-term audio interface. Local audio assets should live under:

```text
packages/amadeus/assets/audio/
  voices/
  sfx/
  cache/
```

- `voices/`: fixed character voice clips, such as greetings or short reactions. These do not provide arbitrary text speech.
- `sfx/`: UI and character sound effects.
- `cache/`: generated TTS output when a real TTS engine is added.

The desktop app should play the `audioUrl` emitted by the runtime when one exists. If no Python TTS provider can generate audio for the requested text, the desktop falls back to `speechSynthesis`.

When runtime audio is played in the future, the desktop should report playback feedback:

```text
audio.playback-started
audio.playback-ended
audio.playback-error
```

This later feedback loop would let the audio and Live2D harnesses coordinate real speaking state and lipsync instead of relying only on a timed mouth loop.

Fixed wav/mp3 files are useful for sound effects and canned reactions, but they are not a replacement for TTS. Arbitrary assistant replies require a provider such as GPT-SoVITS, Bert-VITS2, ChatTTS, Piper, OpenAI TTS, Azure Speech, or another engine behind `amadeus/audio.py`.

## GPT-SoVITS Test Setup

The first local TTS provider candidate is GPT-SoVITS:

```text
D:\OtherProject\LearningLLM\GPT-SoVITS
```

Vivian fine-tuned model assets currently live outside the app repo:

```text
D:\OtherProject\LearningLLM\dataset\薇薇安_zh
D:\OtherProject\LearningLLM\dataset\薇薇安_en
```

Each language has one GPT checkpoint, one SoVITS checkpoint, and one reference wav. These are not enough by themselves: GPT-SoVITS also needs pretrained base assets under `GPT_SoVITS/pretrained_models`, including BERT, HuBERT, and the matching base GPT/SoVITS weights for the configured version.

On this Windows machine, `pwsh` is not available. Use Windows PowerShell to run the installer:

```powershell
cd D:\OtherProject\LearningLLM\GPT-SoVITS
powershell -ExecutionPolicy Bypass -File .\install.ps1 -Device CU126 -Source ModelScope
```

After the base models are present, start the API:

```powershell
python api_v2.py -a 127.0.0.1 -p 9880 -c GPT_SoVITS/configs/tts_infer.yaml
```

The Amadeus provider should only be wired after standalone GPT-SoVITS tests can generate both Chinese and English wav files from the Vivian weights.

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
- `listening`: reserved in shared types but not meaningfully used yet in the current flow
- `thinking`: focused expression or thinking motion
- `speaking`: talking motion and lipsync
- `tool-running`: focused/working state
- `error`: confused expression, then return to idle
