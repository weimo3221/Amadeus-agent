# Amadeus 成熟 Agent 升级计划

Last updated: 2026-07-16

## 目标判断

Amadeus 现在已经有一个能跑的桌面 MVP：Electron 透明窗口、本地 Live2D 模型库、WebSocket 事件、OpenAI-compatible 模型调用、SQLite memory、工具调用、工具权限 prompt、Python sidecar、运行时 TTS fallback 都已经打通。当前 preferred turn path 已经迁到 Python：`apps/server` 主要作为 WebSocket/HTTP bridge，`packages/amadeus/agent.py` 负责单轮对话、模型调用、记忆读写、工具决策、工具执行、权限请求和 runtime event streaming。连桌面显示的 memory count 和 session reset 也已经改成由 Python runtime 拥有，bridge 只做代理。

它的问题不是“没有功能”，而是核心 agent runtime 仍在从 MVP 走向成熟：provider abstraction、ToolRuntime、Memory v2 context assembly、本地 Live2D/audio 基础已经落地，但 skills、任务调度、subagent、eval、完整 harness 能力和更深的桌面 E2E 仍处在早期或占位状态。

如果目标是优化成接近 `deepagents` 与 `hermes-agent` 那种成熟 agent，正确路线不是直接堆功能，而是把 Amadeus 重构成一个可组合 harness：

- 通用 agent core 负责计划、模型、记忆、工具、权限、任务、观察、恢复。
- Live2D 和 audio 是 Amadeus 的卖点，但它们应该作为可安装 harness 挂到 agent 上，而不是写死在 agent brain 里。
- Electron desktop 只负责真实渲染、播放、采集输入和用户授权 UI。
- TypeScript server 逐步收缩为本地 transport bridge。
- Python runtime 成为唯一 agent owner。

一句话：Amadeus 应该变成“带 Live2D/音频身体的成熟 agent harness”，而不是“有 agent 能力的 Live2D 聊天壳”。

## 当前状态与主要差距

### 当前已经具备

- `apps/desktop`
  - Electron + Vite 桌面窗口。
  - Live2D 模型渲染、表情/动作别名、指针跟随、点击反应。
  - 聊天 UI、状态显示、工具权限 prompt、语音开关。
  - `speechSynthesis` fallback 和简单嘴型 loop。
- `apps/server`
  - HTTP `/health`、本地模型/音频静态服务、Live2D 模型切换接口和 WebSocket `/ws`。
  - relay desktop `user.message` 到 Python `/agent/turn`。
  - relay Python NDJSON runtime events 回桌面。
  - forward desktop `tool.permission.response` 到 Python `/tools/permission`。
  - Python runtime 不可用时返回显式 runtime error，而不是运行第二套 TS agent loop。
- `packages/amadeus`
  - Python `server.py` sidecar。
  - `GET /runtime/health` 结构化健康检查，覆盖 runtime、model config、memory DB、tools、Live2D、audio、effective config。
  - Python `agent.py` 是当前 preferred turn path。
  - Python 工具：`get_current_time`、`roll_dice`、`search_files`、`read_file`、`patch`、`write_file`。
  - Python ToolRuntime：registry/config loading、permission metadata、structured `ToolResult`、timeout/cancellation、result compression、`search_files` per-tool result policy、`read_file` explicit line-window reads and non-text kind reporting、`patch` exact-replacement edits、`write_file` whole-file text writes、repeated-failure/no-progress guardrail。
  - Python `model.py` 已成为 OpenAI-compatible provider 边界，`context.py` 已成为 Memory v2 context assembler，`audio.py` 和 `live2d.py` 已提供本地运行时边界。
  - TypeScript `events.ts` 与 `tools.ts` bridge/diagnostics scaffold；`tools.ts` 已收缩为 Python tool HTTP client，不再镜像具体工具实现。

### 成熟 agent 差距

- Python 已经 owns preferred single-turn loop；provider 调用和 context assembly 已拆出第一版模块，但还没有形成成熟 runtime/model/context/harness 包结构。
- ToolRuntime 已有 registry、permission metadata、structured result、SQLite 持久化审计记录、超时、cooperative cancellation、结果压缩、`search_files` per-tool result policy、`read_file` explicit line-window reads and non-text kind reporting、`patch` exact-replacement edits、`write_file` whole-file text writes、重复失败 guardrail 和针对搜索/读文件/patch/write_file 的语义级 no-progress detection。
- Memory v2 核心已落地：summary、SQLite FTS、stable Markdown memory、structured memory_items、review candidates/jobs、自动 review gate、token-budget compaction、API-call-time context assembler 和 `memory.context.used` diagnostics。下一步是质量调优和策略收敛，不是继续补基础存储。
- Live2D/audio 已有第一版 harness/runtime 边界：Live2D harness 映射 `assistant.state` 和 `audio.playback-*` 到 `character.behavior`，本地模型库支持切换，Python audio 支持 GPT-SoVITS 配置和 macOS `say` fallback，desktop capability/playback feedback 已回传 Python。缺口是 audio harness、丰富命令和 amplitude/phoneme 级 lipsync。
- task/job 体系已具备持久化状态、attempt/lease/heartbeat、dependency graph、artifact handoff、subprocess + copy isolation、独立 SQLite lease supervisor、durable process registry、进程接管、资源限制、结构化健康状态，以及第一版受限 isolated child model loop；多 child 自主编排和更完整 graph flow 仍未收口。
- runtime contract eval 已通过 `npm test` 覆盖十个确定性场景，并保留 typecheck/unit/bridge/renderer/Electron E2E；仍缺模型质量、长时间 supervisor/worker crash-restart soak、Memory 与 Live2D/audio interaction 评估。
- 配置与 provider 体系仍然偏浅。`configs/providers.yaml` 已经成为第一版 LLM/TTS 配置入口，但还缺 provider profile、fallback/routing、运行时切换和更丰富的错误恢复策略。

## 对照项目可借鉴点

### 从 deepagents 借鉴

`deepagents` 的关键不是某个工具，而是 harness 装配方式：

- `create_deep_agent(...)` 把 model、tools、middleware、subagents、skills、memory、permissions、backend、checkpointer 统一装配。
- middleware 栈提供可组合能力：todo、filesystem、subagent、summarization、skills、memory、human-in-the-loop。
- harness profile 按模型/provider 调整 prompt、工具描述、middleware、默认 subagent。
- subagent 通过 `task` 工具进入主 agent，拥有独立上下文，并可继承或覆盖工具、模型、权限。
- memory 是 always-loaded context，skills 是 on-demand procedural memory。
- permissions 在工具/后端层 enforcement，而不是只靠模型自律。
- better-harness 的思路是把 prompt、tools、skills、middleware registration 当成可评估、可优化的 harness surfaces。

Amadeus 应借鉴这些结构，但不要原样引入全部复杂度。第一版只需要一套轻量 Python harness API。

### 从 hermes-agent 借鉴

`hermes-agent` 的强项是长期运行和工程可靠性：

- 多 provider transport：把 provider-specific message/tool/response 转换从 agent loop 拆开。
- 工具执行器支持顺序/并发、工具进度 callback、中断、心跳、超时、结果持久化。
- 工具循环 guardrail：检测重复失败、同结果读操作、无进展循环，给模型 warning 或 hard stop。
- 工具集/toolset 按平台、配置、权限启停。
- session search、memory、skills、skill_manage、cronjob、delegate_task 形成长期 agent 能力闭环。
- background review 在会话后异步更新 memory/skill，不污染主对话。
- kanban/cron 体现了任务持久化、claim lock、heartbeat、事件历史、失败恢复。
- MCP 和插件系统让能力通过 adapter 注入，而不是写死在 agent loop。

Amadeus 应借鉴可靠性机制：tool guardrail、job store、session search、background memory review、provider transport、plugin/harness 装配。

## 当前 Agent 补齐路线

对照 `deepagents` 和 `hermes-agent` 后，Amadeus 当前最需要补的不是“会调用工具”，而是“任务化、持久化、可取消、可恢复、可委托、可后台运行”的执行系统。现阶段不要先搬 Hermes Kanban swarm，也不要一次性引入 deepagents 的完整 LangGraph/harness 栈；应先把当前 turn/session 的状态做稳，再逐层扩展。

### 已进入当前主线

- Session plan 持久化：第一版使用 SQLite JSON 表即可，形态为 `session_plans(session_id TEXT PRIMARY KEY, items_json TEXT, updated_at TEXT)`。后续如果需要单项审计、依赖、claim lock，再拆成 `session_plan_items`。
- Context assembly 注入 active plan：只注入 `pending` / `in_progress`，不要把 `completed` 全塞回模型上下文。
- Runtime event 同步 plan：通过 `task.plan.updated` 发给桌面；Main UI 负责完整清单显示，Companion 只显示必要的短状态。
- HTTP 查询/恢复接口：`GET /sessions/{id}/plan` 和 `PUT /sessions/{id}/plan` 用于刷新、切 session 和重启恢复；WebSocket 继续只承载 runtime event。

### 下一阶段优先补齐

1. 轻量任务系统，而不是完整 Kanban：
   - 已完成第一片：`tasks` / `task_events` SQLite 存储，`queued` / `running` / `blocked` / `succeeded` / `failed` / `cancelled` 状态，旧 `done` 归一化到 `succeeded`，`GET /tasks`、`POST /tasks`、`GET /tasks/{id}/events`、`POST /tasks/{id}/cancel`，TypeScript bridge 代理，以及 Main UI active task 状态面板。
   - 已完成第二片：简单 in-process worker、queued → running → succeeded/failed/cancelled 真实状态流转、worker claim/heartbeat、运行中 backing turn cooperative cancel、模型可调用的 `create_task` / `list_tasks` / `cancel_task` 工具，以及 worker 状态的 `task.updated` runtime event 推送广播。
   - 已完成第三片：参考 Hermes Kanban 的可靠性机制，补充 `attemptCount` / `maxAttempts` / `nextRunAt`、失败 retry scheduling、超过尝试上限后 terminal `failed`、启动时 stale `running` reclaim 并重新提交 runnable queued tasks。
   - 下一步：真正调度器、durable worker lease、重启后的 in-flight turn resume/checkpoint。
2. Agent turn 控制：
   - 已完成第一片：每个 running turn 有 `turn_id`，Python runtime 维护 session-scoped running turn 状态，`POST /agent/cancel` 可设置 cooperative cancel event，工具执行通过 `ToolContext.cancel_event` 接收取消信号，并发出 `agent.turn.started` / `agent.turn.cancelled`。
   - 下一步：长任务失败/刷新后的状态恢复、checkpoint/resume、permission wait 的取消唤醒、provider request 级 timeout/cancel 策略。
3. `delegate_task` MVP：
   - 已完成 isolated child 迁移：`delegate_task` 创建持久化 `delegated` task，经 `TaskWorker` 在任务专属归档 session 与 read-only `WorkerRuntimeScope` 中执行真实 model loop；不自动注入父历史、stable memory 或全局检索，只允许受控 source-session memory/file search，禁止写文件、shell、任务创建和递归 delegation，父 agent 只接收 summary/task metadata。
   - 下一步：补模型质量 eval、多 child 协作策略和异步 handoff UX，不扩宽当前 depth/tool 权限边界。
4. Agent harness/middleware 化：
   - context providers
   - tool execution middleware
   - permission middleware
   - runtime event observers
   - post-tool hooks
   - memory write hooks
   - harness/eval hooks
5. Tool executor 增强：
   - 独立 tool call 并发执行
   - per-tool timeout
   - tool result storage/offload
   - 大输出摘要化或引用化
   - before/after/error middleware
6. Context compression 加固：
   - active task/plan 只注入当前相关状态
   - completed 进入摘要或检索，不进入 active context
   - 大型工具输出用引用，不长期占上下文
   - 历史任务摘要明确避免旧任务被误当成当前任务

### 明确延后

- Hermes Kanban swarm、claim/review/synthesizer 全套流程。
- 自动 skill self-improvement。
- 可写文件的 subagent。
- 完整 MCP/plugin marketplace。
- 多平台 proactive delivery。Amadeus 第一版应优先走 Desktop Main UI 和本地 runtime events。

## 目标架构

### 分层

```text
apps/desktop
  - Live2D 渲染 adapter
  - audio playback / ASR capture adapter
  - permission UI
  - status/tool/memory/task UI

apps/server
  - WebSocket/HTTP bridge
  - desktop event validation
  - Python runtime stream relay
  - static asset/audio/model file serving fallback

packages/amadeus
  agent/
    runtime.py
    loop.py
    context.py
    events.py
    state.py
  models/
    base.py
    openai_compatible.py
    transports.py
  tools/
    registry.py
    permissions.py
    executor.py
    guardrails.py
    builtin/
  memory/
    store.py
    summaries.py
    profile.py
    session_search.py
    review.py
  harness/
    base.py
    registry.py
    profiles.py
    live2d.py
    audio.py
    desktop.py
  skills/
    loader.py
    invocation.py
    manager.py
  tasks/
    store.py
    scheduler.py
    workers.py
  server.py
```

### Runtime ownership

Python runtime 应提供新的主接口：

```text
POST /agent/turn
  input: sessionId, text/inputMode, clientCapabilities, runtimeOptions
  output: event stream or event batch

POST /agent/cancel
  input: sessionId, turnId

POST /tools/permission
  input: requestId, approved, editedArgs?

GET /sessions/{id}/state
GET /tasks
GET /memory/profile
```

TypeScript server 的长期职责应变成：

- 接收 desktop WebSocket event。
- 转发到 Python `/agent/turn`。
- 把 Python event stream 原样转成 `packages/amadeus/events.ts` 定义的 runtime event。
- 管理连接、重连、静态文件、CORS、desktop-only permission UI transport。

LLM 调用、工具循环、记忆、行为策略、audio/live2d 命令生成都应迁入 Python。

## Harness 设计

### 为什么 Live2D/audio 要做成 harness

Live2D 和 audio 是 Amadeus 的产品差异点，但它们本质上不是普通工具：

- 工具是 agent 对外部世界的动作，通常由模型按需调用。
- Live2D/audio 是 agent 的身体、感知与表达通道，应该贯穿每个 turn。
- 它们需要状态反馈：播放开始/结束、嘴型参数、ASR partial、模型可用 motion/expression、用户点击。
- 它们需要低延迟 event，不适合每次都走 LLM tool call。

因此应定义 `Harness` 插槽。一个 harness 可以：

- 注册系统 prompt 片段。
- 注册 runtime middleware。
- 注册工具或设备命令。
- 消费输入事件。
- 观察 agent state/tool state。
- 发出 runtime events。
- 声明 capabilities 和配置 schema。

### Harness 基础合同

建议在 `packages/amadeus/harness/base.py` 定义：

```python
from dataclasses import dataclass, field
from typing import Protocol, Any

@dataclass(frozen=True)
class HarnessCapability:
    name: str
    version: str
    events_in: list[str] = field(default_factory=list)
    events_out: list[str] = field(default_factory=list)
    tools: list[str] = field(default_factory=list)

@dataclass
class HarnessContext:
    session_id: str
    turn_id: str | None
    runtime_state: dict[str, Any]
    client_capabilities: dict[str, Any]

class Harness(Protocol):
    name: str

    def capabilities(self) -> HarnessCapability: ...
    def system_prompt(self, context: HarnessContext) -> str | None: ...
    def before_turn(self, context: HarnessContext, user_event: dict[str, Any]) -> list[dict[str, Any]]: ...
    def observe_event(self, context: HarnessContext, event: dict[str, Any]) -> list[dict[str, Any]]: ...
    def after_turn(self, context: HarnessContext, result: dict[str, Any]) -> list[dict[str, Any]]: ...
```

第一版不要过度抽象。先支持同步方法，内部可返回事件数组。后续再扩展 async streaming。

### Live2D harness

`Live2DHarness` 应负责把 agent 状态和音频播放反馈转成 character commands，而不是由 server 硬编码。播放反馈到表情/动作的第一版映射已放在 `configs/harnesses.yaml` 的 `live2d.audioPlaybackBehaviors` 下：

输入：

- `assistant.state`
- `assistant.delta`
- `tool.started`
- `tool.finished`
- `error`
- `desktop.pointer`
- `desktop.character.click`
- `audio.playback-started`
- `audio.playback-ended`

输出：

- `character.behavior`
- `character.expression`
- `character.motion`
- `character.gaze`
- `character.lipsync`

配置：

```yaml
harnesses:
  live2d:
    enabled: true
    adapter: desktop-live2d
    model:
      id: vivian
      path: models/live2d/vivian/vivian.model3.json
    expressions:
      neutral: ["neutral", "default"]
      smile: ["smile", "happy"]
      focused: ["serious", "thinking"]
      confused: ["confused", "surprised"]
    motions:
      idle: ["Idle"]
      think: ["Think", "TapBody"]
      talk: ["Talk", "TapBody"]
      nod: ["Nod", "TapBody"]
    state_map:
      thinking:
        expression: focused
        motion: think
      speaking:
        expression: smile
        motion: talk
```

实现步骤：

1. 把 renderer 中的 expression/motion alias 表复制为配置驱动。
2. desktop 启动后上报 `character.capabilities`，包括模型实际支持的 expressions/motions。
3. Python `Live2DHarness` 根据 capabilities 做 alias resolution，发 `character.behavior`。
4. renderer 只执行具体渲染，不决定语义。
5. 增加 `character.feedback`，desktop 报告 motion 是否成功、模型是否 ready。
6. 增加行为节流，避免 streaming delta 时疯狂切 motion。

### Audio harness

`AudioHarness` 应负责 TTS/ASR/lipsync/audio event 策略：

输入：

- `user.voice-start`
- `user.voice-chunk`
- `user.voice-end`
- `assistant.message`
- `assistant.state`
- `audio.playback-started`
- `audio.playback-ended`
- `audio.playback-error`

输出：

- `audio.tts-started`
- `audio.tts-ready`
- `audio.tts-fallback`
- `audio.lipsync-cues`
- `assistant.state` 或 `character.behavior` 辅助事件

配置：

```yaml
harnesses:
  audio:
    enabled: true
    tts:
      provider: gpt-sovits
      fallback: speechSynthesis
      cache_dir: packages/amadeus/assets/audio/cache
      voices:
        zh:
          name: vivian_zh
          reference_audio: D:/OtherProject/LearningLLM/dataset/.../reference.wav
        en:
          name: vivian_en
          reference_audio: D:/OtherProject/LearningLLM/dataset/.../reference.wav
    lipsync:
      mode: amplitude
      fallback_mode: timed
    asr:
      provider: none
      push_to_talk: true
```

实现步骤：

1. 保留 `speechSynthesis` fallback，但把 fallback 决策事件化。
2. 为 Python `AudioRuntime` 增加 provider registry。
3. GPT-SoVITS provider 只在外部 API 已能稳定生成 wav 后接入。
4. `audio.tts-ready` 增加 `text`, `voice`, `format`, `provider`, `cacheKey`。
5. desktop 播放后发送 `audio.playback-started/ended/error`。
6. 第一版 lipsync 用音频 amplitude 分析生成 `ParamMouthOpenY` cues；第二版再做 phoneme。
7. ASR 先做 push-to-talk event contract，再接 Whisper/faster-whisper 或系统 ASR。

## 分阶段实施计划

### Phase 1：Python AgentRuntime 接管单轮对话

状态：基本完成，后续只保留 cleanup 和模块边界拆分工作。当前实现位于 `packages/amadeus/agent.py`，通过 Python `/agent/turn` 向 TypeScript bridge 输出 NDJSON runtime events。

目标：主循环从 `apps/server/src/index.ts` 迁移到 Python，但 desktop 行为不破。

任务：

- 新增 `packages/amadeus/agent/runtime.py`。
- 实现 `run_turn(session_id, user_text) -> Iterator[AgentEvent]`。
- 新增 Python OpenAI-compatible model adapter，支持 non-stream tool decision 和 streaming final response。
- Python 读取 `configs/providers.yaml` 与 `.env`。
- Python 使用现有 `MessageMemoryStore` 保存/加载消息。
- Python 直接调用 Python tool registry。
- Python 生成现有事件：`assistant.state`、`assistant.delta`、`assistant.message`、`memory.updated`、`tool.started`、`tool.finished`、`tool.permission.request`、`character.behavior`、`audio.tts-ready`。
- TypeScript server 新增 `/agent/turn` relay path，或直接 WebSocket 收到 `user.message` 后请求 Python stream。

验收：

- 当前 desktop chat 行为不变。
- `get_current_time`、`roll_dice`、`search_files`、`read_file`、`patch`、`write_file` 仍可工作。
- 断开 Python runtime 时 server 明确报错。
- `apps/server` 不再直接调用 LLM provider。

### Phase 2：统一 ToolRuntime

状态：进行中。已完成 Python `ToolRegistry`、`configs/tools.yaml` loading、`ToolContext` / `ToolResult` 第一版、duration/failure code、first-pass timeout handling、cooperative cancellation、result preview/compression、`search_files` per-tool result policy、`read_file` explicit line-window reads and non-text kind reporting、`patch` exact-replacement edits、`write_file` whole-file text writes、permission-aware schema projection、SQLite-backed audit records、repeated-failure guardrail、first-pass no-progress detection，以及针对搜索/读文件/patch/write_file 的语义级 no-progress detection。剩余重点是更多高容量工具的 result policies 和随新工具继续调优 no-progress 策略。

目标：工具从“函数字典 + TS fallback”升级为可审计 runtime。

任务：

- 完善 `ToolContext`、`ToolResult`、`ToolPermissionPolicy`。
- 工具 schema、权限、display name、handler 继续收敛到 Python-owned。
- TS `packages/amadeus/tools.ts` 只保留 bridge 类型与 Python `/tools/list`、`/tools/execute` client。
- 加入超时、异常归一化、结果大小限制、可持久化工具审计表。
- 加入 Hermes 风格 `ToolCallGuardrailController`：
  - exact same args repeated failure warning/block。
  - idempotent same result no-progress warning。
  - mutating tool hard approval。
- 加入工具执行事件的 duration、error code、preview。
- `configs/tools.yaml` 改为 Python 加载，支持 toolset/category。

验收：

- 工具 allow/ask/deny/disabled/unknown 都有单元测试。
- repeated failing tool 不会无限循环。
- 工具结果过大时被压缩或落盘，并给模型可用摘要。

### Phase 3：Memory v2

目标：从 raw message replay 变成长期 agent 记忆。

数据模型：

- `messages`：原始会话消息。
- `conversation_summaries`：按 session/turn range 存摘要。
- `user_profile_facts`：稳定用户偏好、身份、工作流。
- `memory_items`：可检索事实，含 scope、confidence、source、created_at、updated_at。
- `session_index`：跨会话搜索索引，先 SQLite FTS5，后续可接向量库。

任务：

- 已完成第一片：`messages_fts`、`GET /memory/search`、`search_memory` 工具，以及每轮自动 `<memory-context>` prefetch 注入。
- 已完成 stable memory 第一片：role-scoped `data/roles/<roleId>/memory/MEMORY.md`、`data/roles/<roleId>/memory/USER.md`、`read_memory`、`update_memory`，并保留默认 role 对旧 `data/memory/` 的迁移兜底。
- 已完成 role identity 第一片：role-scoped `data/roles/<roleId>/SOUL.md`、启动/创建 seed、per-session prompt 加载、`update_current_role_identity` ask-tool、`/roles/{roleId}/identity` API。
- 已完成 conversation summary 第一片：SQLite `conversation_summaries`、覆盖范围元数据、`GET /memory/summary`、`POST /memory/summary`、`POST /memory/compact`、阈值触发 compaction、context 注入。
- 已完成 structured memory 第一片：SQLite `memory_items`、`user` / `agent` / `project` scope、显式 add/list/delete HTTP API；长期结构化记忆默认不做 `<memory-items>` context 注入，读取走 `search_memory_items` 工具。
- 已完成 explicit structured memory tools：`search_memory_items` 只读检索 durable facts，`memory_add` / `memory_replace` / `memory_forget` 通过 `ask` 权限写入、替换或删除单条 durable fact，并带重复检测、来源 session 元数据、模型输出裁剪和 no-progress guardrail。
- 已完成 memory review candidate 审计队列第一片：SQLite `memory_review_candidates`、`pending` / `accepted` / `rejected` / `superseded` 状态、候选查询/创建 API、安全候选自动 accepted 并提升到 `memory_items`、accept 可处理 pending 例外候选、reject 不写入 durable memory，且 rejected 候选会 suppression 后续同 session/scope/content 的重复建议。
- 实现 context assembler：
  - system prompt。
  - character persona。
  - active harness prompt。
  - recent messages。
  - session summary。
    - user profile / structured memory items。
  - relevant retrieved memories。
  - tool state/task state。
- 已完成 token-budget-aware summary compaction 第一片：runtime 使用 tokenizer-free 估算检查 context budget，默认值集中在 `configs/runtime.yaml`，并仍可通过 `AMADEUS_CONTEXT_MAX_TOKENS`、`AMADEUS_CONTEXT_COMPACTION_TRIGGER_RATIO` 和 `AMADEUS_CONTEXT_RECENT_MESSAGE_TARGET_RATIO` 覆盖；预算压力会强制总结旧消息并动态缩小 recent-turn keep window，provider context overflow 会触发一次 compact-and-retry fallback。
- 继续扩展 explicit structured memory 工具：
  - 已完成：`search_memory_items`、`memory_add`、`memory_replace`、`memory_forget`。
- 实现 background memory review runner：
  - 已完成手动触发第一片：`POST /memory/review/run` 读取最近消息、已有 durable memory 和 pending candidates，生成候选审计记录，并将通过 safety/scope filter 的安全候选自动提升到 `memory_items`。
  - 已完成自动调度第一片：每个 turn 的主回复发出后，按 message-count threshold、success/failure cooldown 和 no-new-message gate 判断是否运行 review。
  - 自动调度会自动提升安全候选；低质量、重复或不安全候选会被 suppressed，pending 队列保留给手动创建、旧数据和后续例外路径。
  - 已完成 job observability 第一片：SQLite `memory_review_jobs` 记录每次 manual/auto review 的 trigger、`running` / `completed` / `skipped` / `failed` 状态、skip reason/error、source message range/count、proposed/saved/suppressed candidate counts 和 duration；`GET /memory/review/jobs` 与 WebSocket `memory.review.jobs` 会把最近 job 暴露给桌面端。
  - 严禁保存 API key、临时状态和敏感内容。
- `session_search` 支持跨会话召回。

验收：

- 重启后能记住用户长期偏好。
- 上下文不会无限增长。
- 用户说“记住/忘记”有明确工具路径和 UI 反馈。

### Phase 4：Live2D/audio harness 化

目标：把卖点变成正式 harness，而不是 renderer/server 硬编码。

任务：

- 新增 `packages/amadeus/harness/base.py`、`registry.py`、`live2d.py`、`audio.py`。
- 新增 `configs/harnesses.yaml`。
- desktop 启动后发送：
  - `desktop.capabilities`
  - `character.capabilities`
  - `audio.capabilities`
- Python runtime 根据 harness registry 装配 prompt fragment、事件观察器和输出事件。
- 移除 server 中硬编码的 `character.behavior` 选择。
- Live2D 行为从配置和 harness state map 生成。
- audio provider registry 支持：
  - `none`
  - `speech_synthesis_fallback`
  - `gpt_sovits_http`
  - future `piper` / `openai_tts` / `azure`
- 利用已落地的 playback feedback 事件闭环继续扩展 audio harness 和 lipsync 策略。

验收：

- 禁用 live2d harness 后，agent 仍能作为普通 chat agent 运行。
- 启用 live2d harness 后，thinking/tool/speaking/error 状态都能驱动角色。
- 禁用 audio harness 后不影响文本回复。
- TTS 失败时 desktop fallback 可观测且不重复播放。

### Phase 5：Skills 与 procedural memory

目标：让 agent 能复用工作流，而不是每次重新推理。

任务：

- 采用 `skills/<category>/<skill-name>/SKILL.md` 结构。
- 实现 `skills_list`、`skill_view`、`skill_run`。
- 支持技能 frontmatter：name、description、platforms、tools、harnesses、env。
- 支持 skill bundles。
- 支持 agent 通过 `skill_manage` 创建/修补技能，但默认 `ask` 权限。
- 建立 Live2D/audio 专属技能：
  - `character-smalltalk`
  - `voice-reaction`
  - `desktop-companion`
  - `anime-resource-search` 如果这是项目实际使用场景。

验收：

- 技能按需加载，不把所有 skill 内容塞进 system prompt。
- 技能可以声明依赖某个 harness，例如需要 `audio` 才启用。
- 技能管理有路径安全和审计。

### Phase 6：任务、调度、proactive agent

目标：从被动聊天升级为长期桌面助手。

任务：

- 新增 `tasks` SQLite 表：
  - id、title、body、status、priority、session_id、created_at、updated_at、due_at、claim_lock、last_heartbeat、attempt_count、max_attempts、next_run_at、result、error。
- 新增 `task_events` 和 `task_runs`。
- 实现 reminder/scheduler：
  - `reminder_create`
  - `reminder_list`
  - `reminder_cancel`
  - `daily_brief`
- 实现 background worker：
  - 领取任务。
  - 心跳。
  - 失败重试。
  - 完成后发 desktop notification/event。
- desktop 增加紧凑任务状态 UI。
- Live2D harness 对 proactive event 做自然表现：轻微动作、提示气泡、可静音。

验收：

- reminder 重启后仍存在。
- 到点后 agent 可以通过 Live2D/audio 主动提醒。
- 用户可暂停 proactive 行为。

### Phase 7：Subagent / delegation

目标：处理复杂任务时隔离上下文，避免主会话膨胀。

任务：

- 新增 `delegate_task` 工具。
- 子 agent spec 支持：
  - name
  - description
  - model
  - tools/toolsets
  - harness access
  - permissions
  - max_turns
  - output_schema
- 第一批 subagent：
  - `researcher`
  - `filesystem-worker`
  - `memory-curator`
  - `voice-script-writer`
  - `character-behavior-tuner`
- 子 agent 默认不能直接控制 Live2D/audio，只能返回建议，由主 agent/harness reconciler 决定是否表达。

验收：

- 子任务结果能回到主线程。
- 子 agent 工具权限可继承或收紧。
- 子 agent 不污染主会话 raw history。

### Phase 8：MCP、插件、provider profiles

目标：让外部能力可插拔。

任务：

- MCP client：stdio/http/sse。
- MCP tool schema 转 Python ToolSpec。
- MCP tool description 注入扫描，拦截明显 prompt injection。
- ProviderTransport：
  - `OpenAIChatCompletionsTransport`
  - `ResponsesTransport`
  - `AnthropicTransport` 可后续加。
- HarnessProfile：
  - 按 provider/model 调整 prompt suffix、tool descriptions、tool visibility、middleware。
- Plugin manifest：

```yaml
name: amadeus-live2d-vivian
type: harness
version: 0.1.0
entrypoint: amadeus_live2d_vivian:register
requires:
  harnesses: [live2d]
  events: [character.behavior, character.lipsync]
config_schema: {}
```

验收：

- 新 harness/plugin 不需要改 agent loop。
- 外部 MCP tool 默认 ask。
- provider 切换不影响工具和 harness 事件。

### Phase 9：Eval 与 harness 优化闭环

目标：避免 agent 越改越玄学。

任务：

- 新增 `tests/evals`。
- 建立固定 eval：
  - tool choice：时间问题必须用 time。
  - permission：ask 工具必须请求授权。
  - memory：偏好保存与召回。
  - Live2D：状态事件序列正确。
  - audio：TTS ready/fallback 不重复。
  - tool guardrail：重复失败被阻止。
  - context：长对话触发 summary。
- 建立 harness surfaces：
  - system prompt。
  - tool descriptions。
  - memory review prompt。
  - Live2D behavior map。
  - audio style prompt。
  - middleware registration。
- 借鉴 `better-harness`，只允许优化这些 surfaces，train/holdout 分开。

验收：

- 每个新 harness 都必须有至少一个行为 eval。
- prompt 或 behavior map 改动必须跑 eval。
- 重要回归能在 CI 或本地 `npm run test` / `python -m pytest` 捕获。

## 推荐落地顺序

最务实的顺序：

1. Python 接管 `/agent/turn`。
2. Python ToolRuntime + permissions + guardrails。
3. Memory v2：summary、profile、session_search、context assembly、diagnostics。
4. Session plan 持久化、active plan context 注入、plan runtime events、plan HTTP 恢复接口。
5. 轻量 `tasks` / `task_events` / in-process worker。
6. `/agent/cancel`、turn state、长任务恢复。
7. `delegate_task` MVP：研究/搜索型、深度 1、低并发、无写文件工具。
8. Agent harness/middleware 化。
9. Tool executor 并发、result offload、middleware。
10. Context compression 加固。
11. Skills lifecycle。
12. Reminder/task scheduler 和 proactive desktop events。
13. MCP/plugin/provider profiles。
14. Eval harness 优化闭环。

不要先做 MCP、复杂 subagent swarm、复杂 UI 或 Hermes Kanban。当前最大架构债已经从 runtime ownership 转移到 task/job/subagent/control plane：先把当前 session/turn/task 状态做成可见、可保存、可恢复，再做长期自动化。

## 长任务深度规划完整设计

Amadeus 不应把深度规划做成另一个轻量 `update_plan` 工具。`update_plan` 继续负责用户可见的 turn-local intent/progress；真正的长任务能力要落在 durable task graph、orchestrator、worker isolation、artifact handoff 和 checkpoint/resume 上。这样后续即使从 in-process runner 升级到 process runner，也不需要推翻数据模型和 UI 语义。

### 核心概念

- `PlanRun`：一次用户目标或根任务的规划快照，面向 UI 展示和对话恢复。它可以绑定 `userMessageId`、`rootTaskId`、`turnId`。
- `Task`：持久执行单元。已有 `tasks` 表继续作为主表，状态、重试、lease、review、artifact 都属于 task。
- `TaskEdge`：任务依赖边。表达 `parent -> child`、`blocks`、`requires_artifact`、`review_after` 等关系。
- `TaskAttempt`：一次 worker 执行尝试。当前 `attempt_count` 可以保留为汇总字段，但完整能力需要独立 attempt/run 表。
- `Artifact`：子任务交接物。不要只把结果塞进 `result` 文本，代码 diff、文件路径、命令输出、链接、摘要、结构化 JSON 都应有 typed artifact。
- `WorkerContext`：子 Agent 的隔离上下文，不继承父会话完整历史，只接收任务规格、验收标准、相关记忆、依赖 artifact、局部文件上下文和自身 attempt 历史。
- `Orchestrator`：负责任务分解、依赖调度、结果验收、失败重试策略、blocked/review 决策。它是 runtime 内部角色，不应先暴露成模型可随意调用的工具。

### 数据模型目标

保留现有 `tasks` 表，并向完整 task graph 演进。新增字段应向后兼容，避免引入第二套任务系统。

`tasks` 目标字段：

```text
id
session_id
root_task_id
parent_task_id
plan_run_id
plan_item_id
title
body
kind
source
worker_type
worker_profile
status
priority
due_at
ready_at
blocked_reason
review_required
acceptance_criteria_json
context_hints_json
allowed_toolsets_json
disallowed_tools_json
depends_on_policy
checkpoint_json
handoff_summary
result
error
artifacts_json
attempt_count
max_attempts
next_run_at
claim_lock
lease_owner
lease_expires_at
last_heartbeat
runner_kind
created_at
updated_at
finished_at
```

新增表：

```text
task_edges(
  id,
  from_task_id,
  to_task_id,
  edge_type,
  required_status,
  metadata_json,
  created_at
)

task_attempts(
  id,
  task_id,
  run_id,
  worker_id,
  worker_profile,
  status,
  started_at,
  heartbeat_at,
  finished_at,
  input_context_json,
  checkpoint_json,
  result,
  error,
  token_usage_json,
  tool_usage_json
)

task_artifacts(
  id,
  task_id,
  attempt_id,
  type,
  title,
  path,
  url,
  content,
  metadata_json,
  created_at
)
```

现有 `artifacts_json` 可以继续作为 API/UI 的 denormalized summary；真正的长任务执行应逐步写入 `task_artifacts`，再由 response layer 聚合成兼容旧字段。

### 内部 API

这些 API 首先是 runtime 内部服务，不急着做成模型工具。模型可以创建普通 background task，但深度分解、调度和 worker 上下文裁剪应该由 orchestrator 控制。

```python
class TaskGraphService:
    def decompose_task(self, root_task_id: str, *, strategy: str = "deep") -> TaskGraph: ...
    def add_dependency(self, from_task_id: str, to_task_id: str, edge_type: str) -> None: ...
    def list_ready_tasks(self, *, limit: int) -> list[TaskRecord]: ...
    def mark_artifact(self, task_id: str, artifact: TaskArtifact) -> TaskArtifact: ...
    def build_worker_context(self, task_id: str, *, token_budget: int) -> WorkerContext: ...
    def synthesize_parent(self, parent_task_id: str) -> TaskReview: ...
```

```python
class OrchestratorService:
    def create_root_goal(self, session_id: str, title: str, body: str, options: PlanningOptions) -> TaskRecord: ...
    def plan_root(self, root_task_id: str) -> TaskGraph: ...
    def dispatch_ready(self, root_task_id: str | None = None) -> list[str]: ...
    def review_completed_child(self, task_id: str) -> ReviewDecision: ...
    def wake_dependents(self, task_id: str) -> list[str]: ...
```

`decompose_task` 的输出必须是结构化 graph，而不是自由文本：

```json
{
  "rootTaskId": "task-root",
  "strategy": "deep",
  "tasks": [
    {
      "tempId": "research-runtime",
      "title": "梳理现有任务系统",
      "body": "读取 tasks/workers/memory/server 相关实现，输出可复用边界。",
      "workerProfile": "researcher",
      "acceptanceCriteria": [
        "列出现有状态机和缺口",
        "标明需要保留的兼容字段"
      ],
      "allowedToolsets": ["file_read", "search"],
      "dependsOn": []
    }
  ],
  "edges": [
    {
      "from": "research-runtime",
      "to": "design-schema",
      "type": "blocks",
      "requiredStatus": "succeeded"
    }
  ]
}
```

### Worker 隔离模型

子 Agent 不应共享父 Agent 的完整上下文。每个 worker 运行时只拿到经过裁剪的 `WorkerContext`：

- task title/body/kind/source。
- acceptance criteria 和 out-of-scope。
- workspace path 和 role runtime scope。
- root task 摘要，而不是完整父对话。
- 已完成 dependency artifacts。
- 当前 task 过去 attempts、失败原因、checkpoint。
- 相关 memory/search/file snippets。
- allowed/disallowed toolsets。
- 输出 schema 和 handoff 要求。

worker 的结果也不直接污染主会话 raw history。它写入：

- `task_attempts.result/error/checkpoint_json`
- `task_artifacts`
- `tasks.handoff_summary`
- `task_events`

父 Agent 或 orchestrator 只读取 summary/artifacts/review decision。

### Runner 设计

当前 `TaskRunner` 是稳定扩展点，多种 runner 对上层保持同一个 contract：

- `SubprocessTaskRunner`：当前生产默认 runner，以独立解释器进程运行单任务 entrypoint，并结合 workspace-copy isolation、进程组取消和周期恢复。
- `InProcessTaskRunner` / `SynchronousTaskRunner`：显式测试和兼容选项，用于短任务或确定性 contract 验证。
- `ProcessTaskRunner`：可选 POSIX fork runner；完整线程化 runtime 在 macOS 上存在 fork 后崩溃风险，因此该平台明确禁用并要求使用 external subprocess。
- isolated child model loop：已复用 `WorkerContext`、`TaskWorker` 和 `WorkerRuntimeScope` 替换启发式 `delegate_task`，没有引入第二套任务状态机；下一步是多 child graph 调度质量而非新的 runner contract。

runner contract 不应只接受 `run_task(task_id)`，应演进为：

```python
class TaskRunner(Protocol):
    def submit(self, task_id: str, *, reason: str = "ready") -> str: ...
    def cancel(self, run_id: str, *, reason: str) -> None: ...
    def shutdown(self, *, wait: bool = True) -> None: ...
```

`TaskWorker` 继续负责状态机、claim、lease、heartbeat、retry；runner 只负责把一次 attempt 交给执行环境。

### 调度状态机

任务状态保持现有语义并补齐 graph 行为：

```text
queued -> ready -> running -> succeeded
queued -> blocked
running -> blocked
running -> failed -> queued(retry)
running -> failed
queued/running/blocked -> cancelled
```

如果不想立刻引入 `ready` 状态，可以先让 `queued` 同时表示等待依赖和可运行，但必须在 scheduler 查询里区分：

- dependencies incomplete：不返回给 runner。
- due/nextRunAt 未到：不返回给 runner。
- lease 未过期：不返回给 runner。
- review blocked：不返回给 runner。

调度器循环：

1. recover expired running leases。
2. resolve completed child dependencies。
3. wake dependents whose dependencies are satisfied。
4. dispatch ready queued tasks。
5. publish `task.updated` / `task.graph.updated`。

### Orchestrator 策略

Orchestrator 不是普通聊天 Agent 的自由发挥，而是 runtime 受控流程：

1. `specify`：把用户目标转成 Goal / Approach / Acceptance / Out of scope。
2. `decompose`：生成 2-8 个可并行/串行子任务，带依赖和 profile。
3. `validate_graph`：检查循环依赖、空验收标准、危险工具、过宽任务、成本上限。
4. `dispatch`：只派发 ready tasks。
5. `review_child`：检查 artifact 是否满足 acceptance criteria。
6. `repair`：失败时决定 retry、拆小、改 profile、blocked 等。
7. `synthesize`：所有子任务完成后生成父任务 handoff/result。

第一版可以用当前主模型做 `specify/decompose/review`，但调用点应在内部 service，输入输出 JSON schema 固定，失败时保守降级为单任务执行。

### 权限与工具边界

长任务不能默认继承全部工具。建议 profile 化：

```yaml
worker_profiles:
  researcher:
    toolsets: ["search", "read", "memory_read"]
    write: false
  coder:
    toolsets: ["search", "read", "patch", "terminal"]
    write: true
    requires_review: true
  reviewer:
    toolsets: ["read", "diff", "test"]
    write: false
  synthesizer:
    toolsets: ["read", "memory_read"]
    write: false
```

默认规则：

- 子 Agent 不能递归 delegation，除非 profile 是 `orchestrator` 且 depth 未超限。
- 子 Agent 默认不能控制 Live2D/audio。
- mutating tools 仍走 ToolRuntime permission/audit。
- process runner 必须继承 role `workspacePath` 限制。
- worker 只能更新自己的 task/attempt/artifact，不能任意改 sibling/root。

### API 与事件

HTTP API 应围绕现有 `/tasks` 扩展：

```text
POST /tasks
GET /tasks
GET /tasks/{id}
GET /tasks/{id}/events
GET /tasks/{id}/graph
POST /tasks/{id}/decompose
POST /tasks/{id}/dispatch
POST /tasks/{id}/synthesize
POST /tasks/{id}/cancel
POST /tasks/{id}/approve
POST /tasks/{id}/retry
GET /tasks/{id}/artifacts
GET /tasks/{id}/attempts
```

Runtime events：

```text
task.updated
task.graph.updated
task.attempt.started
task.attempt.heartbeat
task.attempt.finished
task.artifact.created
task.review.required
task.dependency.satisfied
task.dependency.blocked
```

UI 不需要知道 runner 细节，但需要能展示：

- root task tree。
- dependency blocked/ready/running/completed。
- active attempts。
- artifacts。
- review gate。
- retry/error timeline。

### 与 `update_plan` 的关系

`update_plan` 保持轻量，负责当前 turn 的可见计划。长任务 graph 创建后，runtime 可以自动生成或同步 plan items：

- root task 创建：创建 `PlanRun`。
- child task queued：可选生成 plan item。
- child task running/succeeded/blocked/cancelled：同步 linked plan item。
- plan panel 只展示用户可理解的摘要；task graph view 展示完整执行细节。

不要让模型通过 `update_plan` 改 task graph。graph 只能通过 task/orchestrator API 修改。

### 实施顺序

虽然目标按完整功能设计，但实现应按不破坏现有系统的顺序推进：

1. Schema migration：补 `root_task_id`、`plan_run_id`、`worker_profile`、`acceptance_criteria_json`、`context_hints_json`、`checkpoint_json`、`handoff_summary`，新增 `task_edges`、`task_attempts`、`task_artifacts`。
2. Store layer：实现 task graph CRUD、dependency query、attempt/artifact 写入；保持旧 `/tasks` response 兼容。
3. Internal graph service：实现 `decompose_task` schema、graph validation、ready task query。
4. WorkerContext builder：先用于 in-process runner，验证上下文隔离和 artifact handoff。
5. Orchestrator service：实现 specify/decompose/dispatch/review/synthesize 的内部流程。
6. Scheduler loop：从简单 submit 升级为 dependency-aware dispatch。
7. Child agent runner：已把受限 `delegate_task` 从启发式 search/read 升级为 tracked isolated child loop；后续补多 child graph quality 与异步 handoff。
8. Process runner：在 `TaskRunner` contract 后接 subprocess，实现真正长期任务和重启恢复。
9. UI graph view：在 Main UI TasksView 增加 graph、attempt、artifact、review。
10. Eval：增加 decomposition JSON validity、dependency dispatch、worker isolation、artifact handoff、cancel/retry/recover 回归测试。

### 不做的事情

- 不把 `decompose_task` 直接暴露成模型工具作为第一版入口。
- 不复制一套 Hermes Kanban 数据库；Amadeus 已经有 `tasks`，应增强现有表。
- 不让 worker 继承父对话完整历史。
- 不让子 Agent 默认拥有写文件、shell、audio/Live2D 控制或递归 delegation。
- 不把完成子任务的全文结果长期塞进 active context。

## 下一轮实现切片

目标按完整长任务能力设计，但代码落地要先从不会破坏现有 `/tasks` API 的基础设施开始。

### Slice 1：Task graph schema

- Status: implemented as the first persistence slice.
- `tasks` now has graph/worker/context compatibility fields including `root_task_id`, `plan_run_id`, `worker_profile`, `acceptance_criteria_json`, `context_hints_json`, `allowed_toolsets_json`, `disallowed_tools_json`, `checkpoint_json`, and `handoff_summary`.
- `task_edges`, `task_attempts`, and `task_artifacts` now exist with store CRUD and read-only HTTP surfaces.
- Store layer keeps old `artifacts_json`, `parentTaskId`, and `planItemId` responses compatible while exposing graph fields.
- Unit coverage includes migration, task response, edge validation, attempt/artifact round trip, and HTTP graph/attempt/artifact reads.

### Slice 2：Dependency-aware scheduler

- Status: first in-store dependency-aware runnable selection is implemented.
- `list_runnable_tasks()` and `start_task()` now skip queued tasks whose incoming `task_edges` have not reached their required status.
- `TaskWorker.recover()` already uses `list_runnable_tasks()`, so recovered submissions inherit the dependency filter.
- Remaining work: explicit `TaskGraphService`, dependent wake events, root cancel cascading, and richer graph event publication.

### Slice 3：WorkerContext builder

- Status: implemented for the current in-process worker.
- Added `WorkerContext` and `build_worker_context(...)` in the worker layer.
- Context now contains task spec, acceptance criteria, root summary, dependency artifacts, attempt history, context hints, and allowed/disallowed tool metadata.
- Current in-process workers use this prompt instead of directly passing task title/body.
- Worker execution now records `task_attempts`, heartbeats the active attempt, finishes attempts with result/error/checkpoint state, and writes successful worker results as summary artifacts.
- Remaining work: memory/file snippet retrieval, token-budget-aware trimming, and reuse from child-agent/process runners.

### Slice 4：Internal orchestrator

- Status: first model-backed graph generation, graph repair, graph lifecycle events, and root synthesis are implemented behind the internal orchestrator service.
- Added internal `OrchestratorService` with root goal creation, structured graph validation, graph repair, child task/edge persistence, ready child dispatch, terminal child review, graph lifecycle event recording, and root synthesis.
- Added controlled Python HTTP entrypoints: `POST /tasks/{id}/decompose` applies a validated structured graph, `POST /tasks/{id}/dispatch` submits dependency-ready children through the existing task worker, and `POST /tasks/{id}/synthesize` summarizes terminal child results into the root task.
- `POST /tasks/{id}/decompose` can also run with `auto: true`, which asks the configured planning model for a fixed-shape JSON spec and task graph, validates it, asks the model for one fixed-shape repair if validation fails, applies the repaired graph when valid, and falls back to a single child task if generation/repair still fails.
- `POST /tasks/{id}/synthesize` waits while children are still active, blocks the root when any child failed/cancelled, and completes the root with a summary artifact when all children succeeded. Model synthesis uses a fixed JSON response shape and falls back to deterministic child-result summarization.
- Graph validation rejects duplicate task ids, unknown dependencies, unknown edge endpoints, excessive child counts, dependency cycles, unknown worker profiles, unknown toolsets, and profile/toolset escalation.
- Known worker profiles get orchestrator-owned default `allowedToolsets`, so missing model tool bounds do not become an unbounded child-worker prompt.
- Root tasks now record durable `task_events` for `graph.decomposed`, `graph.applied`, `graph.dispatched`, and `graph.synthesized`, including source/fallback/repair and child task metadata where relevant.
- Controlled HTTP graph operations now also publish `task.updated` runtime events with `graph_decomposed`, `graph_dispatched`, and `graph_synthesized` actions for desktop/WebSocket subscribers.
- `decompose_task` is still not exposed as a model tool; model generation is an internal service call and all writes still go through graph validation.
- Remaining work: richer repair strategies, broader planning quality evals, richer graph UI semantics, and multi-child scheduling/handoff quality.

### Slice 5：Isolated child runner

- 已通过现有 `TaskWorker`/`TaskRunner` 合同运行 tracked child task；生产路径复用 external subprocess runner，不新增 `ThreadedChildAgentRunner` 状态机。
- 已完成第一版 worker runtime scope：worker task 的 `workerProfile`、`allowedToolsets`、`disallowedTools`、任务 workspace hints 和 sandbox mode 会被解析成临时 `AgentRuntime` `WorkerRuntimeScope`，限制 child turn 的 tool schema、prompt hints、执行检查、ToolContext 元数据、audit metadata 和 workspace root resolution。workspace hint 必须落在当前 session workspace 内，否则 worker 在模型执行前失败并记录 `worker_scope_invalid` attempt checkpoint。sandbox mode 目前支持 `read_only`、`workspace_write` 和 `workspace_execute`：read-only 会隐藏并阻断 mutation/execution tools，workspace-write 允许 workspace 文件改动但阻断 shell/process/code execution，workspace-execute 只有在 profile/toolset 同时允许时才放开执行类工具；被 sandbox 阻断的调用会在 handler 前返回 `worker_sandbox_denied` 并写入 audit。worker ask-tool policy 也已按 profile 收口：researcher `web_extract`、coder `patch` 等少数工具可 `worker_auto_approved`；其他 ask tool 不会从后台 worker 打开交互式 permission prompt，而是把任务 block 到 tool-specific `approval_required` checkpoint。用户 resume 后 checkpoint 会携带 approved tool，下一次 `WorkerRuntimeScope` 允许该 ask-tool 走 worker auto-approved 路径。attempt checkpoint 已升级为阶段化记录，覆盖 context build、scope validation、model turn start、tool completion、assistant output、error、cancel、review block、worker tool approval block 和 completion，heartbeat 会保留最新阶段。worker `tool.finished` preview 会写成 task artifact，后续 WorkerContext 会把当前 task artifacts 放入 `<task-artifacts>`，让 resume worker 先复用已有工具输出。成功工具事件现在会在缺少 policy preview 时携带 compact JSON result preview，worker 会从 patch/write/read/search/terminal preview 中提取文件状态/幂等续跑 metadata，包括 affected files、observed files、command、exit code、changed flag、idempotency hint，以及可用文件的 size/mtime/SHA-256 manifest。WorkerContext 构建时会把 saved file manifest 与当前 workspace 重新比较，输出 unchanged/changed/unverifiable verification，并生成第一版 `fileResumePolicy`，例如 `skip_redundant_mutation`、`reinspect_before_mutation` 和 `reuse_observation`，供 resume worker 判断是否复用、跳过或重新检查。Main UI task detail artifact cards 会把这些 verified file resume policies 作为状态、路径、instructions 和 override tag 展示出来，避免只能看 raw JSON。`WorkerRuntimeScope` 现在还会携带这些 verified policies 进入工具 guardrail：unchanged mutation artifact 会在 handler 执行前阻断同工具同路径重复 patch/write，changed mutation artifact 会要求同一 worker turn 先 `read_file` 再继续 mutation；`fileResumePolicy.override` 第一版支持 `force_rerun`、`ignore_artifact` 和 `accept_current_state`。task detail 现在会从一等 task artifact endpoint 加载 artifact，并可通过 `POST /tasks/{taskId}/artifacts/{artifactId}/file-resume-override` 设置或清除这些 override，更新后广播 `artifact_override_updated`。stale recovery 和 subprocess-loss retry 会把最新 running attempt checkpoint 提升成 task-level `resumeFrom` checkpoint 和 handoff summary，供下一次 worker turn 恢复上下文使用；review-required completion 会写入 task-level `approval_required` checkpoint 和 handoff summary，approval/resume 会继续写入审批或 blocked resume checkpoint；WorkerContext 会把 `resumeFrom` 渲染成 `<resume-strategy>`，按阶段要求验证已有输出、避免重复工作或改变失败后的执行策略。approval resume 的 `<resume-strategy>` 还会列出 approved ask-tools，并限制 worker 只为 blocked step 使用这些工具，避免把一次审批解释成永久权限。
- Worker ask-tool approval 已从纯工具级别推进到第一版 action-specific policy：terminal 命令会生成 exact command action key，process kill/status key 绑定具体 PID，kill key 同时绑定规范化 signal，process list key 绑定规范化 query hash，文件/网页类工具会按 path/target 生成 key；blocked checkpoint 会保存 action label/risk metadata，第一批 classifier 会标记 destructive/privileged shell、installer、network script/access、sensitive data/path、workspace-external path、whole-file write、bulk replace、insecure/sensitive URL 和 unknown target；resume 后只把该 action key 放入下一次 `WorkerRuntimeScope`，并带来自 `tasks.workerApprovalActionTtlSeconds` / `AMADEUS_WORKER_APPROVAL_ACTION_TTL_SECONDS` 的过期时间，默认 15 分钟；旧的 tool-wide auto approval 只兼容没有 action key 的历史 checkpoint。
- Main UI task detail 现在会把 approval action label/key、tool name、risk level/labels、expiry 状态、受限授权说明和 approval-aware resume 按钮文案直接展示在 checkpoint 面板里，不再要求用户读 raw checkpoint JSON 才能判断批准的动作边界。
- Approval/override 审计第一版已闭合：blocked/resumed/review-approved task event 会保存 action、risk、scope、TTL/expiry、source/actor 等适用字段；成功 `worker_auto_approved` 工具审计会保留相同的 action/risk/expiry 关联；file-resume override set/clear 会保存 artifact/policy、前后 override、changed、source/actor，并在 Main UI timeline 使用独立标签。
- `delegate_task` 已迁到 tracked child runner：创建 `delegated` researcher task，在归档 `worker:<taskId>` session 中运行真实 model loop，使用 read-only scope、source-session memory 边界、timeout/cancel propagation 和 summary-only parent result。
- 子 Agent 只写任务专属 session、attempt/artifacts/task events，不写父会话 raw history；worker prompt 禁止自动 stable-memory、global transcript 和 external-memory prefetch。
- 仍需补更广的危险/敏感/低置信度动作 approval/override policy 调优、OS-native sandbox、多 child 编排质量和异步 handoff UX。

### Slice 6：Process runner

- Status: production now defaults to the independent durable supervisor path with workspace-copy isolation. The Python HTTP runtime uses `ExternalSupervisorTaskRunner` as a DB-backed client, while `packages/amadeus/task_supervisor.py` owns `SubprocessTaskRunner`; the optional POSIX fork-backed `ProcessTaskRunner` remains behind the same contract for supported non-macOS systems.
- First dedicated single-task worker entrypoint is implemented in `amadeus.task_worker_entrypoint`: a subprocess can bind work through `--task-id` / `AMADEUS_TASK_ID` and `--database` / `AMADEUS_MEMORY_DB` while reusing `TaskWorker` state transitions.
- First external subprocess launcher/supervisor slice is implemented in `SubprocessTaskRunner`: `AMADEUS_TASK_RUNNER=subprocess` starts `python -m amadeus.task_worker_entrypoint`, passes `AMADEUS_TASK_ID`, `AMADEUS_TASK_RUN_ID`, `AMADEUS_MEMORY_DB`, `AMADEUS_WORKSPACE`, and `AMADEUS_WORKER_PROFILE`, enforces bounded concurrency, suppresses duplicate active launches, starts independent process groups, supports task-specific termination, and reclaims non-zero exits back into queued retry or terminal failure.
- Subprocess-loss attempts are now explicitly marked `abandoned`, keeping process interruption distinct from normal model/runtime `failed` attempts.
- Worker profile/toolset/workspace/permission policy now limits child tool visibility, execution, context metadata, audit metadata, workspace root resolution, and ask-tool behavior through `AgentRuntime.worker_runtime_scope(...)`; worker workspace hints outside the session workspace are rejected before model execution, and non-auto-approved worker ask-tools block into tool-specific approval checkpoints.
- Worker attempts now heartbeat stage-oriented checkpoints that expose the last phase, last event, worker profile/toolsets, and result/error previews for restart diagnostics.
- Stale-lease recovery and subprocess-loss retry now promote the latest attempt checkpoint into task-level `resumeFrom` handoff context instead of only reporting a generic retry error.
- WorkerContext now renders phase-specific `<resume-strategy>` instructions from `resumeFrom`, including verifying partial assistant output before redoing work.
- 已实现 verified manifest policy 的第一版 runtime-level enforcement、file-resume override 执行语义、用户可操作的 file-resume override 流程与 durable audit event、target-specific approval key 的可配置过期、第一批 destructive/sensitive action classifier、approval block/resume/review 与成功工具执行的关联审计、第一版 worker sandbox mode enforcement，以及 task-detail approval action/risk/expiry/inline resume copy 展示；仍需补更广的 approval/override policy 调优和 OS/process-level 工作区沙箱策略。
- task attempt heartbeat 独立于父 turn 生命周期。
- 重启后 expired lease 可 reclaim；subprocess 非零退出已能将匹配 run 的未完成 attempt 标记为 `abandoned` 并触发 retry/fail。
- 独立 `DurableTaskSupervisor` 已落地：SQLite `supervisor_leases` 保证本机单主，`task_processes` 保存 run/PID/process-group/workspace/log 状态；重启后可接管存活 worker、恢复丢失 worker、同步 cancelled task、执行 wall timeout 与 TERM-to-KILL，worker entrypoint 应用 POSIX CPU/address-space/file/open-file limits，并把结果写入 task event。
- Python `/runtime/health` 会暴露 external supervisor lease/process registry 并在 supervisor 缺失时降级；`scripts/dev_stack.py` 先启动 supervisor，再启动 runtime/bridge。剩余工作是 OS-native sandbox、长时间双重崩溃 soak、资源使用指标，以及跨主机调度（当前不在 SQLite 单机控制面的范围内）。

### Slice 7：Main UI graph view

- TasksView 增加 tree/dependency 状态、attempt timeline、artifact list、review gate。
- PlanPanel 继续展示可读计划，不承担完整 graph 调试职责。
- 新增 task graph event handling：`task.graph.updated`、`task.attempt.*`、`task.artifact.created`。

### Slice 8：Packaged real-runtime E2E

- `npm run test:e2e` 新增真实进程链路：测试侧 OpenAI-compatible fixture、Python runtime、Node bridge 和 packaged Electron/Vue Main UI 分别运行，使用临时 SQLite/state root 和随机端口，不依赖外部模型凭据。
- 真实链路覆盖 chat turn 持久化、切换到第二 session、回到 Companion 后恢复历史，以及 review-required task 从 blocked 经 Main UI 审批到 durable succeeded。
- 每条 Electron E2E 使用独立 `userData`，避免 single-instance lock 和浏览器持久状态造成空退出或跨用例污染。
- 仍需补真实 Cubism 资源启动、桌面/Bridge/Python 重启中的 UI 恢复，以及更长时间的交互 soak。

### Slice 8：Eval and regression

- 增加 long-task eval：decomposition JSON validity、dependency dispatch、worker isolation、artifact handoff、cancel/retry/recover、review gate。
- 每个 runner 至少有一组相同 task graph contract test。
- 长任务相关 prompt/schema 改动必须跑这些 eval。

## 关键工程原则

- Python runtime owns behavior。TypeScript bridge owns transport。Desktop owns device execution。
- Harness 不是 tool。Live2D/audio 是常驻设备/表达层。
- 所有敏感动作都在 executor 层 enforcement，不依赖 prompt。
- 所有外部 provider 都通过 adapter/transport，不进主循环。
- 所有长期状态都落 SQLite 或明确文件，不只放内存。
- 所有后台任务都要有 heartbeat、状态、事件历史、可恢复。
- 所有 prompt 和行为映射都要成为可测试 surface。

## 最小目标形态

达到下面状态时，Amadeus 才算从 MVP 进入成熟 agent：

- 关掉 desktop 后，Python agent 仍能通过 HTTP/CLI 正常对话和用工具。
- 关掉 Live2D harness 后，agent core 不受影响。
- 换一个 Live2D 模型，只改配置和 adapter，不改 agent loop。
- 换 TTS provider，只改 audio harness/provider，不改 desktop chat。
- 长对话不会无限膨胀，有 summary 和 profile memory。
- 工具失败不会无限重复，有 guardrail 和审计。
- reminder/task 能跨重启恢复。
- subagent 能隔离复杂任务。
- 关键行为有 eval，而不是靠手测。
