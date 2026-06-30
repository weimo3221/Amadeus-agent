# Amadeus 成熟 Agent 升级计划

Last updated: 2026-06-22

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
- 没有 task/subagent/job 体系。复杂长任务、后台任务、proactive 行为还无法可靠运行。
- 没有 eval 和 regression harness。现在有 typecheck/unit/bridge/renderer/Electron startup smoke coverage，但缺少针对 agent 行为和 Live2D/audio interaction 的可重复评估。
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
   - 下一步：失败重试和 worker 重启恢复。
2. Agent turn 控制：
   - 已完成第一片：每个 running turn 有 `turn_id`，Python runtime 维护 session-scoped running turn 状态，`POST /agent/cancel` 可设置 cooperative cancel event，工具执行通过 `ToolContext.cancel_event` 接收取消信号，并发出 `agent.turn.started` / `agent.turn.cancelled`。
   - 下一步：长任务失败/刷新后的状态恢复、checkpoint/resume、permission wait 的取消唤醒、provider request 级 timeout/cancel 策略。
3. `delegate_task` MVP：
   - 已完成第一片：`delegate_task` 是受限研究/搜索型工具，`max_depth=1`、`max_concurrency=2`，只使用 memory search、file search 和 explicit bounded file reads，不给写文件、shell、Live2D/audio 或递归 delegation 能力，父 agent 只接收 summary 和结构化 findings。
   - 下一步：把当前启发式研究 delegate 替换成真正 isolated child-agent runner，并保留相同的工具/深度/并发限制。
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
- 已完成 stable memory 第一片：`data/memory/MEMORY.md`、`data/memory/USER.md`、`read_memory`、`update_memory`。
- 已完成 conversation summary 第一片：SQLite `conversation_summaries`、覆盖范围元数据、`GET /memory/summary`、`POST /memory/summary`、`POST /memory/compact`、阈值触发 compaction、context 注入。
- 已完成 structured memory 第一片：SQLite `memory_items`、`user` / `agent` / `project` scope、显式 add/list/delete HTTP API、`<memory-items>` context 注入。
- 已完成 explicit structured memory tools：`search_memory_items` 只读检索 durable facts，`memory_add` / `memory_replace` / `memory_forget` 通过 `ask` 权限写入、替换或删除单条 durable fact，并带重复检测、来源 session 元数据、模型输出裁剪和 no-progress guardrail。
- 已完成 memory review candidate 队列第一片：SQLite `memory_review_candidates`、`pending` / `accepted` / `rejected` / `superseded` 状态、候选查询/创建 API、accept 提升到 `memory_items`、reject 不写入 durable memory，且 rejected 候选会 suppression 后续同 session/scope/content 的重复建议。
- 实现 context assembler：
  - system prompt。
  - character persona。
  - active harness prompt。
  - recent messages。
  - session summary。
    - user profile / structured memory items。
  - relevant retrieved memories。
  - tool state/task state。
- 已完成 token-budget-aware summary compaction 第一片：runtime 使用 tokenizer-free 估算检查 context budget，默认值集中在 `configs/runtime.yaml`，并仍可通过 `AMADEUS_CONTEXT_MAX_TOKENS`、`AMADEUS_CONTEXT_COMPACTION_TRIGGER_RATIO` 和 `AMADEUS_CONTEXT_RECENT_MESSAGE_TARGET_RATIO` 覆盖；预算压力会强制总结旧消息并动态缩小 recent-message keep window，provider context overflow 会触发一次 compact-and-retry fallback。
- 继续扩展 explicit structured memory 工具：
  - 已完成：`search_memory_items`、`memory_add`、`memory_replace`、`memory_forget`。
- 实现 background memory review runner：
  - 已完成手动触发第一片：`POST /memory/review/run` 读取最近消息、已有 durable memory 和 pending candidates，只生成 `memory_review_candidates`，不直接写入 `memory_items`。
  - 已完成自动调度第一片：每个 turn 的主回复发出后，按 message-count threshold、success/failure cooldown 和 no-new-message gate 判断是否运行 review。
  - 自动调度不会自动 accept，也不会直接写 durable memory。
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
  - id、title、body、status、priority、session_id、created_at、updated_at、due_at、claim_lock、last_heartbeat、result、error。
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

## 近期 2 周可执行计划

### Week 1

- 建 `packages/amadeus/agent/runtime.py`。
- 把 server 中 LLM 请求迁到 Python。
- 新增 `/agent/turn`，先返回 event batch，后续再做真正 streaming。
- Python runtime 负责保存 user/assistant messages。
- server 只转发 `user.message` 和 relay events。
- 写单元测试：
  - missing API key。
  - simple text answer。
  - time tool call。
  - ask tool permission request。
  - permission denied tool result。

### Week 2

- 建 Python `tools/registry.py`、`permissions.py`、`executor.py`。
- 工具配置改 Python 加载。
- `packages/amadeus/tools.ts` 已从硬编码 registry 过渡为 `/tools/list` bridge。
- 加 `ToolCallGuardrailController`。
- 建 `harness/base.py` 和 `harness/live2d.py`，先迁移 `assistant.state -> character.behavior`。
- 新增 `configs/harnesses.yaml`。
- desktop 增加 `character.capabilities` 上报。

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
