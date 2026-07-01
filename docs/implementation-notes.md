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

- Active provider/model transport logic now lives in `packages/amadeus/model.py` as a first-pass OpenAI-compatible boundary. It reads `configs/providers.yaml` plus environment-expanded provider values, keeps lightweight provider metadata, and raises classified `ModelError` instances for auth, rate limit, server, timeout, context, payload, format, model-not-found, and unknown failures. `packages/amadeus/agent.py` still owns when and why to request tool decisions, summaries, memory review, and final responses.
- `packages/amadeus/skills.py` now owns a first-pass runtime skill catalog. It scans `skills/<category>/<skill-name>/SKILL.md`, parses real YAML frontmatter when available, tolerates Hermes- and skill-creator-style nested metadata such as `platforms`, `compatibility`, and `metadata`, reports bundled resource directories like `scripts/`, `references/`, `assets/`, `agents/`, and `evals/`, and can inject explicitly requested skills into one turn without loading every skill by default. `packages/amadeus/live2d.py` now owns the local Live2D model library boundary while renderer-specific adapter logic still lives in desktop.
- `packages/live2d-stage` is still an intended package boundary; the working Live2D renderer logic currently lives in `apps/desktop/src/renderer/companion/main.ts`, while the larger chat/workbench renderer lives in `apps/desktop/src/renderer/main-ui/main.ts`.

Current progress calibration:

- The active project phase is desktop/runtime stabilization, not MVP construction. The Python-first runtime path, local Live2D model serving, TTS fallback, ToolRuntime foundation, and Memory v2 foundation are already present. The next work should prove and harden real packaged-desktop behavior first.
- Electron E2E is the highest-priority test gap. The first deterministic local-runtime UI path now covers packaged desktop connection, chat submission, streamed assistant output, and visible chat rendering without a live provider. The Live2D path now covers packaged desktop model config/list reads, renderer model loading through a deterministic Live2D test double, model select switching, `/live2d/select`, and harness config persistence. The runtime audio path now covers `audio.tts-ready`, mock runtime audio playback success, playback failure, and the resulting `audio.playback-*` feedback events reaching the bridge. The permission path now covers deterministic `tool.permission.request`, the real Allow / Deny desktop UI, and `tool.permission.response` reaching the bridge for both approval and denial flows.
- Lipsync is still a priority experience gap, but the desktop now does a first real upgrade: runtime can emit `audio.lipsync-cues`, the desktop consumes those cues when present, otherwise runtime audio playback drives `ParamMouthOpenY` from Web Audio amplitude analysis, and the older timed mouth loop remains as fallback for speech synthesis and environments where media-element analysis is unavailable. `packages/amadeus/audio.py` now prefers provider-native lipsync cue payloads when a TTS provider returns them, otherwise plans runtime cues from a text-driven phoneme/viseme sequence and uses local cached `wav` envelope data only as a timing/intensity modulator when available. Future work should improve non-Latin phoneme mapping and broaden provider cue-schema compatibility.
- `apps/server` should keep shrinking toward a transport bridge. It now proxies `/live2d/*`, forwards `tool.permission.response`, forwards runtime feedback, and relays turns, but it should not regain agent-loop, tool-loop, memory, model-library, or provider-decision ownership.
- ToolRuntime should be treated as mostly implemented and entering hardening mode. Do not start a second tool execution framework; extend `amadeus.tool_runtime` when new reliability needs appear.
- Memory v2 should be treated as core-implemented and entering consolidation mode. A first-pass `ContextAssembler` now owns API-call-time summary, structured memory, and FTS retrieval injection plus `memory.context.used` diagnostics; the runtime retains the most recent diagnostics per session in an in-memory ring buffer for developer inspection. The next memory work should focus on summary/profile policy, review quality, overflow behavior, and diagnostics endpoints rather than basic storage primitives.
- Runtime diagnostics now have two layers: legacy `GET /health` for compatibility and structured `GET /runtime/health` for local health checks across runtime, model config, memory DB, tools, Live2D, audio, and effective runtime config. Keep this endpoint local and deterministic; do not make it call external model or TTS providers.
- The first runtime harness slice is in place: `packages/amadeus/harness` loads `configs/harnesses.yaml`, and the Live2D harness maps `assistant.state` into `character.behavior`. Continue maturing the model boundary only as needed for additional providers or richer provider-specific response handling.
- The first practical TTS loop is in place: `packages/amadeus/audio.py` can auto-select GPT-SoVITS when configured, otherwise use macOS `say`/`afconvert` as a local TTS provider, cache generated wav files under the local audio library, and return `audio.tts-ready` through the existing runtime path.
- Local Live2D model storage is in place: `models/live2d` stores switchable local models, `configs/harnesses.yaml` selects the active model, and `packages/amadeus/live2d.py` now owns model resolution, model listing, manifest reads, and `/live2d/select` persistence. `apps/server` keeps the desktop-facing `8788` origin by proxying `/live2d/*` to the Python runtime and rewriting model URLs back to the bridge origin. The default model is now local `hiyori-free`.
- First-pass desktop feedback is in place end to end: each renderer sends `desktop.capabilities` after connection, Companion sends updated capabilities after model load, and both `clientId` / `surface` metadata and audio playback feedback are forwarded to Python `POST /runtime/feedback`; Python `HarnessFeedbackPolicy` stores per-client capabilities, aggregate session capabilities, audio playback state, and recent feedback events for harness policy.
- Live2D now consumes playback feedback at the harness layer: `audio.playback-started` maps to a talking behavior, `audio.playback-ended` maps to idle, and `audio.playback-error` maps to a confused/failure behavior. The bridge forwards these Python-returned `character.behavior` events back to the desktop socket. The desktop still keeps its immediate local mouth loop as a fallback and low-latency response.
- The desktop side now prefers runtime-provided `audio.lipsync-cues` for runtime audio, otherwise samples the playing `HTMLAudioElement` through Web Audio `AnalyserNode` and maps waveform energy to `ParamMouthOpenY`. The Python audio library now resolves actual `wav` duration where possible, normalizes provider-native `lipsyncCues` / `visemes` / `phonemes` payloads when present, otherwise builds phoneme/viseme cue sequences from assistant text, and can scale those fallback cues with local waveform envelope data. The next large architectural gaps are richer audio harness decisions, richer Live2D commands, better non-Latin phoneme mapping, and later skill management / orchestration rather than basic skill loading.

Live2D and audio should be treated as installable harnesses. They can contribute prompt fragments and observe runtime events, but the actual rendering and playback stay in the desktop adapter.

## Tool Runtime Boundary

The Python runtime now separates concrete tool implementations from runtime tool policy:

- `amadeus.tools`: public tool registry entrypoint, concrete local tool handlers, and their default `ToolSpec` metadata.
- `amadeus.tool_runtime.registry`: effective registry construction, `configs/tools.yaml` overlays, enabled schema selection, permission-state projection, structured `ToolContext` / `ToolResult`, turn/tool-call and permission metadata propagation, session workspace epoch propagation, duration/failure metadata, first-pass timeout/cancellation handling, result preview/compression for model context, per-tool model-output policies, and handler dispatch.
- `amadeus.tool_runtime.audit`: tool audit events plus SQLite persistence, metadata payloads, and filtered query APIs for started/finished/denied/blocked/failed decisions.
- `amadeus.tool_runtime.guardrails`: per-turn guardrails for repeated failed calls, repeated completed calls, and semantic no-progress patterns such as empty/same searches, repeated read windows, repeated patch failures, and repeated write failures. File-observing signatures include the session `workspace_epoch` so successful workspace edits invalidate stale file read/search no-progress counts.
- `amadeus.agent`: conversation loop, permission requests, event streaming, memory writes, and coordination with the tool runtime.
- `amadeus.memory_safety`: pre-persistence safety checks for memory review candidates, currently blocking secret-like content, temporary debug/run state, uncertain claims, overly specific local/cache/generated paths, and obvious `user` / `agent` / `project` scope mismatches before anything reaches the human review queue.
- `configs/runtime.yaml`: runtime memory/context defaults for token-budget compaction, context assembler budgets, context diagnostics retention, summary windows/cooldowns, and memory review limits. Environment variables are still allowed as deployment overrides, and `POST /runtime/config/reload` reapplies the YAML-backed effective config without restarting. Recent in-memory context diagnostics are queryable with `GET /memory/context/diagnostics`.
- `packages/amadeus/tools.ts`: TypeScript bridge types and Python tool HTTP clients only. It intentionally does not mirror concrete tool handlers or schemas; server diagnostics should call Python `/tools/list`.

Keep future tool hardening inside `tool_runtime` unless it needs model context or desktop events. The next additions should be additional per-tool result policies for new high-volume tools, richer diagnostics UI surfaces on top of `GET /tools/audit` if needed, and continued tuning of semantic no-progress policies as new tools land. Live2D and audio harnesses may register optional tools later, but they should not be implemented as ad hoc branches in the agent loop.

## Skills V1 Boundary

The first runtime skill slice is intentionally narrow and modeled after the useful parts of Hermes rather than its full ecosystem:

- Skills live under `skills/<category>/<skill-name>/SKILL.md`.
- `SKILL.md` should declare `name` and `description`. `preferred_tools` and `allowed_tools` are optional, and broader frontmatter like `platforms`, `compatibility`, and nested `metadata` is accepted for compatibility with more general skill packs and skill-creator output.
- Python exposes `GET /skills/list` and `GET /skills/view`.
- The tool registry exposes read-only `skills_list` and `skill_view`.
- The runtime system prompt now includes an always-on `<available_skills>` catalog, following the Hermes-style progressive disclosure path: the model should inspect the catalog and call `skill_view(name)` before relying on a relevant installed skill.
- `POST /agent/turn` still accepts an optional `skills: string[]` field, but those are now injected as `<suggested-skills>` hints rather than mandatory full skill instructions.
- When `skill_view(name)` succeeds during a turn, Python appends that skill's full instructions as a turn-local `<active-skills source="skill_view">` block for the rest of that turn.
- Skill activation is now observable through streamed `skill.started` / `skill.finished` events, mirroring the lighter-weight `tool.started` / `tool.finished` desktop status model without introducing a separate persisted audit system.
- `apps/server` now proxies read-only `/skills/list` and `/skills/view` requests to Python so the desktop can stay on the bridge origin.
- The desktop renderer now exposes a refreshable multi-select skill checklist with local search/filtering, shows only a short inline summary for the active skill, persists selected skill identifiers plus the last active preview in local storage, and includes the selected skill identifiers on each `user.message` turn payload.

This is enough to establish a real skill boundary without taking on bundles, marketplace sync, subagent orchestration, or skill editing flows yet.

`workspace_epoch` is maintained by `AgentRuntime` per session. It is a monotonic runtime counter, not a content hash or filesystem scan. It starts at `0`, is passed into `ToolContext`, guardrail signatures, and `tool.audit` metadata, and advances after `patch` or `write_file` succeeds with `changed: true`. This lets the same `read_file` window or `search_files` query be blocked as no-progress within one epoch, then become allowed again after a real workspace mutation. Future shell/external mutation tools should advance the counter conservatively on successful mutation.

### Tool Inventory And Extension Path

Current active Python tools:

- `get_current_time`: `allow`; returns formatted current time for an IANA timezone.
- `roll_dice`: `ask`; rolls bounded dice counts/sides and returns rolls plus total.
- `read_memory`: `allow`; reads current-role stable Markdown memory from `data/roles/<roleId>/memory/MEMORY.md` or `data/roles/<roleId>/memory/USER.md`.
- `update_memory`: `ask`; performs controlled `add` / `replace` / `remove` updates to current-role stable Markdown memory, with exact-match replacement and size limits.
- `update_current_role_identity`: `ask`; updates the current session role name and/or `data/roles/<roleId>/SOUL.md` after explicit user approval.
- MCP bridge first slice: when `tools.mcp.enabled` is true in `configs/tools.yaml`, `ToolRegistry` discovers configured HTTP JSON-RPC MCP servers via `tools/list`, exposes each remote tool as `mcp__<server>__<tool>`, and executes it through `tools/call` while reusing normal permission, timeout, cancellation, result compaction, and audit paths.
- Main UI now exposes a first MCP management surface. The MCP tab edits `tools.mcp.enabled`, default permission, and HTTP JSON-RPC server entries, can test `tools/list` discovery for one server, saves through Python `POST /tools/config`, and rebuilds the Python `ToolRegistry` immediately so server diagnostics and model tool schemas refresh without a manual restart. `scripts/dev_mcp_server.py` provides a tiny local HTTP JSON-RPC MCP server with `echo` and `project_info` tools for manual verification. This is still HTTP JSON-RPC only; stdio/SSE server lifecycle management remains future work.
- `search_memory`: `allow`; searches prior SQLite conversation memory through an FTS-backed index, scoped to the current session by default, with a per-tool model-output policy for bounded snippets. The context assembler also prefetches a small sanitized FTS result set each turn and injects it as API-only `<memory-context>` on the current user message.
- `search_memory_items`: `allow`; searches durable structured `memory_items` facts by optional scope/query, with a per-tool model-output policy for bounded fact metadata.
- `memory_add`: `ask`; writes one durable structured memory fact after user approval, limited to `user` / `agent` / `project` scope, with duplicate detection and source-session metadata.
- `memory_replace`: `ask`; replaces one active durable structured memory fact after user approval.
- `memory_forget`: `ask`; deletes one active durable structured memory fact after user approval.
- Memory review candidates are stored separately from durable memory. `POST /memory/review/run` asks the provider to propose candidates from recent messages, candidates are exposed through `GET/POST /memory/review/candidates`, accepted through `POST /memory/review/accept`, and rejected through `POST /memory/review/reject`; only acceptance writes to `memory_items`. Automatic post-turn review is threshold/cooldown gated and still only creates pending candidates. Rejected candidates suppress identical future suggestions.
- The desktop review UI uses WebSocket events rather than talking to the Python sidecar directly: `memory.review.list`, `memory.review.run`, `memory.review.accept`, and `memory.review.reject` are handled by `apps/server`, which proxies the Python memory review APIs and returns `memory.review.candidates` / `memory.review.updated`.
- `search_files`: `allow`; searches workspace-relative filenames and/or small text file contents with `target: all | files | content`, path containment, skipped generated directories, result caps, and a per-tool model-output policy.
- `read_file`: `allow`; reads an explicit line-numbered window from a workspace-relative UTF-8 text file with path containment, file type/size limits, `startLine` / `lineLimit`, `totalLines`, `hasMore`, and a visible character cap. It intentionally avoids hidden runtime compression. Images, PDFs, binaries, and unknown extensions return structured `kind/supported/hint` metadata instead of being decoded.
- `patch`: `ask`; applies a single-file UTF-8 text replacement with workspace containment, generated-directory denylist, file size limits, unique `oldText` matching by default, optional `replaceAll`, and unified diff output.
- `write_file`: `ask`; creates or fully overwrites workspace-relative UTF-8 text files with workspace containment, generated-directory denylist, text-extension checks, size limits, explicit `overwrite=true` for replacement, parent directory creation inside the workspace, and unified diff output.

`search_files` is the only built-in project file search tool exposed by the Python registry. The old `local_file_search` alias has been removed to keep built-in tool selection unambiguous. MCP tools are externally supplied and use the `mcp__<server>__<tool>` namespace.

To add a simple tool, implement a JSON-serializable handler in a focused module under `packages/amadeus/tools/`, define its `ToolSpec` next to the handler, register that spec from `packages/amadeus/tools/__init__.py`, add the effective config entry in `configs/tools.yaml`, and cover it with focused ToolRuntime tests. Use `handler(args, context)` when the tool should observe cancellation or session/cwd metadata. Keep risky actions as `ask`, constrain filesystem/network behavior explicitly, and add a per-tool result policy in `tool_runtime/registry.py` when outputs can become large.

For external tools, prefer MCP config over new built-ins when the capability naturally belongs to another local service. The first supported transport is HTTP JSON-RPC with `tools/list` and `tools/call`; stdio/process lifecycle management is still future work.

Task worker execution is split at the `TaskRunner` boundary. `TaskWorker` owns task claim/retry/recovery/cancel/event semantics, while `InProcessTaskRunner` owns the current thread-pool submission mechanism. Future process-backed runners should implement the same `submit(task_id, run_task)` and `shutdown(wait=...)` contract without changing task state transitions.

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
- Keep Live2D model bundles under `models/live2d`; new models should be added through a manifest/model directory plus `configs/harnesses.yaml` selection rather than hardcoded renderer URLs.

## Harness Config Direction

The first harness implementation and `configs/harnesses.yaml` are already active. The shape is:

```yaml
harnesses:
  live2d:
    enabled: true
    adapter: desktop-live2d
    model:
      id: default
      path: models/live2d/default/default.model3.json
    audioPlaybackBehaviors:
      started:
        emotion: neutral
        expression: smile
        motion: talk
        intensity: 0.65
      ended:
        emotion: neutral
        expression: neutral
        motion: idle
        intensity: 0.35
      error:
        emotion: confused
        expression: confused
        motion: shake_head
        intensity: 0.55
  audio:
    enabled: true
    tts:
      provider: none
      fallback: speechSynthesis
    lipsync:
      mode: timed
```

The runtime loads Live2D model selection and playback-state behavior mapping from this config. `audioPlaybackBehaviors` accepts the short keys `started`, `ended`, and `error` as aliases for `audio.playback-started`, `audio.playback-ended`, and `audio.playback-error`. Missing behavior fields fall back to the Python defaults.

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
- `cache/`: generated TTS output from GPT-SoVITS, macOS `say`, or later providers. This directory is runtime cache and is gitignored.

The desktop app plays the `audioUrl` emitted by the runtime when one exists. If no Python TTS provider can generate audio for the requested text, the desktop falls back to `speechSynthesis`. On macOS, the default `auto` TTS config should produce real runtime audio through `say` without requiring an external service.

When runtime audio is played, the desktop reports playback feedback:

```text
audio.playback-started
audio.playback-ended
audio.playback-error
```

This feedback loop is now available in Python through `HarnessFeedbackPolicy` and `GET /runtime/feedback`, and Live2D uses it for playback-state-driven behavior. The desktop now adds amplitude-driven mouth motion for runtime audio, while the main cue path comes from provider-native runtime cues when present or `audio.py` phoneme/viseme planning otherwise; the remaining work is broader provider cue compatibility and a fuller audio harness boundary.

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

Tools should use one of three permission levels. The default product posture is to avoid interrupting the user for low-risk read-only workspace inspection, and to ask only for persistent side effects, external actions, or sensitive-risk operations.

- `allow`: safe to run immediately, including bounded read-only inspection inside the project workspace.
- `ask`: requires explicit user approval because the action mutates state, reaches outside the local workspace, contacts external services, opens apps/URLs, executes scripts, or may expose sensitive data.
- `deny`: unavailable.

Examples:

- current time: `allow`
- searching or reading bounded project text files: `allow`
- patching or writing files: `ask`
- running scripts, shell commands, installers, network actions, opening URLs, or touching workspace-external paths: `ask`
- deleting broad file trees or arbitrary shell execution without a stronger approval UI: `deny`

## Desktop Behavior States

The desktop character should react to runtime state:

- `idle`: default breathing/idle animation
- `listening`: reserved in shared types but not meaningfully used yet in the current flow
- `thinking`: focused expression or thinking motion
- `speaking`: talking motion and lipsync
- `tool-running`: focused/working state
- `error`: confused expression, then return to idle
