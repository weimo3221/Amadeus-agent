# Implementation Notes

## Current Build Direction

The project started as a TypeScript monorepo for fast Electron iteration, but the runtime direction is now Python-first:

- Electron + Vite for `apps/desktop`
- Node.js WebSocket bridge for `apps/server`
- Python runtime under `packages/amadeus`
- shared event types from `packages/amadeus/events.ts`

`apps/desktop` should remain a UI/device adapter. `apps/server` should remain a transport bridge. Agent, memory, model adapters, tools, skills, and audio planning should move into `packages/amadeus` behind narrow HTTP/event APIs.

The Python-first turn path is already in place: `/agent/turn` is implemented as an NDJSON event stream from Python to the TypeScript bridge. The bridge relays each runtime event to desktop and forwards `tool.permission.response` back to Python through `/tools/permission`. The older TypeScript model/tool loop has been removed, so `apps/server` reports a runtime error when Python cannot accept a turn instead of running a second agent loop.

`npm test` now runs Python `unittest` coverage for the Python runtime path and HTTP sidecar handlers, TypeScript tests for Python NDJSON relay, permission forwarding, server-level WebSocket relay, and a desktop renderer harness for runtime UI behavior. `npm run test:e2e` builds both `apps/desktop-ui-next` and the Electron app, then verifies the packaged Vue Main UI chat, multi-skill selection, permission prompt, Companion session attach, Live2D model switching, runtime audio feedback, and Companion hover flows. One packaged workflow starts a deterministic OpenAI-compatible fixture plus the real Python runtime, Node bridge, and temporary SQLite state to verify persisted chat, session switching/reload, and task review approval without external credentials.

Current implementation note:

- Active provider/model transport logic now lives in `packages/amadeus/model.py` as a first-pass OpenAI-compatible boundary. It reads `configs/providers.yaml` plus environment-expanded provider values, keeps lightweight provider metadata, and raises classified `ModelError` instances for auth, rate limit, server, timeout, context, payload, format, model-not-found, and unknown failures. `packages/amadeus/agent.py` still owns when and why to request tool decisions, summaries, memory review, and final responses.
- `packages/amadeus/prompting.py` now splits system prompt construction into stable runtime rules and contextual workspace/tool/memory/skill sections. Workspace instruction loading checks `.amadeus.md` / `AMADEUS.md` before `AGENT.md`, then Claude and Cursor-style files, strips YAML frontmatter, blocks obvious prompt-injection phrases, sanitizes context-like markup, and scales truncation against the configured context budget.
- `packages/amadeus/context.py` now consumes a runtime memory manager: summaries stay in system context, while more volatile reference blocks such as active plans, active todos, task state, recent task results, SQLite FTS retrievals, and external provider snippets are attached to the current user message and excluded from persisted conversation history. Structured long-term memory is no longer injected automatically; it is read through `search_memory_items`. Memory query tokenization uses `jieba` plus bounded CJK n-gram fallback; message FTS indexes token-expanded content but returns original transcript content.
- `packages/amadeus/memory_provider.py` now defines the runtime memory provider layer. Exactly one runtime memory provider is active at a time. With no external provider configured, `mem0_like_runtime` is the default: it keeps the hybrid SQLite/session memory lanes, exposes summaries, FTS snippets, and the default SQLite memory tools; typed long-term memory facts remain indexed for explicit tool recall rather than prefetch injection. `hybrid_runtime` and `builtin_runtime` remain available for compatibility. If an external provider is configured, it replaces the built-in provider for runtime prefetch and memory-tool exposure, so the model sees one memory backend instead of competing local and external tools. Raw session transcripts are not provider outputs and remain accessible only through explicit transcript APIs/tools.
- `packages/amadeus/skills.py` now owns the runtime skill catalog and approved experience-skill creation. It scans `skills/<category>/<skill-name>/SKILL.md`, parses real YAML frontmatter when available, tolerates Hermes- and skill-creator-style nested metadata such as `platforms`, `compatibility`, and `metadata`, reports bundled resource directories like `scripts/`, `references/`, `assets/`, `agents/`, and `evals/`, filters the prompt catalog by platform and available tool names, caches discovery by manifest metadata, and can save approved reusable experiences under `skills/experience/<name>/SKILL.md`. `packages/amadeus/live2d.py` now owns the local Live2D model library boundary while renderer-specific adapter logic still lives in desktop.
- `packages/live2d-stage` is still an intended package boundary; the working Live2D renderer logic currently lives in `apps/desktop/src/renderer/companion/main.ts`, while the sole production larger chat/workbench renderer is the Vue app in `apps/desktop-ui-next`. The earlier vanilla Main UI renderer and its Electron Vite entry have been removed.
- `apps/desktop-ui-next` should be treated as the replacement for the legacy Main UI renderer only. It should not replace `apps/desktop` itself unless a new Electron shell is created elsewhere, because `apps/desktop` still owns native window lifecycle, IPC/preload wiring, Companion, global cursor tracking, desktop playback, and packaged Electron E2E entrypoints.

Current progress calibration:

- The active project phase is desktop/runtime stabilization, not MVP construction. The Python-first runtime path, local Live2D model serving, TTS fallback, ToolRuntime foundation, and Memory v2 foundation are already present. The next work should prove and harden real packaged-desktop behavior first.
- Electron E2E now covers both deterministic component boundaries and one real-process local runtime path. The real path uses isolated ports, a temporary database/state root, and per-run Electron `userData`; it exercises the packaged Vue UI through the Python runtime and Node bridge, confirms the user/assistant turn in SQLite, switches to another session and back, reloads persisted chat, and approves a review-gated task. The Live2D path still uses a deterministic Live2D test double for model config/list, renderer load, model selection, `/live2d/select`, and harness config persistence. Runtime audio covers `audio.tts-ready`, mock playback success/failure, and `audio.playback-*` feedback; permission coverage exercises both Allow and Deny.
- Lipsync is still a priority experience gap, but the desktop now does a first real upgrade: runtime can emit `audio.lipsync-cues`, the desktop consumes those cues when present, otherwise runtime audio playback drives `ParamMouthOpenY` from Web Audio amplitude analysis, and the older timed mouth loop remains as fallback for speech synthesis and environments where media-element analysis is unavailable. `packages/amadeus/audio.py` now prefers provider-native lipsync cue payloads when a TTS provider returns them, otherwise plans runtime cues from a text-driven phoneme/viseme sequence and uses local cached `wav` envelope data only as a timing/intensity modulator when available. Future work should improve non-Latin phoneme mapping and broaden provider cue-schema compatibility.
- `apps/server` should keep shrinking toward a transport bridge. It now proxies `/live2d/*`, forwards `tool.permission.response`, forwards runtime feedback, and relays turns, but it should not regain agent-loop, tool-loop, memory, model-library, or provider-decision ownership.
- ToolRuntime should be treated as mostly implemented and entering hardening mode. Do not start a second tool execution framework; extend `amadeus.tool_runtime` when new reliability needs appear.
- Memory v2 should be treated as core-implemented and entering consolidation mode. `ContextAssembler` now consumes the active runtime memory provider for API-call-time summary/FTS injection and `memory.context.used` diagnostics; the default local provider supplies summaries and FTS retrieval when no external provider is active. Long-term `memory_items` use a Mem0-like shape with `memoryType`, JSON metadata, content hash, source ids, access stats, and history events. Durable facts now maintain their own `memory_items_fts` BM25 index over content/type/metadata, support simple metadata filters, and synchronize that index on add/replace/delete. When local BGE-M3 is configured and deployed, `memory_item_embeddings` stores derived dense vectors and `search_memory_items` uses hybrid vector/BM25/metadata ranking for typed long-term recall, falling back to the BM25/SQL path when vectors are missing or unavailable. External providers replace that runtime provider surface rather than stacking another memory backend beside it. The runtime retains the most recent diagnostics per session in an in-memory ring buffer for developer inspection. The next memory work should focus on summary/profile policy, review quality, ranking observability, overflow behavior, and diagnostics endpoints rather than basic storage primitives.
- Runtime diagnostics now have two layers: legacy `GET /health` for compatibility and structured `GET /runtime/health` for local health checks across runtime, model config, memory DB, task worker, embedding, tools, Live2D, audio, harness feedback, and effective runtime config. Keep this endpoint local and deterministic; do not make it call external model or TTS providers. The Vue Config Center consumes this endpoint through a Runtime Doctor tab that is optimized for first-run and local dependency readiness: it shows summary cards, flags missing API keys / unavailable task supervisor / Live2D model issues / disabled TTS / optional embedding deployment, and links to the relevant configuration tabs. This is a product-facing view of existing local health data, not a replacement for logs or a remote telemetry surface.
- The first runtime harness slice is in place: `packages/amadeus/harness` loads `configs/harnesses.yaml`, the Live2D harness maps `assistant.state` into `character.behavior`, and the AudioHarness observes final `assistant.message` events to call `AudioRuntime.speak()` and emit `audio.lipsync-cues` / `audio.tts-ready` through the same registry path. Continue maturing the model boundary only as needed for additional providers or richer provider-specific response handling.
- The first practical TTS loop is in place: `packages/amadeus/audio.py` can auto-select GPT-SoVITS when configured, otherwise use macOS `say`/`afconvert` as a local TTS provider, cache generated wav files under the local audio library, and return `audio.tts-ready` through the existing runtime path.
- Local Live2D model storage is in place: `models/live2d` stores switchable local models, `configs/harnesses.yaml` selects the active model, and `packages/amadeus/live2d.py` now owns model resolution, model listing, manifest reads, and `/live2d/select` persistence. `apps/server` keeps the desktop-facing `8788` origin by proxying `/live2d/*` to the Python runtime and rewriting model URLs back to the bridge origin. The default model is now local `hiyori-free`.
- First-pass desktop feedback is in place end to end: each renderer sends `desktop.capabilities` after connection, Companion sends updated capabilities after model load, and both `clientId` / `surface` metadata and audio playback feedback are forwarded to Python `POST /runtime/feedback`; Python `HarnessFeedbackPolicy` stores per-client capabilities, aggregate session capabilities, audio playback state, and recent feedback events for harness policy.
- Live2D now consumes playback feedback at the harness layer: `audio.playback-started` maps to a talking behavior, `audio.playback-ended` maps to idle, and `audio.playback-error` maps to a confused/failure behavior. The bridge forwards these Python-returned `character.behavior` events back to the desktop socket. The desktop still keeps its immediate local mouth loop as a fallback and low-latency response.
- The desktop side now prefers runtime-provided `audio.lipsync-cues` for runtime audio, otherwise samples the playing `HTMLAudioElement` through Web Audio `AnalyserNode` and maps waveform energy to `ParamMouthOpenY`. The Python audio library now resolves actual `wav` duration where possible, normalizes provider-native `lipsyncCues` / `visemes` / `phonemes` payloads when present, otherwise builds phoneme/viseme cue sequences from assistant text, and can scale those fallback cues with local waveform envelope data. The first AudioHarness removes the direct TTS branch from the agent loop; the next large architectural gaps are richer audio harness policy decisions, richer Live2D commands, better non-Latin phoneme mapping, and later skill management / orchestration rather than basic skill loading.

Live2D and audio should be treated as installable harnesses. They can contribute prompt fragments and observe runtime events, but the actual rendering and playback stay in the desktop adapter.

## Tool Runtime Boundary

The Python runtime now separates concrete tool implementations from runtime tool policy:

- `amadeus.tools`: public tool registry entrypoint, concrete local tool handlers, and their default `ToolSpec` metadata.
- `amadeus.tool_runtime.registry`: effective registry construction, `configs/tools.yaml` overlays, enabled schema selection, permission-state projection, structured `ToolContext` / `ToolResult`, turn/tool-call and permission metadata propagation, session workspace epoch propagation, duration/failure metadata, first-pass timeout/cancellation handling, result preview/compression for model context, per-tool model-output policies, and handler dispatch.
- `amadeus.tool_runtime.audit`: tool audit events plus SQLite persistence, metadata payloads, and filtered query APIs for started/finished/denied/blocked/failed decisions.
- `amadeus.tool_runtime.guardrails`: per-turn guardrails for repeated failed calls, repeated completed calls, and semantic no-progress patterns such as empty/same searches, repeated read windows, repeated patch failures, and repeated write failures. File-observing signatures include the session `workspace_epoch` so successful workspace edits invalidate stale file read/search no-progress counts.
- `amadeus.agent`: conversation loop, permission requests, event streaming, memory writes, system prompt caching, external memory prefetch, and coordination with the tool runtime.
- `amadeus.memory_safety`: pre-persistence safety checks for memory review candidates, currently blocking secret-like content, temporary debug/run state, uncertain claims, overly specific local/cache/generated paths, and obvious `user` / `agent` / `project` scope mismatches before safe candidates are auto-promoted into durable memory.
- `configs/runtime.yaml`: runtime memory/context/task defaults for token-budget compaction, context assembler budgets, context diagnostics retention, summary windows/cooldowns, memory review limits, and worker action-specific approval expiry. Budget-driven compaction derives its raw recent tail from `maxTokens * compactionTriggerRatio * recentMessageTargetRatio`, with a small capped message-count floor so large tool results cannot force an oversized tail. Environment variables are still allowed as deployment overrides, including `AMADEUS_WORKER_APPROVAL_ACTION_TTL_SECONDS`, and `POST /runtime/config/reload` reapplies the YAML-backed effective config without restarting. Recent in-memory context diagnostics are queryable with `GET /memory/context/diagnostics`.
- `packages/amadeus/tools.ts`: TypeScript bridge types and Python tool HTTP clients only. It intentionally does not mirror concrete tool handlers or schemas; server diagnostics should call Python `/tools/list`.

Keep future tool hardening inside `tool_runtime` unless it needs model context or desktop events. The next additions should be additional per-tool result policies for new high-volume tools, richer diagnostics UI surfaces on top of `GET /tools/audit` if needed, and continued tuning of semantic no-progress policies as new tools land. Live2D and audio harnesses may register optional tools later, but they should not be implemented as ad hoc branches in the agent loop.

## Skills V1 Boundary

The first runtime skill slice is intentionally narrow and modeled after the useful parts of Hermes rather than its full ecosystem:

- Skills live under `skills/<category>/<skill-name>/SKILL.md`.
- `SKILL.md` should declare `name` and `description`. `preferred_tools` and `allowed_tools` are optional, and broader frontmatter like `platforms`, `compatibility`, and nested `metadata` is accepted for compatibility with more general skill packs and skill-creator output.
- Python exposes `GET /skills/list` and `GET /skills/view`.
- The tool registry exposes `skills_list`, `skill_view`, and `skill_manage`. `skill_manage` is an `ask` tool and currently supports saving approved reusable workflow experience as a local skill.
- The runtime system prompt now includes an always-on `<available_skills>` catalog filtered by declared platform and available tool names, following the Hermes-style progressive disclosure path: the model should inspect the catalog and call `skill_view(name)` before relying on a relevant installed skill.
- `POST /agent/turn` still accepts an optional `skills: string[]` field, but those are now injected as `<suggested-skills>` hints rather than mandatory full skill instructions.
- When `skill_view(name)` succeeds during a turn, Python appends that skill's full instructions as a turn-local `<active-skills source="skill_view">` block for the rest of that turn.
- Skill activation is now observable through streamed `skill.started` / `skill.finished` events, mirroring the lighter-weight `tool.started` / `tool.finished` desktop status model without introducing a separate persisted audit system.
- `apps/server` now proxies read-only `/skills/list` and `/skills/view` requests to Python so the desktop can stay on the bridge origin.
- The desktop renderer now exposes a refreshable multi-select skill checklist with local search/filtering, shows only a short inline summary for the active skill, persists selected skill identifiers plus the last active preview in local storage, and includes the selected skill identifiers on each `user.message` turn payload.

This is enough to establish a real skill boundary plus a narrow experience-save path without taking on bundles, marketplace sync, subagent orchestration, or a full skill editing/import UI yet.

`skills/web-access` is installed as a project skill for real web access. It keeps the downloaded skill resources (`SKILL.md`, `scripts/`, `references/`, templates, and plugin metadata) inside the Amadeus skill tree, while local `config.env` remains ignored. Runtime activation follows the same progressive path as other skills: the model sees the compact catalog, calls `skill_view("web-access")`, receives the full CDP workflow instructions as a turn-local active skill, and then uses existing tools such as `terminal` under normal permission and audit control. This is deliberately separate from the built-in `web_search` implementation: `web_search` still uses the lightweight DuckDuckGo HTML provider, while `web-access` is a procedural fallback for real browser access.

`workspace_epoch` is maintained by `AgentRuntime` per session. It is a monotonic runtime counter, not a content hash or filesystem scan. It starts at `0`, is passed into `ToolContext`, guardrail signatures, and `tool.audit` metadata, and advances after `patch` or `write_file` succeeds with `changed: true`. Successful `terminal` and `execute_code` runs also advance it conservatively because arbitrary shell/Python code can mutate workspace files without returning a structured diff. This lets the same `read_file` window or `search_files` query be blocked as no-progress within one epoch, then become allowed again after a real workspace mutation.

### Tool Inventory And Extension Path

Current active Python tools:

- `get_current_time`: `allow`; returns formatted current time for an IANA timezone.
- `roll_dice`: `ask`; rolls bounded dice counts/sides and returns rolls plus total.
- `terminal`: `ask`; runs bounded foreground shell commands inside the workspace, captures stdout/stderr, enforces cwd containment, and conservatively advances `workspace_epoch` after successful execution.
- `process`: `ask`; lists local processes, checks status for a pid, or sends a signal to a known pid.
- `web_search`: `allow`; searches the public web through a lightweight DuckDuckGo HTML provider and returns bounded result titles/URLs.
- `web_extract`: `ask`; fetches HTTP(S) pages and extracts bounded readable text from HTML/text responses.
- `browser_navigate` / `browser_snapshot` / `browser_click` / `browser_type` / `browser_scroll` / `browser_back` / `browser_press` / `browser_get_images` / `browser_vision` / `browser_console` / `browser_cdp` / `browser_dialog`: registered but disabled by default; bridge to a configured HTTP browser backend (`AMADEUS_BROWSER_TOOLS_URL`) or MCP browser server (`AMADEUS_BROWSER_MCP_URL`) instead of embedding a second Playwright runtime.
- `vision_analyze`: `ask`; extracts safe local image metadata without a provider, or sends image/prompt data to `AMADEUS_VISION_ENDPOINT` when configured.
- `clarify`: `allow`; prepares structured user-facing clarification questions for ambiguous or irreversible work.
- `execute_code`: `ask`; runs bounded Python code from a temporary script in a workspace-contained cwd, captures stdout/stderr, and conservatively advances `workspace_epoch` after execution.

Smoke testing confirms the local handlers, HTML extraction, browser HTTP bridge, vision HTTP bridge, and project skill activation path run correctly. Public web search still depends on external endpoint reachability; DuckDuckGo HTML and Jina search timed out from the current development network, so reliable web access should use a configured proxy/provider or the installed `web-access` skill. The real browser/CDP checks are intentionally opt-in:

```bash
AMADEUS_RUN_WEB_ACCESS_SMOKE=1 python -m unittest \
  tests.test_python_agent_runtime.AgentRuntimeTests.test_web_access_skill_smoke_task_uses_project_cdp_proxy \
  tests.test_python_agent_runtime.AgentRuntimeTests.test_web_access_skill_smoke_task_finds_attention_paper_on_arxiv
```

The paper smoke test searches arXiv for `Attention Is All You Need`, verifies `arXiv:1706.03762` and `Ashish Vaswani` through the arXiv API, then opens the abstract page through the CDP proxy and checks the browser DOM before returning a compact `AMADEUS_PAPER_LOOKUP_RESULT` to the model context.
- `read_memory`: `allow`; reads current-role stable Markdown memory from `data/roles/<roleId>/memory/MEMORY.md` or `data/roles/<roleId>/memory/USER.md`.
- `update_memory`: `ask`; performs controlled `add` / `replace` / `remove` updates to current-role stable Markdown memory, with exact-match replacement and size limits.
- `update_current_role_identity`: `ask`; updates the current session role name and/or `data/roles/<roleId>/SOUL.md` after explicit user approval.
- `skills_list`: `allow`; lists installed runtime skills with summaries and declared tool preferences.
- `skill_view`: `allow`; loads full instructions for one installed runtime skill and activates it for the rest of the current turn.
- `skill_manage`: `ask`; saves or updates an approved reusable workflow experience as `skills/<category>/<skill-name>/SKILL.md`, defaulting to `skills/experience`.
- Role runtime scope: each role can optionally store `runtimeScope` with `tools`, `skills`, and `mcpServers` arrays. Empty arrays mean "no role-level restriction" for that category. Non-empty arrays narrow the globally enabled ToolRegistry, skill catalog, and MCP server-derived tools for sessions attached to that role. This affects system prompt tool hints, `<available_skills>`, model tool schemas, `/tools/list?sessionId=...`, `/skills/list?sessionId=...`, `skill_view`, and direct `/tools/execute`; it does not grant permissions or enable globally disabled tools.
- MCP bridge first slice: when `tools.mcp.enabled` is true in `configs/tools.yaml`, `ToolRegistry` discovers configured HTTP JSON-RPC MCP servers via `tools/list`, exposes each remote tool as `mcp__<server>__<tool>`, and executes it through `tools/call` while reusing normal permission, timeout, cancellation, result compaction, and audit paths. Server and tool identifiers are normalized to model-safe names by lowercasing and replacing spaces, dots, hyphens, and other non-identifier characters with `_`; for example `hermes-fixture` + `messages-read` becomes `mcp__hermes_fixture__messages_read`.
- Main UI now exposes MCP management and observability surfaces. The MCP tab edits `tools.mcp.enabled`, default permission, and HTTP JSON-RPC server entries, can test `tools/list` discovery for one server, saves through Python `POST /tools/config`, and rebuilds the Python `ToolRegistry` immediately so server diagnostics and model tool schemas refresh without a manual restart. It also compares global `/tools/config` discovery with current-session `/tools/list?sessionId=...` role-filtered visibility, shows per-server discovered/visible/filtered counts, recent MCP failure codes and durations, permission/blocked decisions, and persisted ToolRuntime metadata from `/tools/audit`. `scripts/dev_mcp_server.py` provides a tiny local HTTP JSON-RPC MCP server with `echo` and `project_info` tools for manual verification, and `scripts/dev_mcp_server.py --fixture hermes` exposes no-token Hermes-style local conversation/message tools (`conversations_list`, `conversation_get`, `messages_read`, `channels_list`) for more realistic MCP add/test/execute checks. This is still HTTP JSON-RPC only; stdio/SSE server lifecycle management remains future work.
- `search_memory`: `allow`; searches prior SQLite conversation memory through an FTS-backed index, scoped to the current session by default, with a per-tool model-output policy for bounded snippets. The FTS query and index content use `jieba` tokenization for Chinese recall. The context assembler also prefetches a small sanitized FTS result set each turn and injects it as API-only `<memory-context>` on the current user message.
- `read_session_messages`: `allow`; reads a bounded, paginated raw transcript window for a session. This is a transcript/log inspection tool, not a durable memory provider output, and has its own model-output policy for capped message previews.
- Tool-call transcript persistence: `messages` now stores assistant `tool_calls` and `role=tool` results with `tool_call_id` / `tool_name`, so tool execution history can survive across turns as a valid OpenAI-style transcript rather than only as in-turn memory. Provider history loading strips DB-only metadata, removes orphan tool results, and inserts a small stub result if a retained assistant tool call lost its matching result to a history window. Summary compaction aligns its fold window so `assistant(tool_calls)` and following `tool` results are not split; tool-call details are represented in summary source lines instead of being treated as plain assistant text. Context compaction can now run before a turn when the assembled request is over budget, after provider context-overflow errors before retry, and after a completed turn when the saved final response pushes the next request over budget.
- Main UI transcript rendering keeps assistant tool-call decision messages visible when they contain content or `tool_calls`, renders the tool calls as collapsed cards with tool names/arguments by default, filters truly empty assistant bubbles, and groups consecutive Agent messages so only the final Agent message in a user turn shows the avatar. Turn-scoped `PlanPanel` rendering is assistant-side, matching the model-authored nature of `update_plan`.
- `search_memory_items`: `allow`; searches durable structured `memory_items` facts by optional scope/query/type/metadata filter, uses BGE-M3 hybrid vector/BM25/metadata ranking when configured, falls back to BM25/SQL, and applies a per-tool model-output policy for bounded fact metadata.
- `memory_add`: `ask`; writes one durable structured memory fact after user approval, limited to `user` / `agent` / `project` scope, with duplicate detection and source-session metadata.
- `memory_replace`: `ask`; replaces one active durable structured memory fact after user approval.
- `memory_forget`: `ask`; deletes one active durable structured memory fact after user approval.
- Memory review candidates are stored as audit records around durable memory promotion. `POST /memory/review/run` asks the provider to propose candidates from recent messages, safety filters suppress unsafe/transient/scope-mismatched proposals, safe candidates are marked `accepted` and written to `memory_items`, and pending candidates can still be accepted through `POST /memory/review/accept` or rejected through `POST /memory/review/reject`. Automatic post-turn review is threshold/cooldown gated and follows the same auto-promotion path. Rejected candidates suppress identical future suggestions.
- The desktop review UI uses WebSocket events rather than talking to the Python sidecar directly: `memory.review.list`, `memory.review.run`, `memory.review.accept`, and `memory.review.reject` are handled by `apps/server`, which proxies the Python memory review APIs and returns `memory.review.candidates` / `memory.review.updated`.
- `search_files`: `allow`; searches workspace-relative filenames and/or small text file contents with `target: all | files | content`, path containment, skipped generated directories, result caps, and a per-tool model-output policy.
- `read_file`: `allow`; reads an explicit line-numbered window from a workspace-relative UTF-8 text file with path containment, file type/size limits, `startLine` / `lineLimit`, `totalLines`, `hasMore`, and a visible character cap. It intentionally avoids hidden runtime compression. Images, PDFs, binaries, and unknown extensions return structured `kind/supported/hint` metadata instead of being decoded.
- `patch`: `ask`; applies a single-file UTF-8 text replacement with workspace containment, generated-directory denylist, file size limits, unique `oldText` matching by default, optional `replaceAll`, and unified diff output.
- `write_file`: `ask`; creates or fully overwrites workspace-relative UTF-8 text files with workspace containment, generated-directory denylist, text-extension checks, size limits, explicit `overwrite=true` for replacement, parent directory creation inside the workspace, and unified diff output.

`search_files` is the only built-in project file search tool exposed by the Python registry. The old `local_file_search` alias has been removed to keep built-in tool selection unambiguous. MCP tools are externally supplied and use the `mcp__<normalized_server>__<normalized_tool>` namespace.

To add a simple tool, implement a JSON-serializable handler in a focused module under `packages/amadeus/tools/`, define its `ToolSpec` next to the handler, register that spec from `packages/amadeus/tools/__init__.py`, add the effective config entry in `configs/tools.yaml`, and cover it with focused ToolRuntime tests. Use `handler(args, context)` when the tool should observe cancellation or session/cwd metadata. Keep risky actions as `ask`, constrain filesystem/network behavior explicitly, and add a per-tool result policy in `tool_runtime/registry.py` when outputs can become large.

For external tools, prefer MCP config over new built-ins when the capability naturally belongs to another local service. The first supported transport is HTTP JSON-RPC with `tools/list` and `tools/call`; stdio/process lifecycle management is still future work.

Task worker execution is split at the `TaskRunner` boundary. `TaskWorker` remains the only owner of task claim/retry/recovery/cancel/event semantics. Production defaults to `AMADEUS_TASK_RUNNER=subprocess`, `AMADEUS_TASK_SUPERVISOR_MODE=external`, and `AMADEUS_TASK_WORKSPACE_ISOLATION=copy`; the Python HTTP runtime uses `ExternalSupervisorTaskRunner` as a no-launch SQLite client while `packages/amadeus/task_supervisor.py` owns recovery, queued dispatch, and subprocess launch in an independent process. `in_process`, synchronous, embedded subprocess supervision, and supported non-macOS POSIX fork runners remain explicit compatibility/test choices. The fork runner is rejected on macOS because the full threaded Python runtime is not safe to execute after `fork`.

The external supervisor uses `supervisor_leases` as a SQLite single-primary lease and `task_processes` as a durable run/PID/process-group registry. Each tick renews ownership before reconciliation or dispatch. New queued work is polled independently from the older embedded recovery interval through `AMADEUS_TASK_SUPERVISOR_POLL_SECONDS` (default 1 second), while the primary lease defaults to 45 seconds through `AMADEUS_TASK_SUPERVISOR_LEASE_SECONDS`. A restarted supervisor adopts still-live worker PIDs, records `subprocess_adopted`, reclaims missing workers through the existing retry/fail path, and terminates cancelled or wall-time-expired adopted workers with `SIGTERM` followed by `SIGKILL` after the configured grace period. `SubprocessTaskRunner` writes combined stdout/stderr to a per-run log, records workspace/log/resource metadata, and keeps process registry exit state. The worker entrypoint applies CPU, address-space, file-size, and open-file POSIX limits before constructing `AgentRuntime`; wall timeout is enforced by the supervisor. Limit application is recorded as `worker_resource_limits_applied`, including unsupported or failed host limits. Relevant settings are `AMADEUS_TASK_LOGS_ROOT`, `AMADEUS_TASK_WALL_TIMEOUT_SECONDS`, `AMADEUS_TASK_CPU_LIMIT_SECONDS`, `AMADEUS_TASK_MEMORY_LIMIT_MB`, `AMADEUS_TASK_FILE_SIZE_LIMIT_MB`, `AMADEUS_TASK_OPEN_FILES_LIMIT`, and `AMADEUS_TASK_TERMINATION_GRACE_SECONDS`.

`/runtime/health` reports configured runner kind, supervisor mode, external lease liveness, and active process records, and degrades the task-worker check when the external supervisor is absent. `scripts/dev_stack.py` starts the supervisor before the Python runtime. Supervisor SIGTERM detaches live child process groups instead of killing them, releases its lease, and lets the next supervisor adopt the registry. Copy isolation creates a per-run workspace under `AMADEUS_TASK_WORKSPACE_SANDBOX_ROOT` or the memory database directory, skips generated/heavy directories and external symlinks, passes `AMADEUS_WORKER_WORKSPACE_OVERRIDE`, `AMADEUS_WORKER_WORKSPACE_ISOLATION`, and `AMADEUS_WORKER_WORKSPACE_SOURCE` to the child, and runs the entrypoint with that copied workspace as cwd.

OS-native worker containment is selected independently from workspace-copy isolation through `AMADEUS_TASK_OS_SANDBOX=auto|required|none|bubblewrap|sandbox-exec`. `auto` probes Linux `bwrap` or macOS `sandbox-exec` with a real subprocess before claiming enforcement; an unavailable backend remains an explicit `backend=none`, `enforced=false` status with the probe failure reason. `required` and explicit backend names fail supervisor construction when enforcement is unavailable. Bubblewrap mounts the host root read-only, keeps the SQLite state root writable, remounts the shared worker-workspace root read-only, then opens only the current task workspace for writes. The Seatbelt profile permits host reads, process/network operations needed by the model/tool runtime, writes to the current workspace, and writes only to the SQLite database/WAL/SHM/journal paths. Worker HOME/temp directories live under the current workspace. Selection and enforcement metadata is persisted in the supervisor lease, process registry, and `subprocess_started` event and is surfaced through `/runtime/health`. On the current macOS development host, even an allow-all `sandbox-exec` probe returns `Operation not permitted`, so `auto` honestly falls back to the existing copy/env/tool guards; deployment requiring kernel enforcement must set `required` and run on a host where the backend probe succeeds.

Worker turns now apply `workerProfile` / `allowedToolsets` / `disallowedTools` / task workspace hints through a temporary `AgentRuntime` `WorkerRuntimeScope`, so model tool schemas, prompt hints, execution checks, tool context metadata, audit metadata, and workspace root resolution are narrowed for the child task in addition to normal role scope. Task workspace hints must resolve inside the current session workspace; invalid non-copy worker workspaces fail the task before model execution with a `worker_scope_invalid` attempt checkpoint instead of silently escaping the role workspace or retrying a deterministic configuration error. Copy-isolated worker scopes may point at a workspace outside the session root, but `validate_worker_runtime_scope` requires `workspace_source_path` to resolve inside the session workspace and then lets `_workspace_root_for_session` use the copied root. `task_worker_entrypoint` also constructs `MessageMemoryStore` and `AgentRuntime` with the copied workspace as the default root so the child runtime is not sharing the parent repo root implicitly. Worker scopes also carry a first sandbox mode from profile defaults or task `contextHints.sandboxMode`: `read_only` hides and blocks mutation/execution tools, `workspace_write` allows workspace file mutation while blocking shell/process/code execution, and `workspace_execute` permits execution tools only when the profile/toolset scope also allows them. Sandbox-denied calls produce `worker_sandbox_denied` tool/audit records before handler execution. Worker `terminal` and `execute_code` calls in an active worker sandbox use workspace-local `HOME` and temporary directories; `terminal` rejects obvious outside-workspace absolute/relative path references before invoking the shell, and `execute_code` injects a workspace write guard through `sitecustomize` for common Python file-writing APIs. Worker permission policy is profile-aware: ask-tools are not allowed to open interactive prompts from background worker turns; narrow profile-approved ask tools such as researcher `web_extract` and coder `patch` are auto-approved only when the concrete action is not high-risk and has no blocking risk labels. Other worker ask-tools now block the task with a tool/action-specific `approval_required` checkpoint containing an action key, label, risk level, and risk labels when available; action classifiers now identify destructive/privileged shell commands, installers, network scripts/access, secret-like output, sensitive paths, workspace-external paths, whole-file writes, bulk replacements, insecure/sensitive URLs, and unknown targets. After the user resumes, the checkpoint grants that specific action key to the next `WorkerRuntimeScope` as a one-run worker auto-approval with configurable expiry from `tasks.workerApprovalActionTtlSeconds` / `AMADEUS_WORKER_APPROVAL_ACTION_TTL_SECONDS` (default 15 minutes). Legacy tool-wide approval remains only for older checkpoints that lack an action key and is not sufficient for high-risk or blocking-label actions.

Process action approvals are target-specific: kill/status keys bind to a concrete PID, kill keys also bind to the normalized signal, and list keys bind to a normalized query hash. Block/resume/review approval task events preserve action, risk, scope, TTL/expiry, actor, and source metadata where applicable; successful `worker_auto_approved` tool audit records preserve the correlated action/risk/expiry fields.

Each worker model turn now runs under an archived `worker:<taskId>` session while the task itself remains owned by the parent session. This keeps child user/tool/assistant messages out of parent raw history and gives cancellation a task-specific running-turn key. Worker prompt assembly deliberately disables automatic role stable-memory, transcript/global-FTS, and external-memory prefetch; `WorkerContext` remains the initial handoff, while explicitly allowed memory tools can read only the isolated session or the recorded source session. The `delegate_task` tool now uses this path instead of its old heuristic search/read implementation: it creates a `delegated` researcher task, applies a read-only scope and tool denylist, waits for the durable task result under `tasks.childAgentTimeoutSeconds` / `AMADEUS_CHILD_AGENT_TIMEOUT_SECONDS`, propagates parent cancellation, and returns only summary/task metadata.

Worker attempts now persist stage-oriented checkpoints during context build, scope validation, model-turn start, tool completion, assistant output, error, cancellation, review blocking, worker tool approval blocking, and completion; heartbeats preserve the latest structured checkpoint instead of replacing it with a bare running marker, and previous attempt prompts include checkpoint phase/last-event hints. Worker `tool.finished` previews are saved as normalized task artifacts with tool metadata, and current-task artifacts are included in WorkerContext as `<task-artifacts>` so resumed workers can reuse prior tool output. Successful tool events now include a compact JSON result preview when no policy preview exists, letting worker artifacts capture file-state/idempotency metadata for patch/write/read/search/terminal tools: affected files, observed files, commands, exit codes, changed flags, explicit hints to verify saved state before repeating work, workspace file manifests with size, mtime, and SHA-256 where files are available and bounded, current manifest verification during `WorkerContext` build, and first-pass `fileResumePolicy` instructions such as `skip_redundant_mutation`, `reinspect_before_mutation`, and `reuse_observation`. Main UI task artifact cards render those verified file resume policies with status tags, paths, instructions, and override tags so resume behavior is auditable without reading raw JSON. The same verified policies now enter `ToolLoopGuardrail` through `WorkerRuntimeScope`, blocking unchanged same-tool same-path repeat mutations before handler execution and requiring a same-turn `read_file` refresh before mutating a path whose saved mutation artifact is changed. A first override contract is supported on `fileResumePolicy.override`: `force_rerun` allows repeating a matching mutation, `ignore_artifact` skips one stale artifact policy, and `accept_current_state` accepts a changed file state without requiring a read refresh. Task artifacts are now addressable through a first-class artifact list endpoint, and Main UI task details can set or clear those file-resume overrides through `POST /tasks/{taskId}/artifacts/{artifactId}/file-resume-override`; the runtime publishes a task update with `artifact_override_updated` after each change. Override set/clear operations also append durable task events with artifact/policy identity, previous/new override, change status, actor, and source; Main UI maps both event types to explicit timeline labels.

Stale-lease recovery and subprocess-loss retry now promote the last running attempt checkpoint into the task checkpoint as `resumeFrom` plus a handoff summary, so the next worker turn sees the interrupted phase rather than only a generic retry error. Review-required completions now persist a task-level `approval_required` checkpoint plus handoff summary while blocked; approval writes `approved`, and manual resume records either `approval_resume_requested` or `blocked_resume_requested`. `WorkerContext` now turns `resumeFrom` into an explicit `<resume-strategy>` prompt section, guiding the next worker to verify partial assistant output, avoid duplicate work, or change approach after errors depending on the saved phase. For approval resumes, the strategy also lists approved ask-tools, approved action keys, and action-key expiry, then tells the worker to use them only for the blocked step, not as broad or permanent permission. Main UI task details surface approval action label/key, tool name, risk labels, expiry status, scoped approval copy, and approval-aware resume button text above the raw checkpoint JSON. The supervisor and native backend boundary are still single-host facilities: Windows/container backend support, network policy profiles stricter than the current model-provider-compatible allowance, longer double-crash soak coverage, and broader approval/override policy tuning remain.

Running tasks now persist a lease (`leaseOwner`, `leaseExpiresAt`, `runnerKind`) in addition to the legacy `claimLock`; worker heartbeat threads renew the lease during long turns, startup recovery requeues expired leases or legacy stale heartbeats, and terminal transitions clear lease state. Current worker turns are built from an isolated `WorkerContext` containing the task spec, acceptance criteria, context hints, task/dependency artifacts, and previous attempt history; do not fall back to parent conversation replay for child/worker execution. Do not introduce a parallel Kanban/swarm scheduler until this runner boundary has been exercised.

Task artifacts have both a compatibility summary and a normalized first-class store. `tasks.artifacts_json` remains the backward-compatible response field, while `task_artifacts` records worker/dependency handoff payloads linked to task attempts. Normalization lives in `amadeus.tasks` and enforces the first typed payload set (`file`, `diff`, `command_output`, `summary`, `link`) with bounded textual fields.

Long-task decomposition and synthesis should go through the internal `OrchestratorService`, not direct model-authored database mutations. The service accepts structured graph payloads, validates task ids/dependencies/cycles, enforces the first worker profile/toolset policy matrix, persists child tasks and edges through the normal task store, dispatches only dependency-ready children, records graph lifecycle events on the root task, publishes graph-specific `task.updated` actions from controlled HTTP entrypoints, and synthesizes terminal child results back into the root task. An applied graph owns its root in `blocked/orchestrator_waiting`; workers must never execute that root. `maxConcurrency` is enforced in runnable selection and again in the `BEGIN IMMEDIATE` claim transaction. Root cancellation cascades to active children before the root, while child cancellation is local. Failed-child replan is append-only and bounded: replacements inherit the old worker/tool policy, dependency rewiring is atomic, and only explicitly superseded failures are excluded from synthesis. Model-backed `specify` / `decompose` / `repair` / `synthesize` is available only as an internal service path with fixed JSON prompts and conservative deterministic fallbacks. Do not expose these internals as unconstrained model tools or bypass graph validation. Changes to graph orchestration, runner durability, worker isolation, Memory recall/safety policy, Audio fallback/lipsync, approval scope, or artifact resume must keep `scripts/eval_runtime_contracts.py` green; this deterministic contract suite emits per-check metrics and is the first gate before broader unit, typecheck, and Electron coverage.

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
- bounded public web search returning titles/URLs only: `allow`
- fetching page contents or calling external action APIs: `ask`
- patching or writing files: `ask`
- running scripts, shell commands, installers, opening URLs, or touching workspace-external paths: `ask`
- deleting broad file trees or arbitrary shell execution without a stronger approval UI: `deny`

## Desktop Behavior States

The desktop character should react to runtime state:

- `idle`: default breathing/idle animation
- `listening`: reserved in shared types but not meaningfully used yet in the current flow
- `thinking`: focused expression or thinking motion
- `speaking`: talking motion and lipsync
- `tool-running`: focused/working state
- `error`: confused expression, then return to idle
