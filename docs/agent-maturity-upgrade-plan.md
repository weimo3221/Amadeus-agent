# Amadeus 成熟 Agent 升级计划

Last updated: 2026-06-19

## 目标判断

Amadeus 现在已经有一个能跑的桌面 MVP：Electron 透明窗口、远程 Live2D 模型、WebSocket 事件、OpenAI-compatible 模型调用、SQLite 原始消息、工具调用、工具权限 prompt、Python sidecar、音频 fallback 都已经打通。当前 preferred turn path 已经迁到 Python：`apps/server` 主要作为 WebSocket/HTTP bridge，`packages/amadeus/agent.py` 负责单轮对话、模型调用、记忆读写、工具决策、工具执行、权限请求和 runtime event streaming。

它的问题不是“没有功能”，而是核心 agent runtime 还没有成熟：provider abstraction、context assembly、长期记忆、工具审计/超时/取消、Live2D/audio harness、skills、任务调度和 eval 仍处在早期或占位状态。

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
  - HTTP `/health` 和 WebSocket `/ws`。
  - relay desktop `user.message` 到 Python `/agent/turn`。
  - relay Python NDJSON runtime events 回桌面。
  - forward desktop `tool.permission.response` 到 Python `/tools/permission`。
  - Python runtime 不可用时返回显式 runtime error，而不是运行第二套 TS agent loop。
- `packages/amadeus`
  - Python `server.py` sidecar。
  - Python `agent.py` 是当前 preferred turn path。
  - Python 工具：`get_current_time`、`roll_dice`、`search_files`、`read_file`、`patch`；`local_file_search` 作为旧名兼容 alias 保留。
  - Python SQLite message store。
  - Python ToolRuntime：registry/config loading、permission metadata、structured `ToolResult`、timeout/cancellation、result compression、`search_files` per-tool result policy、`read_file` explicit line-window reads and non-text kind reporting、`patch` exact-replacement edits、repeated-failure/no-progress guardrail。
  - Python audio/live2d dataclass 合同雏形。
  - TypeScript `events.ts` 与 `tools.ts` bridge/diagnostics scaffold。

### 成熟 agent 差距

- Python 已经 owns preferred single-turn loop，但 provider 调用、context assembly、行为策略仍集中在 `agent.py`，还没有拆成成熟 runtime/model/context/harness 模块。
- ToolRuntime 已有 registry、permission metadata、structured result、SQLite 持久化审计记录、超时、cooperative cancellation、结果压缩、`search_files` per-tool result policy、`read_file` explicit line-window reads and non-text kind reporting、`patch` exact-replacement edits、重复失败 guardrail 和 first-pass no-progress detection，但还缺更强语义级 no-progress detection。
- 记忆只有 raw message replay，没有 conversation summary、user profile、事实抽取、跨会话搜索、记忆写入策略。
- Live2D/audio 还没有 harness 化。现在 Live2D 控制在 renderer，audio 只是 Python 接口 + desktop fallback，没有统一的“感知、状态、输出、播放反馈”协议。
- 没有 task/subagent/job 体系。复杂长任务、后台任务、proactive 行为还无法可靠运行。
- 没有 eval 和 regression harness。现在有 typecheck/unit/bridge/renderer/Electron startup smoke coverage，但缺少针对 agent 行为和 Live2D/audio interaction 的可重复评估。
- 配置与 provider 体系还浅。`configs/providers.yaml` 还没有成为 model/provider transport 的实际装配入口。

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

`Live2DHarness` 应负责把 agent 状态转成 character commands，而不是由 server 硬编码：

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
- `get_current_time`、`roll_dice`、`search_files`、`read_file`、`patch` 仍可工作，`local_file_search` 作为旧名兼容 alias。
- 断开 Python runtime 时 server 明确报错。
- `apps/server` 不再直接调用 LLM provider。

### Phase 2：统一 ToolRuntime

状态：进行中。已完成 Python `ToolRegistry`、`configs/tools.yaml` loading、`ToolContext` / `ToolResult` 第一版、duration/failure code、first-pass timeout handling、cooperative cancellation、result preview/compression、`search_files` per-tool result policy、`read_file` explicit line-window reads and non-text kind reporting、`patch` exact-replacement edits、permission-aware schema projection、SQLite-backed audit records、repeated-failure guardrail、first-pass no-progress detection。剩余重点是更多高容量工具的 result policies 和更强语义级 no-progress detection。

目标：工具从“函数字典 + TS fallback”升级为可审计 runtime。

任务：

- 完善 `ToolContext`、`ToolResult`、`ToolPermissionPolicy`。
- 工具 schema、权限、display name、handler 继续收敛到 Python-owned。
- TS `packages/amadeus/tools.ts` 只保留 schema bridge 或从 Python `/tools/list` 拉取。
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

- 实现 context assembler：
  - system prompt。
  - character persona。
  - active harness prompt。
  - recent messages。
  - session summary。
  - user profile。
  - relevant retrieved memories。
  - tool state/task state。
- 实现 summary compaction：超过 N 条消息后总结旧上下文。
- 实现 memory write 工具：
  - `memory_add`
  - `memory_replace`
  - `memory_search`
  - `memory_forget`
- 实现 background memory review：
  - 每个 turn 后异步判断是否需要保存偏好/事实。
  - 不阻塞主回复。
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
- 增加 playback feedback 事件闭环。

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
3. Memory v2：summary、profile、session_search。
4. Live2D/audio harness registry。
5. GPT-SoVITS provider 与 audio playback feedback。
6. Skills。
7. Reminder/task scheduler。
8. Subagent/delegate_task。
9. MCP/plugin/provider profiles。
10. Eval harness 优化闭环。

不要先做 MCP、subagent、复杂 UI。当前最大架构债是 runtime ownership，不先迁掉，后面的能力都会同时散落在 TS server、Python runtime 和 desktop renderer。

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
- `packages/amadeus/tools.ts` 从硬编码 registry 过渡为 `/tools/list` bridge。
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
