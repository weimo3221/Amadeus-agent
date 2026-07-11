# Amadeus Runtime

Python-side Amadeus runtime.

This package is intended to become the real agent core. The surrounding TypeScript apps are moving toward transport and device-adapter roles:

- `apps/desktop`: Electron window, Live2D rendering, local UI, runtime audio playback, and permission UI.
- `apps/server`: WebSocket bridge between desktop events and the Python runtime.
- `packages/amadeus`: preferred agent turn path, memory, tools, runtime HTTP API, and active model/skills/harness boundaries.

## Current active modules

- `agent.py`: active preferred turn flow.
- `memory.py`: active SQLite-backed message history, FTS search, and conversation summaries.
- `tools/`: active Python tool implementations and their public registry entrypoint.
- `audio.py`: active audio/TTS interface.
- `server.py`: active HTTP runtime.

## Current evolving boundaries

These files are active boundaries, but some still need more depth:

- `model.py`
- `skills.py`
- `live2d.py`

## Current runtime behavior

- `agent.py` contains the real preferred turn logic today.
- The runtime loads recent SQLite history, saves user and assistant messages, runs a bounded Hermes-style tool loop with OpenAI-compatible `tool_calls`, executes Python tools until the model stops requesting tools or reaches the configured iteration budget, emits streamed `assistant.delta` / `assistant.message`, and may emit `audio.tts-ready`.
- Skills now live under `../../skills/<category>/<skill-name>/SKILL.md`. The runtime exposes them through `/skills/list`, `/skills/view`, `skills_list`, and `skill_view`. The system prompt now carries an always-on compact skills catalog, and the model is expected to call `skill_view` before relying on a relevant installed skill. `POST /agent/turn` still accepts optional `skills: string[]`, but those are treated as user-suggested skills rather than mandatory full-skill injection. `name` and `description` are the main frontmatter fields; `preferred_tools` and `allowed_tools` are optional. The loader also accepts richer skill-creator / Hermes-style YAML such as `platforms`, `compatibility`, nested `metadata`, and bundled `scripts/`, `references/`, `assets/`, `agents/`, or `evals/` directories.
- Successful or failed skill activation attempts now emit streamed `skill.started` / `skill.finished` events, parallel to the existing tool execution status events, so the desktop UI can show when a `skill_view` result actually became active for the turn.
- Run `python scripts/validate_skills.py skills` to validate the current skill tree, or point it at one skill directory such as `python scripts/validate_skills.py skills/skill-creator --json`.
- Tool permission requests are brokered through streamed `tool.permission.request` events plus `POST /tools/permission`.
- Audio is wired through `audio.tts-ready`. The default `auto` TTS config prefers GPT-SoVITS when configured and otherwise uses macOS `say`/`afconvert` when available, with `speechSynthesis` as the desktop fallback.

## Current Tools

Active tools are defined under `tools/` as Python handlers plus OpenAI-compatible `ToolSpec` metadata. Effective enablement and permission state are loaded from `../../configs/tools.yaml`.

## Runtime Configuration

Runtime memory/context tuning is loaded from `../../configs/runtime.yaml`. This file controls token-budget compaction, context assembler budgets, summary thresholds and cooldowns, the recent raw conversation window, and background memory review limits. The recent raw window is configured in user/assistant turns with `summary.keepRecentTurns` (default `3`) and `summary.minKeepRecentTurns`; attached assistant/tool messages for those turns are kept together. The older `keepRecentMessages` names are still accepted as compatibility aliases. Environment variables remain the deployment override layer, so values such as `AMADEUS_CONTEXT_MAX_TOKENS` and `AMADEUS_SUMMARY_KEEP_RECENT_TURNS` take precedence over the YAML file.

The Python runtime reads this file on startup. After editing it, call `POST /runtime/config/reload` to apply the new values without restarting; the response includes the effective config snapshot.

| Tool | Permission | What it does |
| --- | --- | --- |
| `get_current_time` | `allow` | Returns the current date/time for a requested IANA timezone. It defaults to `Asia/Shanghai` and falls back to UTC for invalid timezones. |
| `roll_dice` | `ask` | Rolls one or more dice with bounded `sides` and `count`, returning individual rolls and the total. |
| `read_memory` | `allow` | Reads stable Markdown memory for agent facts (`MEMORY.md`) or user preferences (`USER.md`). |
| `update_memory` | `ask` | Adds, replaces, or removes bounded stable memory entries without allowing whole-file rewrites. |
| `search_memory` | `allow` | Searches prior SQLite conversation memory for earlier messages, remembered preferences, past decisions, or conversation history. |
| `read_session_messages` | `allow` | Reads a bounded, paginated raw transcript window for a session. Use it for exact conversation-log inspection, not durable memory recall. |
| `search_memory_items` | `allow` | Searches durable structured `user` / `agent` / `project` memory facts stored in SQLite. |
| `memory_add` | `ask` | Adds one durable structured memory fact after user approval, with duplicate detection and source-session metadata. |
| `memory_replace` | `ask` | Replaces one active durable structured memory fact after user approval. |
| `memory_forget` | `ask` | Deletes one active durable structured memory fact after user approval. |
| `search_files` | `allow` | Searches workspace-relative filenames and/or small text file contents using `target: all | files | content`, skipping generated/heavy directories and capping result count. |
| `read_file` | `allow` | Reads an explicit, line-numbered window from a workspace-relative UTF-8 text file after search; images, PDFs, binaries, and unknown extensions return structured `kind/supported/hint` metadata instead of being decoded. |
| `skills_list` | `allow` | Lists installed runtime skills with identifiers, descriptions, and declared tool preferences. |
| `skill_view` | `allow` | Loads the full instructions for one installed runtime skill by identifier or unique skill name. |
| `patch` | `ask` | Applies a safe single-file text replacement inside the workspace, requiring a unique `oldText` match unless `replaceAll=true`, and returns a unified diff preview. |
| `write_file` | `ask` | Creates or fully overwrites a workspace-relative UTF-8 text file, refusing accidental overwrites unless `overwrite=true`, and returns size/line metadata plus a diff preview. |

The runtime layer around these tools adds behavior that tool handlers do not need to reimplement:

- `ToolRegistry` loads default specs and applies `configs/tools.yaml`.
- `ToolContext` carries session id, cwd, optional memory store, turn/tool-call ids, permission decision metadata, audit metadata, session workspace epoch, timeout, cooperative cancellation, and model-output size limits.
- `ToolResult` keeps the full tool output separately from the smaller `model_output` written back into model context.
- Tool execution records duration, stable failure codes, `tool.started` / `tool.finished` events, and persisted `tool.audit` records. Audit payloads include metadata such as `workspaceEpoch` / `workspaceEpochAfter` for normal `/agent/turn` tool calls.
- MCP bridge first slice is implemented inside `ToolRegistry`: configured HTTP JSON-RPC MCP servers are discovered with `tools/list`, exposed as `mcp__<server>__<tool>` schemas, and executed with `tools/call` under the normal permission, timeout, cancellation, output policy, and audit path.
- Guardrails block repeated exact failures and repeated same-signature completed calls inside one turn. File-observing signatures for `search_files`, `read_file`, `patch`, and `write_file` include the session workspace epoch, so a successful workspace mutation can make the same file read/search meaningful again.
- Role identity is stored as `data/roles/<roleId>/SOUL.md`. Stable Markdown memory is stored per role under `data/roles/<roleId>/memory/MEMORY.md` and `data/roles/<roleId>/memory/USER.md`, with legacy default-role migration from `data/memory/`.
- Task execution now goes through a `TaskRunner` boundary. `InProcessTaskRunner` preserves the current thread-pool behavior, while the state machine, retry/recovery logic, cancellation, and event publishing remain in `TaskWorker`.
- `ContextAssembler` builds API-call-time context each turn from the active runtime memory provider. Stable Markdown memory (`MEMORY.md` / `USER.md`) remains in the prompt layer. `configs/runtime.yaml` defaults to `memory.provider: mem0_like_runtime`, which keeps the hybrid provider's summary/current-session FTS/global FTS lanes and treats durable long-term facts as typed `memory + metadata + history` records. Set `memory.provider: hybrid_runtime` for the previous provider, or `builtin_runtime` for the original local provider exactly as before. If an external provider is configured, it replaces the runtime provider for prefetch and memory-tool exposure so the model sees only one memory backend. Active session plan items, todos, task state, and recent terminal task outcomes are runtime state, not durable memory. Relevant prior snippets go into a sanitized `<memory-context>` block on the current user message, external provider results go into `<external-memory-context>`, and `memory.context.used` reports which sources were used, including `retrievalProvider` metadata. These injected blocks are not persisted. The runtime keeps the most recent `context.diagnosticsLimit` diagnostics per session in an in-memory ring buffer for developer inspection.
- Conversation summaries are persisted in SQLite through `GET /memory/summary` and `POST /memory/summary`, injected as reference-only context, and refreshed by threshold-based compaction or manual `POST /memory/compact`. Raw history loaded for each model call uses the latest configured conversation turns after the summary boundary rather than a raw message count.
- Structured `memory_items` store durable `user` / `agent` / `project` facts in SQLite using a Mem0-like shape: `content`, `memoryType`, JSON `metadata`, `contentHash`, source ids, access stats, timestamps, and soft deletion. They expose explicit HTTP add/list/delete/history APIs, are searchable through the read-only `search_memory_items` tool, can be added/replaced/forgotten through approval-gated tools, and inject a small active set into model context as reference-only `<memory-items>`.
- Memory query tokenization uses `jieba` plus bounded CJK n-gram fallback. `messages_fts` indexes the original message text plus tokenized terms so Chinese queries can match Chinese historical messages by segmented terms; search results still return the original message content. Structured `memory_items` reuse the same tokenizer for LIKE-based fact filtering.
- Memory review candidates are persisted in SQLite as an audit and exception-handling queue. `POST /memory/review/run` asks the provider to propose candidates from recent messages, filters secret-like content, temporary debug/run state, uncertain claims, overly specific local/cache/generated paths, and obvious `user` / `agent` / `project` scope mismatches, then automatically promotes safe candidates into `memory_items` while marking their candidate records as `accepted`. `GET/POST /memory/review/candidates` still lists or creates candidates, `POST /memory/review/accept` promotes a pending candidate into `memory_items`, and `POST /memory/review/reject` rejects one without writing durable memory. Rejected candidates suppress later identical suggestions for the same session/scope/content.
- Automatic memory review can run after a completed turn once the message threshold is met. It is cooldown-gated and writes safe durable memory items directly after pre-persistence safety checks.
- The desktop bridge exposes the memory review queue over WebSocket events. The renderer lists pending candidates, supports manual Review, and sends Accept / Reject actions back through the server bridge for manually created, old, or future exception-path candidates.
- `search_memory` has a per-tool model-output policy that keeps match metadata while limiting model-context result count and snippet length.
- `read_session_messages` has a per-tool model-output policy that keeps transcript metadata while limiting model-context message count and per-message preview length.
- `search_memory_items` has a per-tool model-output policy that keeps structured fact metadata while limiting model-context item count and content length.
- `search_files` has a per-tool model-output policy that keeps search metadata while limiting model-context result count and preview length.
- `read_file` does not use hidden runtime compression. It returns a caller-controlled `startLine` / `lineLimit` text window with line numbers, `totalLines`, and `hasMore` so the model can continue reading explicitly. Non-text files are identified as `image`, `pdf`, `binary`, or `unknown` and return an unsupported response with a next-tool hint.
- `patch` follows the Hermes-style edit path: exact `oldText` / `newText`, unique-match default, optional `replaceAll`, restricted generated directories, UTF-8 text-only writes, and diff output for review.
- `write_file` is the whole-file write path: new UTF-8 text files by default, explicit `overwrite=true` for replacement, restricted generated directories, text-extension checks, size limits, parent directory creation inside the workspace, and diff output for review.

`workspace_epoch` is a session-local monotonic counter, not a filesystem hash. It starts at `0` for each runtime session and advances after `patch` or `write_file` succeeds with `changed: true`. The current purpose is guardrail invalidation and diagnostics: repeated file reads/searches are only considered no-progress repeats within the same epoch. Future shell or external mutation tools should conservatively advance the same counter when they can change workspace files.

`search_files` is the only project file search tool exposed by the Python registry.

## Provider Reasoning

Model thinking controls use a provider-neutral config surface (`thinkingEnabled`, `reasoningEffort`) and a provider-specific translation layer in `provider_reasoning.py`.

- DeepSeek V4 / `deepseek-reasoner` receive `thinking: { type: "enabled" | "disabled" }` and, when enabled, `reasoning_effort: low|medium|high`.
- DeepSeek tool-call loops preserve returned `reasoning_content` on assistant tool-call history and replay it on the next provider request. If a legacy DeepSeek tool-call message lacks the field, the runtime pads it with a single space because empty strings can be rejected by DeepSeek.
- Non-DeepSeek providers have `reasoning_content` and internal display-only `reasoning` fields stripped before API calls, so DeepSeek-specific replay metadata cannot leak into OpenAI/OpenRouter/Gemini-style requests.
- Desktop Main UI may render `assistant.reasoning.delta` as a collapsed reasoning panel. Companion ignores that event by design.

## Adding A Tool

Adding a simple local tool is intentionally lightweight:

1. Add a Python handler in a focused module under `tools/`, such as `tools/reminders.py`.
   - Signature can be `handler(args)` for simple tools.
   - Use `handler(args, context)` if the tool needs `ToolContext`, for example to check `context.is_cancelled()` in a long loop.
   - Return a JSON-serializable `dict`. Return `{"error": "..."}` for expected tool-level failures.
2. Add a `ToolSpec` next to the handler and register it from `tools/__init__.py`.
   - `name` must match the function schema name.
   - `permission` should usually be `allow` for safe read-only low-risk tools, `ask` for persistent mutations, workspace-external access, script/shell execution, network or user-visible external actions, and `deny` only when disabled by default.
   - `schema` should be OpenAI-compatible function metadata with clear parameter descriptions.
3. Add or update the matching entry in `../../configs/tools.yaml`.
   - `enabled: true|false`
   - `permission: allow|ask|deny`
4. Add focused tests in `../../tests/test_tool_runtime.py` or `../../tests/test_python_agent_runtime.py`.
   - Test handler success/failure.
   - Test config permission behavior if the permission matters.
   - Add a per-tool model-output policy in `tool_runtime/registry.py` only if the result can become large or noisy.

For a simple read-only tool, this is usually a small change: one handler, one `ToolSpec`, one config entry, and one or two tests. Tools that touch the network, filesystem writes, subprocesses, credentials, or long-running work need more care: use `ask`, enforce workspace/path constraints, support cancellation if possible, and keep model output compact.

## Run

```bash
python packages/amadeus/server.py
```

Default endpoint:

```text
http://127.0.0.1:8790
```

## Runtime Memory Provider Configuration

Turn-time memory assembly is configured in `configs/runtime.yaml`:

```yaml
memory:
  provider: mem0_like_runtime
  globalRetrievalFallback: true
  vectorRetrieval: true
  vectorCandidateLimit: 80
```

- `mem0_like_runtime` is the default provider. It keeps the hybrid retrieval behavior and records long-term memory as typed items with metadata, access stats, history, and optional BGE-M3 vector embeddings.
- `hybrid_runtime` is the preserved previous provider. It keeps summaries, accepted structured memory, current-session FTS snippets, and memory tools, then optionally fills sparse retrieval slots from global SQLite FTS.
- `builtin_runtime` is the preserved original provider. Set `memory.provider: builtin_runtime` to disable the new hybrid lane and use the previous behavior exactly.
- `memory.globalRetrievalFallback: false` keeps `mem0_like_runtime` or `hybrid_runtime` selected but disables cross-session FTS fallback.
- `memory.vectorRetrieval: true` lets `mem0_like_runtime` use local BGE-M3 dense vectors for typed long-term memory when the embedding provider is configured and deployed. If BGE-M3 is unavailable, runtime recall automatically falls back to the SQL/FTS memory item path.
- `memory.vectorCandidateLimit` bounds the durable memory item candidate set used by hybrid vector/text ranking.
- Environment overrides are available through `AMADEUS_MEMORY_PROVIDER`, `AMADEUS_MEMORY_GLOBAL_RETRIEVAL_FALLBACK`, `AMADEUS_MEMORY_VECTOR_RETRIEVAL`, and `AMADEUS_MEMORY_VECTOR_CANDIDATE_LIMIT`.

The BGE-M3 embedding layer is local-first. `GET /memory/embedding/config` reports dependency/model deployment state plus vector index coverage. `POST /memory/embedding/deploy` installs/configures the local `BAAI/bge-m3` provider through FlagEmbedding, and `POST /memory/embedding/backfill` embeds stale or missing `memory_items` rows into the SQLite `memory_item_embeddings` derived table.

## Current HTTP API

- `GET /health`
- `GET /runtime/health`：structured local health checks for runtime, model config, memory DB, tools, Live2D, audio, and effective config.
- `GET /runtime/config`：read the active provider/model config, provider presets, Live2D model config, and audio config for the Main UI configuration center. Model config includes `thinkingEnabled` and `reasoningEffort`.
- `PUT /runtime/config`：persist provider/model settings to `.env` and `configs/providers.yaml`, rebuild the active model config, and apply provider-specific reasoning settings.
- `GET /runtime/feedback?sessionId=default`：query the Python-side harness feedback snapshot for desktop capabilities and audio playback state.
- `GET /tools/list`：Python-owned source for effective tool permission state and enabled schemas.
- `GET /tools/audit?sessionId=default&toolName=search_files&decision=finished&ok=true&limit=100`：query persisted tool audit records for diagnostics.
- `POST /runtime/config/reload`：reload effective memory/context runtime config from YAML plus environment overrides.
- `POST /runtime/feedback`：record desktop capability and runtime audio playback feedback for Python-side harness policy; may return emitted harness events such as playback-driven `character.behavior`.
- `POST /agent/turn`
- `POST /tools/execute`：compatibility execution endpoint for direct tool diagnostics; normal turns execute tools inside Python `AgentRuntime`.
- `POST /tools/permission`
- `GET /memory/count?sessionId=default`
- `GET /memory/messages?sessionId=default&limit=40`
- `GET /memory/context/diagnostics?sessionId=default&limit=10`：query recent in-memory context assembler diagnostics for developer inspection.
- `GET /memory/search?sessionId=default&query=hello&limit=10`
- `GET /memory/items?scope=user&memoryType=preference&query=preference&limit=20`
- `GET /memory/items/history?memoryItemId=1&limit=50`
- `GET /memory/embedding/config`：inspect local BGE-M3 embedding configuration, optional FlagEmbedding dependencies, model cache, deployment state, vector index coverage, and active backfill status.
- `GET /memory/summary?sessionId=default`
- `GET /memory/review/candidates?sessionId=default&status=pending&limit=50`
- `GET /memory/review/jobs?sessionId=default&limit=20`
- `GET /sessions/{id}/plan`：load the SQLite-backed session task plan for desktop refresh/session restore.
- `PUT /sessions/{id}/plan`：replace or merge the session task plan with `{ "items": [...], "merge": false }`.
- `GET /scheduled-jobs?sessionId=companion:default&activeOnly=true&limit=20`：list session-scoped scheduled companion messages.
- `GET /scheduled-jobs/{id}/events`：inspect scheduled message lifecycle events.
- `POST /scheduled-jobs`：create a scheduled message with `{ "sessionId": "...", "title": "...", "message": "...", "schedule": "every 10s", "repeatCount": 4 }`. Supported schedules include `10s`, `30m`, `every 2h`, common five-field daily/weekly/monthly cron shapes, and ISO timestamps.
- `POST /scheduled-jobs/{id}/pause|resume|cancel`：manage scheduled messages. Fired jobs persist an assistant message and broadcast `assistant.message`; lifecycle changes broadcast `scheduled.updated`.
- `GET /todos?sessionId=companion:default&activeOnly=true&limit=100`：list persistent session todo items.
- `PUT /todos`：replace or merge the session todo list with `{ "sessionId": "...", "merge": true, "todos": [{ "id": "a", "content": "Buy tea", "status": "pending" }] }`. The `todo` tool exposes the same read/write behavior to the agent.
- `POST /memory/messages`
- `POST /memory/items`：add a typed long-term memory item; accepts optional `memoryType` and `metadata`.
- `POST /memory/items/delete`
- `POST /memory/embedding/deploy`：configure and start local BGE-M3 dependency/model deployment.
- `POST /memory/embedding/cancel`：cancel an active local BGE-M3 deployment.
- `POST /memory/embedding/backfill`：embed missing/stale long-term `memory_items` through the configured local BGE-M3 provider and update `memory_item_embeddings`.
- `POST /memory/review/candidates`
- `POST /memory/review/accept`
- `POST /memory/review/reject`
- `POST /memory/review/run`
- `POST /memory/summary`
- `POST /memory/compact`
- `POST /memory/reset`
- `POST /audio/speak`
- `POST /audio/transcribe?format=webm`：transcribe binary microphone audio through the configured ASR provider.
- `GET /audio/files/{relativePath}`
- `GET /live2d/config`
- `GET /live2d/models`
- `POST /live2d/select`
- `GET /live2d/models/{relativePath}`

## Notes

- `/agent/turn` returns an NDJSON event stream.
- Python runtime coverage includes deterministic `AgentRuntime` unit tests and local HTTP sidecar tests in `tests/test_python_runtime_http.py`. Full desktop behavior is covered higher up by server/desktop tests.
