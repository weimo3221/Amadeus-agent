import { reactive, ref } from 'vue'
import type {
  MemoryContextUsedPayload,
  ScheduledJobRecord,
  ScheduledJobUpdatedPayload,
  TaskPlanItem,
  TaskPlanPayload,
  TaskRecord,
  TaskUpdatedPayload,
} from '@amadeus-agent/amadeus/events'
import type {
  ChatMessage,
  ChatToolCall,
  ConnectionState,
  MemoryItem,
  PlanItem,
  PlanRunItem,
  RoleProfile,
  SessionContext,
  ScheduledJob,
  SkillActivation,
  SessionItem,
  SkillItem,
  TaskEventItem,
  TaskItem,
  TaskNotification,
  TaskStatus,
  ToolTone,
} from '@/types'
import { AgentRuntimeClient, type ConnectionPhase, type ToolPermissionPrompt } from '@/runtime/client'
import { COMPANION_SESSION_ID, SESSION_ID } from '@/runtime/config'
import {
  backfillEmbeddingIndex,
  cancelTaskRequest,
  cancelEmbeddingDeploy,
  approveTaskRequest,
  createScheduledJobRequest,
  createTaskRequest,
  createSessionRequest,
  deleteSessionRequest,
  deployEmbeddingModel,
  fetchEmbeddingConfig,
  fetchAudioConfig,
  fetchLive2dBehaviors,
  fetchLive2dModels,
  fetchMemoryContextDiagnostics,
  fetchMemoryItems,
  fetchProviderPresets,
  fetchRoles,
  fetchRuntimeConfig,
  fetchScheduledJobs,
  fetchSessionMessages,
  fetchSessionPlan,
  fetchSessionPlanRuns,
  fetchSessions,
  fetchSkills,
  fetchToolAudit,
  fetchToolsConfig,
  fetchToolsList,
  fetchTasks,
  fetchTaskEvents,
  resumeTaskRequest,
  fetchTtsVoices,
  importLive2dModel,
  selectLive2dModel,
  updateAudioConfig,
  updateLive2dBehaviors,
  updateRoleRequest,
  updateRuntimeApiConfig,
  type AudioConfigResult,
  type AudioConfigUpdate,
  type EmbeddingConfigResult,
  type Live2dBehavior,
  type Live2dBehaviorsResult,
  type Live2dImportResult,
  type Live2dModelPayload,
  type MemoryItemPayload,
  type PlanRunPayload,
  type ProviderPreset,
  type RolePayload,
  type RoleUpdate,
  type RuntimeApiUpdate,
  type RuntimeConfigResult,
  type SessionPayload,
  type SkillPayload,
  type StoredMessage,
  type ToolAuditRecordPayload,
  type ToolsListResult,
  type ToolsConfigResult,
  type TtsVoicePayload,
} from '@/runtime/http'

interface RuntimeState {
  connection: ConnectionState
  chat: ChatMessage[]
  plan: PlanItem[]
  tasks: TaskItem[]
  skills: SkillItem[]
  suggestedSkillIds: string[]
  activeSkills: SkillActivation[]
  sessions: SessionItem[]
  roles: RoleProfile[]
  activeRole: RoleProfile | null
  memoryItems: MemoryItem[]
  memoryContextDiagnostics: MemoryContextUsedPayload[]
  scheduledJobs: ScheduledJob[]
  sessionContext: SessionContext
  activeSessionId: string
  roleName: string
  scheduledCount: number
  toolPermission: ToolPermissionPrompt | null
  providerPresets: ProviderPreset[]
  live2dModels: Live2dModelPayload[]
  ttsVoices: TtsVoicePayload[]
  ttsProvider: string
  ttsSupportsEnumeration: boolean
  runtimeConfig: RuntimeConfigResult | null
  embeddingConfig: EmbeddingConfigResult | null
  audioConfig: AudioConfigResult | null
  live2dBehaviors: Live2dBehaviorsResult | null
  toolsConfig: ToolsConfigResult | null
  effectiveTools: ToolsListResult | null
  toolAuditRecords: ToolAuditRecordPayload[]
  mcpAuditRecords: ToolAuditRecordPayload[]
  taskNotifications: TaskNotification[]
}

const state = reactive<RuntimeState>({
  connection: 'connecting',
  chat: [],
  plan: [],
  tasks: [],
  skills: [],
  suggestedSkillIds: [],
  activeSkills: [],
  sessions: [],
  roles: [],
  activeRole: null,
  memoryItems: [],
  memoryContextDiagnostics: [],
  scheduledJobs: [],
  sessionContext: {
    activeId: SESSION_ID,
    activeTitle: 'Companion 默认会话',
    companionId: COMPANION_SESSION_ID,
    companionTitle: 'Companion 默认会话',
    companionMessageCount: 0,
    companionUpdatedAt: '',
    viewingCompanion: SESSION_ID === COMPANION_SESSION_ID,
    hasCompanionSession: false,
  },
  activeSessionId: SESSION_ID,
  roleName: 'Amadeus',
  scheduledCount: 0,
  toolPermission: null,
  providerPresets: [],
  live2dModels: [],
  ttsVoices: [],
  ttsProvider: 'none',
  ttsSupportsEnumeration: false,
  runtimeConfig: null,
  embeddingConfig: null,
  audioConfig: null,
  live2dBehaviors: null,
  toolsConfig: null,
  effectiveTools: null,
  toolAuditRecords: [],
  mcpAuditRecords: [],
  taskNotifications: [],
})

let client: AgentRuntimeClient | null = null
let started = false
let activeRoleId = 'amadeus'
const pendingAssistantId = ref<string | null>(null)
const activeTurnId = ref<string | null>(null)
const activeTurnUserMessageId = ref<string | null>(null)

function connectionFromPhase(phase: ConnectionPhase): ConnectionState {
  if (phase === 'connected') return 'online'
  if (phase === 'connecting') return 'connecting'
  return 'offline'
}

function timeLabel(iso?: string): string {
  const date = iso ? new Date(iso) : new Date()
  if (Number.isNaN(date.getTime())) return ''
  return date.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })
}

function relativeLabel(iso?: string | null): string {
  if (!iso) return ''
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return ''
  const diffMs = Date.now() - date.getTime()
  const min = Math.floor(diffMs / 60000)
  if (min < 1) return '刚刚'
  if (min < 60) return `${min} 分钟前`
  const hours = Math.floor(min / 60)
  if (hours < 24) return `${hours} 小时前`
  const days = Math.floor(hours / 24)
  if (days < 7) return `${days} 天前`
  return date.toLocaleDateString('zh-CN', { month: '2-digit', day: '2-digit' })
}

function truncateToolArgumentText(text: string): string {
  const normalized = text.trim()
  if (normalized.length <= 1200) return normalized
  return `${normalized.slice(0, 1200)}\n...`
}

function formatToolArguments(raw: unknown): string {
  if (raw === null || raw === undefined) return ''
  if (typeof raw === 'string') {
    const trimmed = raw.trim()
    if (!trimmed) return ''
    try {
      return truncateToolArgumentText(JSON.stringify(JSON.parse(trimmed), null, 2))
    } catch {
      return truncateToolArgumentText(trimmed)
    }
  }
  try {
    return truncateToolArgumentText(JSON.stringify(raw, null, 2))
  } catch {
    return truncateToolArgumentText(String(raw))
  }
}

function normalizeToolCalls(raw: unknown): ChatToolCall[] {
  if (!Array.isArray(raw)) return []
  return raw
    .map((item, index) => {
      if (!item || typeof item !== 'object') return null
      const record = item as Record<string, unknown>
      const fn = record.function && typeof record.function === 'object'
        ? record.function as Record<string, unknown>
        : {}
      const rawName = fn.name ?? record.name ?? record.toolName ?? record.tool_name
      const name = typeof rawName === 'string' && rawName.trim() ? rawName.trim() : `tool_${index + 1}`
      const rawArguments = fn.arguments ?? record.arguments ?? record.args
      const id = typeof record.id === 'string' && record.id.trim() ? record.id.trim() : undefined
      const call: ChatToolCall = {
        name,
        argumentsText: formatToolArguments(rawArguments),
      }
      if (id) call.id = id
      return call
    })
    .filter((item): item is ChatToolCall => item !== null)
}

function messagesToChat(messages: StoredMessage[]): ChatMessage[] {
  return messages
    .filter((m) => {
      if (m.role === 'user') return true
      if (m.role !== 'assistant') return false
      const toolCalls = normalizeToolCalls(m.toolCalls ?? m.tool_calls)
      return Boolean((m.content ?? '').trim() || toolCalls.length)
    })
    .map((m, index) => {
      const toolCalls = normalizeToolCalls(m.toolCalls ?? m.tool_calls)
      return {
        id: m.id ? `m-${m.id}` : `h-${index}`,
        messageId: m.id,
        role: m.role === 'user' ? 'user' : 'assistant',
        content: m.content ?? '',
        createdAt: m.createdAt ? relativeLabel(m.createdAt) : '',
        ...(toolCalls.length ? { toolCalls } : {}),
      }
    })
}

const planStatusMap: Record<TaskPlanItem['status'], PlanItem['status'] | null> = {
  pending: 'pending',
  in_progress: 'active',
  completed: 'done',
  cancelled: null,
}

function planToItems(payload: TaskPlanPayload | null): PlanItem[] {
  if (!payload) return []
  return payload.items
    .map((item) => {
      const status = planStatusMap[item.status]
      if (!status) return null
      return { id: item.id, label: item.content, status }
    })
    .filter((item): item is PlanItem => item !== null)
}

function planRunToItem(run: PlanRunPayload): PlanRunItem {
  return {
    turnId: run.turnId,
    userMessageId: run.userMessageId ?? null,
    assistantMessageId: run.assistantMessageId ?? null,
    status: run.status,
    items: planToItems({ sessionId: run.sessionId, items: run.items, summary: { total: 0, completed: 0, inProgress: 0, pending: 0, cancelled: 0 }, updatedAt: run.updatedAt }),
    updatedAt: run.updatedAt,
    archivedAt: run.archivedAt ?? null,
  }
}

function planIsComplete(items: PlanItem[]): boolean {
  return items.length > 0 && items.every((item) => item.status === 'done')
}

function latestUserMessage(): ChatMessage | null {
  for (let index = state.chat.length - 1; index >= 0; index -= 1) {
    const message = state.chat[index]
    if (message.role === 'user') return message
  }
  return null
}

function latestAssistantMessage(): ChatMessage | null {
  for (let index = state.chat.length - 1; index >= 0; index -= 1) {
    const message = state.chat[index]
    if (message.role === 'assistant') return message
  }
  return null
}

function userMessageForTurn(turnId?: string | null): ChatMessage | null {
  if (turnId) {
    const byTurn = state.chat.find((message) => message.role === 'user' && message.turnId === turnId)
    if (byTurn) return byTurn
  }
  if (activeTurnUserMessageId.value) {
    const byActive = state.chat.find((message) => message.id === activeTurnUserMessageId.value)
    if (byActive?.role === 'user') return byActive
  }
  return latestUserMessage()
}

function createAssistantMessage(turnId?: string | null): ChatMessage {
  const message: ChatMessage = {
    id: `a-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
    role: 'assistant',
    content: '',
    createdAt: timeLabel(),
    turnId: turnId ?? undefined,
    pending: true,
  }
  state.chat.push(message)
  pendingAssistantId.value = message.id
  return message
}

function assistantMessageForTurn(turnId?: string | null, options: { create?: boolean } = {}): ChatMessage | null {
  const create = Boolean(options.create)
  if (pendingAssistantId.value) {
    const pending = state.chat.find((message) => message.id === pendingAssistantId.value)
    if (pending?.role === 'assistant') {
      if (turnId) pending.turnId = turnId
      return pending
    }
  }
  if (turnId) {
    for (let index = state.chat.length - 1; index >= 0; index -= 1) {
      const message = state.chat[index]
      if (message.role === 'assistant' && message.turnId === turnId) return message
    }
  }
  if (!create) return turnId ? null : latestAssistantMessage()
  return createAssistantMessage(turnId)
}

function bindTurnToLatestUser(turnId: string): void {
  activeTurnId.value = turnId
  const message = userMessageForTurn(turnId) ?? latestUserMessage()
  if (!message) return
  message.turnId = turnId
  activeTurnUserMessageId.value = message.id
}

function attachPlanToTurn(items: PlanItem[], turnId?: string | null): void {
  const message = assistantMessageForTurn(turnId, { create: true })
  if (!message) return
  if (turnId) message.turnId = turnId
  message.plan = items
  message.planArchived = false
  message.planIncomplete = false
  message.planCollapsed = false
  if (turnId) activeTurnId.value = turnId
}

function archivePlanForTurn(turnId?: string | null): void {
  const message = assistantMessageForTurn(turnId)
  if (message?.plan?.length) {
    const complete = planIsComplete(message.plan)
    message.planArchived = true
    message.planIncomplete = !complete
    message.planCollapsed = complete
  }
  if (!turnId || activeTurnId.value === turnId) {
    activeTurnId.value = null
    activeTurnUserMessageId.value = null
  }
}

function attachLoadedPlanToLatestTurn(items: PlanItem[]): void {
  if (!items.length) return
  const message = latestAssistantMessage() ?? createAssistantMessage(activeTurnId.value)
  if (!message) return
  message.plan = items
  message.planArchived = true
  message.planIncomplete = !planIsComplete(items)
  message.planCollapsed = planIsComplete(items)
  message.pending = false
}

function attachPlanRunsToMessages(runs: PlanRunPayload[]): void {
  const byUserMessageId = new Map<number, ChatMessage>()
  const byAssistantMessageId = new Map<number, ChatMessage>()
  for (const message of state.chat) {
    if (!message.messageId) continue
    if (message.role === 'user') {
      byUserMessageId.set(message.messageId, message)
    }
    if (message.role === 'assistant') {
      byAssistantMessageId.set(message.messageId, message)
    }
  }
  for (const rawRun of runs) {
    const run = planRunToItem(rawRun)
    if (!run.items.length) continue
    let message = run.assistantMessageId ? byAssistantMessageId.get(run.assistantMessageId) : undefined
    if (!message) {
      message = {
        id: `plan-${run.turnId}`,
        role: 'assistant',
        content: '',
        createdAt: relativeLabel(run.archivedAt ?? run.updatedAt),
        turnId: run.turnId,
      }
      const userMessage = run.userMessageId ? byUserMessageId.get(run.userMessageId) : null
      const insertIndex = userMessage ? state.chat.findIndex((candidate) => candidate.id === userMessage.id) + 1 : 0
      if (insertIndex > 0) {
        state.chat.splice(insertIndex, 0, message)
      } else {
        state.chat.push(message)
      }
    }
    message.turnId = run.turnId
    message.plan = run.items
    message.planArchived = run.status !== 'active'
    message.planIncomplete = run.status === 'incomplete'
    message.planCollapsed = run.status === 'completed'
  }
}

function syncExistingPlanSnapshots(items: PlanItem[]): void {
  if (!items.length) return
  const nextIds = new Set(items.map((item) => item.id))
  for (const message of state.chat) {
    if (!message.plan?.some((item) => nextIds.has(item.id))) continue
    message.plan = items
    if (message.planArchived) {
      const complete = planIsComplete(items)
      message.planIncomplete = !complete
      message.planCollapsed = complete
    }
  }
}

function tasksToItems(records: TaskRecord[]): TaskItem[] {
  return records.map((task) => ({
    id: task.id,
    title: task.title,
    detail: task.body ?? '',
    result: task.result ?? '',
    error: task.error ?? '',
    status: task.status as TaskStatus,
    updatedAt: relativeLabel(task.updatedAt),
    attempts: task.attemptCount ?? 0,
    maxAttempts: task.maxAttempts ?? 3,
    kind: task.kind ?? 'agent_turn',
    source: task.source ?? 'manual',
    parentTaskId: task.parentTaskId ?? null,
    planItemId: task.planItemId ?? null,
    workerType: task.workerType ?? 'agent',
    blockedReason: task.blockedReason ?? null,
    reviewRequired: Boolean(task.reviewRequired),
    dueAt: task.dueAt ?? null,
    nextRunAt: task.nextRunAt ?? null,
    leaseOwner: task.leaseOwner ?? null,
    leaseExpiresAt: task.leaseExpiresAt ?? null,
    runnerKind: task.runnerKind ?? null,
    lastHeartbeat: task.lastHeartbeat ?? null,
    finishedAt: task.finishedAt ?? null,
    checkpoint: task.checkpoint && typeof task.checkpoint === 'object' ? task.checkpoint : {},
    handoffSummary: task.handoffSummary ?? null,
    artifacts: (task.artifacts ?? []).map((artifact) => ({
      type: String(artifact.type ?? 'summary'),
      ...artifact,
    })),
  }))
}

function notificationTone(status: TaskStatus): ToolTone {
  if (status === 'succeeded') return 'success'
  if (status === 'failed') return 'danger'
  if (status === 'blocked') return 'warning'
  if (status === 'cancelled') return 'neutral'
  return 'info'
}

function maybeNotifyTask(record: TaskRecord, action?: string): void {
  const status = record.status as TaskStatus
  if (!['succeeded', 'failed', 'blocked', 'cancelled'].includes(status) && action !== 'review_approved') return
  const id = `${record.id}-${status}-${action ?? 'updated'}`
  if (state.taskNotifications.some((item) => item.id === id)) return
  state.taskNotifications = [
    {
      id,
      taskId: record.id,
      title: record.title,
      status,
      tone: notificationTone(status),
      createdAt: timeLabel(),
    },
    ...state.taskNotifications,
  ].slice(0, 5)
}

function skillsToItems(skills: SkillPayload[]): SkillItem[] {
  return skills.map((skill) => ({
    id: skill.identifier || skill.name,
    name: skill.name,
    category: skill.category ?? 'general',
    summary: skill.description ?? '',
  }))
}

function sessionsToItems(sessions: SessionPayload[], activeId: string): SessionItem[] {
  return sessions
    .filter((s) => !s.archived)
    .map((s) => ({
      id: s.id,
      title: s.title || '未命名会话',
      roleName: s.roleName || 'Amadeus',
      messageCount: s.messageCount ?? 0,
      updatedAt: relativeLabel(s.updatedAt),
      active: s.id === activeId,
    }))
}

function buildSessionContext(sessions: SessionItem[], activeId: string): SessionContext {
  const active = sessions.find((s) => s.id === activeId)
  const companion = sessions.find((s) => s.id === COMPANION_SESSION_ID)
  return {
    activeId,
    activeTitle: active?.title ?? (activeId === COMPANION_SESSION_ID ? 'Companion 默认会话' : activeId),
    companionId: COMPANION_SESSION_ID,
    companionTitle: companion?.title ?? 'Companion 默认会话',
    companionMessageCount: companion?.messageCount ?? 0,
    companionUpdatedAt: companion?.updatedAt ?? '',
    viewingCompanion: activeId === COMPANION_SESSION_ID,
    hasCompanionSession: companion !== undefined,
  }
}

function rolesToProfiles(roles: RolePayload[]): RoleProfile[] {
  return roles
    .filter((r) => !r.archived)
    .map((r) => ({
      id: r.id,
      name: r.name || '未命名角色',
      description: r.description ?? '',
      persona: r.persona ?? '',
      style: r.style ?? '',
      provider: r.provider ?? '',
      model: r.model ?? '',
      live2dModel: r.live2dModel ?? '',
      ttsVoice: r.ttsVoice ?? '',
      runtimeScope: r.runtimeScope,
    }))
}

function memoryItemsToItems(items: MemoryItemPayload[]): MemoryItem[] {
  return items.map((item) => ({
    id: String(item.memoryItemId),
    scope: item.scope,
    content: item.content ?? '',
    confidence: item.confidence ?? 0,
    updatedAt: relativeLabel(item.updatedAt),
  }))
}

const scheduledStatusLabels: Record<ScheduledJobRecord['status'], string> = {
  scheduled: '已启用',
  running: '执行中',
  paused: '已暂停',
  completed: '已完成',
  cancelled: '已取消',
  failed: '失败',
}

const scheduledStatusTones: Record<ScheduledJobRecord['status'], ToolTone> = {
  scheduled: 'success',
  running: 'info',
  paused: 'warning',
  completed: 'neutral',
  cancelled: 'neutral',
  failed: 'danger',
}

function scheduledToItems(jobs: ScheduledJobRecord[]): ScheduledJob[] {
  return jobs.map((job) => ({
    id: job.id,
    title: job.title || '未命名定时任务',
    mode: job.mode ?? 'message',
    lastTaskId: job.lastTaskId ?? null,
    schedule: job.scheduleDisplay || '',
    nextRun: relativeLabel(job.nextRunAt),
    lastRun: relativeLabel(job.lastRunAt),
    repeat: job.repeatCount ?? 0,
    completedRuns: job.completedRuns ?? 0,
    status: job.status,
    statusLabel: scheduledStatusLabels[job.status] ?? job.status,
    statusTone: scheduledStatusTones[job.status] ?? 'neutral',
    enabled: job.status === 'scheduled' || job.status === 'running',
  }))
}

function ensurePendingAssistant(turnId?: string): ChatMessage {
  if (pendingAssistantId.value) {
    const existing = state.chat.find((m) => m.id === pendingAssistantId.value)
    if (existing) {
      if (turnId) existing.turnId = turnId
      return existing
    }
  }
  return createAssistantMessage(turnId)
}

async function loadSessionData(sessionId: string): Promise<void> {
  const [messages, plan, planRuns, tasksResult, scheduledResult] = await Promise.all([
    fetchSessionMessages(sessionId),
    fetchSessionPlan(sessionId),
    fetchSessionPlanRuns(sessionId),
    fetchTasks(sessionId),
    fetchScheduledJobs(sessionId),
  ])
  state.chat = messagesToChat(messages)
  state.plan = planToItems(plan)
  attachPlanRunsToMessages(planRuns)
  if (!planRuns.length) attachLoadedPlanToLatestTurn(state.plan)
  state.tasks = tasksToItems(tasksResult.tasks)
  state.scheduledJobs = scheduledToItems(scheduledResult.jobs)
  state.scheduledCount = scheduledResult.jobs.length
  pendingAssistantId.value = null
  activeTurnId.value = null
  activeTurnUserMessageId.value = null
}

async function refreshPlanAndTasks(): Promise<void> {
  const [plan, tasksResult] = await Promise.all([
    fetchSessionPlan(state.activeSessionId),
    fetchTasks(state.activeSessionId),
  ])
  state.plan = planToItems(plan)
  syncExistingPlanSnapshots(state.plan)
  state.tasks = tasksToItems(tasksResult.tasks)
}

async function loadSessionList(): Promise<void> {
  const sessions = await fetchSessions()
  state.sessions = sessionsToItems(sessions, state.activeSessionId)
  state.sessionContext = buildSessionContext(state.sessions, state.activeSessionId)
  const current = sessions.find((s) => s.id === state.activeSessionId)
  if (current) {
    state.roleName = current.roleName || 'Amadeus'
    activeRoleId = current.roleId || activeRoleId
  }
  syncActiveRole()
}

function syncActiveRole(): void {
  state.activeRole = state.roles.find((r) => r.id === activeRoleId) ?? state.roles[0] ?? null
}

async function loadRoles(): Promise<void> {
  state.roles = rolesToProfiles(await fetchRoles())
  syncActiveRole()
}

async function loadMemoryItems(): Promise<void> {
  state.memoryItems = memoryItemsToItems(await fetchMemoryItems())
}

async function loadMemoryDiagnostics(): Promise<void> {
  state.memoryContextDiagnostics = await fetchMemoryContextDiagnostics(state.activeSessionId)
}

async function loadConfigOptions(): Promise<void> {
  const [presets, live2dModels, ttsVoices, runtimeConfig, embeddingConfig, audioConfig, live2dBehaviors] =
    await Promise.all([
      fetchProviderPresets(),
      fetchLive2dModels(),
      fetchTtsVoices(),
      fetchRuntimeConfig(),
      fetchEmbeddingConfig(),
      fetchAudioConfig(),
      fetchLive2dBehaviors(),
    ])
  state.providerPresets = presets
  state.live2dModels = live2dModels
  state.ttsVoices = ttsVoices.voices
  state.ttsProvider = ttsVoices.provider
  state.ttsSupportsEnumeration = ttsVoices.supportsEnumeration
  state.runtimeConfig = runtimeConfig
  state.embeddingConfig = embeddingConfig
  state.audioConfig = audioConfig
  state.live2dBehaviors = live2dBehaviors
}

async function loadToolDiagnostics(): Promise<void> {
  const [toolsConfig, effectiveTools, auditRecords] = await Promise.all([
    fetchToolsConfig(),
    fetchToolsList(state.activeSessionId),
    fetchToolAudit({ sessionId: state.activeSessionId, limit: 100 }),
  ])
  state.toolsConfig = toolsConfig
  state.effectiveTools = effectiveTools
  state.toolAuditRecords = auditRecords
  state.mcpAuditRecords = auditRecords.filter((record) => record.toolName.startsWith('mcp__')).slice(-20)
}

async function bootstrap(): Promise<void> {
  await Promise.all([
    loadSessionList(),
    loadSessionData(state.activeSessionId),
    loadRoles(),
    loadMemoryItems(),
    loadMemoryDiagnostics(),
    loadConfigOptions(),
    loadToolDiagnostics(),
  ])
  state.skills = skillsToItems(await fetchSkills())
}

function createClient(): AgentRuntimeClient {
  return new AgentRuntimeClient(
    {
      onConnectionChange: (phase) => {
        state.connection = connectionFromPhase(phase)
      },
      onSessionId: (sessionId) => {
        state.activeSessionId = sessionId
        state.sessionContext = buildSessionContext(state.sessions, state.activeSessionId)
      },
      onTurnStarted: (turnId) => {
        bindTurnToLatestUser(turnId)
      },
      onAssistantReasoningDelta: (text, turnId) => {
        const message = ensurePendingAssistant(turnId)
        message.reasoning = `${message.reasoning ?? ''}${text}`
        message.pending = true
      },
      onAssistantDelta: (text, turnId) => {
        const message = ensurePendingAssistant(turnId)
        message.content += text
        message.pending = true
      },
      onAssistantMessage: (text, turnId) => {
        const pendingId = pendingAssistantId.value
        const message = pendingAssistantId.value
          ? state.chat.find((m) => m.id === pendingAssistantId.value)
          : null
        if (message) {
          if (turnId) message.turnId = turnId
          if (text) message.content = text
          message.pending = false
        } else if (text) {
          state.chat.push({
            id: `a-${Date.now()}`,
            role: 'assistant',
            content: text,
            createdAt: timeLabel(),
            turnId,
          })
        }
        if (turnId || pendingId) {
          archivePlanForTurn(turnId ?? activeTurnId.value)
        }
        pendingAssistantId.value = null
      },
      onToolStarted: (_toolName, displayName) => {
        const message = pendingAssistantId.value
          ? state.chat.find((candidate) => candidate.id === pendingAssistantId.value)
          : null
        if (message?.role === 'assistant') message.toolName = displayName
      },
      onToolFinished: (toolName) => {
        if (toolName.startsWith('mcp__')) {
          void loadToolDiagnostics()
        }
      },
      onToolPermissionRequest: (prompt) => {
        state.toolPermission = prompt
      },
      onToolPermissionResolved: () => {
        state.toolPermission = null
      },
      onSkillStarted: (skillName, displayName) => {
        const id = displayName || skillName
        const existing = state.activeSkills.find((skill) => skill.id === id || skill.name === skillName)
        if (existing) {
          existing.status = 'loading'
          existing.failureCode = null
          return
        }
        state.activeSkills.push({
          id,
          name: skillName,
          displayName,
          status: 'loading',
        })
      },
      onSkillFinished: (skillName, displayName, ok, identifier, failureCode) => {
        const id = identifier || displayName || skillName
        const existing = state.activeSkills.find(
          (skill) => skill.id === id || skill.name === skillName || skill.displayName === displayName,
        )
        if (existing) {
          existing.id = id
          existing.name = skillName
          existing.displayName = displayName || id
          existing.status = ok ? 'active' : 'failed'
          existing.failureCode = failureCode
          return
        }
        state.activeSkills.push({
          id,
          name: skillName,
          displayName: displayName || id,
          status: ok ? 'active' : 'failed',
          failureCode,
        })
      },
      onPlanUpdated: (payload) => {
        if (payload.sessionId && payload.sessionId !== state.activeSessionId) return
        const items = planToItems(payload)
        state.plan = items
        attachPlanToTurn(items, payload.turnId ?? activeTurnId.value)
      },
      onTaskUpdated: (payload: TaskUpdatedPayload) => {
        if (payload.task.sessionId && payload.task.sessionId !== state.activeSessionId) return
        maybeNotifyTask(payload.task, payload.action)
        void refreshPlanAndTasks()
      },
      onScheduledJobUpdated: (payload: ScheduledJobUpdatedPayload) => {
        if (payload.job.sessionId && payload.job.sessionId !== state.activeSessionId) return
        void fetchScheduledJobs(state.activeSessionId).then((result) => {
          state.scheduledJobs = scheduledToItems(result.jobs)
          state.scheduledCount = result.jobs.length
        })
      },
      onMemoryContextUsed: (payload) => {
        if (payload.sessionId && payload.sessionId !== state.activeSessionId) return
        state.memoryContextDiagnostics = [...state.memoryContextDiagnostics, payload].slice(-8)
      },
      onError: (message) => {
        const pending = pendingAssistantId.value
          ? state.chat.find((m) => m.id === pendingAssistantId.value)
          : null
        if (pending) {
          pending.content = `出错了：${message}`
          pending.pending = false
        } else {
          state.chat.push({
            id: `err-${Date.now()}`,
            role: 'assistant',
            content: `出错了：${message}`,
            createdAt: timeLabel(),
          })
        }
        pendingAssistantId.value = null
      },
    },
    state.activeSessionId,
  )
}

export function useRuntime() {
  if (!started) {
    started = true
    client = createClient()
    void bootstrap()
    client.connect()
  }

  function sendMessage(text: string): void {
    const trimmed = text.trim()
    if (!trimmed) return
    state.activeSkills = []
    const userMessage: ChatMessage = {
      id: `u-${Date.now()}`,
      role: 'user',
      content: trimmed,
      createdAt: timeLabel(),
    }
    state.chat.push(userMessage)
    activeTurnId.value = null
    activeTurnUserMessageId.value = userMessage.id
    state.plan = []
    pendingAssistantId.value = null
    client?.sendUserMessage(trimmed, [...state.suggestedSkillIds])
  }

  function toggleSuggestedSkill(id: string): void {
    if (state.suggestedSkillIds.includes(id)) {
      state.suggestedSkillIds = state.suggestedSkillIds.filter((skillId) => skillId !== id)
      return
    }
    state.suggestedSkillIds = [...state.suggestedSkillIds, id]
  }

  function selectSession(id: string): void {
    if (id === state.activeSessionId) return
    const url = new URL(window.location.href)
    url.searchParams.set('sessionId', id)
    window.location.href = url.toString()
  }

  function selectCompanionSession(): void {
    selectSession(COMPANION_SESSION_ID)
  }

  async function createSession(): Promise<void> {
    const session = await createSessionRequest(activeRoleId)
    if (!session) return
    selectSession(session.id)
  }

  async function deleteSession(id: string): Promise<void> {
    const ok = await deleteSessionRequest(id)
    if (!ok) return
    if (id === state.activeSessionId) {
      const next = state.sessions.find((s) => s.id !== id)
      if (next) {
        selectSession(next.id)
      } else {
        await createSession()
      }
      return
    }
    state.sessions = state.sessions.filter((s) => s.id !== id)
  }

  async function updateRole(id: string, update: RoleUpdate): Promise<boolean> {
    const role = await updateRoleRequest(id, update)
    if (!role) return false
    await loadRoles()
    const current = state.sessions.find((s) => s.id === state.activeSessionId)
    if (!current || current.roleName === role.name || id === activeRoleId) {
      state.roleName = role.name || state.roleName
    }
    return true
  }

  function respondPermission(approved: boolean): void {
    if (!state.toolPermission) return
    client?.respondToToolPermission(state.toolPermission.requestId, approved)
    state.toolPermission = null
  }

  async function saveApiConfig(update: RuntimeApiUpdate): Promise<boolean> {
    const result = await updateRuntimeApiConfig(update)
    if (!result) return false
    state.runtimeConfig = result
    state.providerPresets = result.presets
    return true
  }

  async function saveAudioConfig(update: AudioConfigUpdate): Promise<boolean> {
    const result = await updateAudioConfig(update)
    if (!result) return false
    state.audioConfig = result
    state.ttsProvider = result.runtimeProvider
    state.ttsVoices = result.voices
    return true
  }

  async function saveLive2dBehaviors(
    behaviors: Record<string, Live2dBehavior>,
  ): Promise<boolean> {
    const result = await updateLive2dBehaviors(behaviors)
    if (!result) return false
    state.live2dBehaviors = result
    return true
  }

  async function importLive2d(
    sourceDir: string,
    options: { modelId?: string; activate?: boolean } = {},
  ): Promise<Live2dImportResult | { error: string }> {
    const result = await importLive2dModel(sourceDir, options)
    if ('error' in result) return result
    state.live2dModels = result.models
    return result
  }

  async function selectLive2d(modelId: string): Promise<boolean> {
    const ok = await selectLive2dModel(modelId)
    if (!ok) return false
    state.live2dModels = await fetchLive2dModels()
    state.live2dBehaviors = await fetchLive2dBehaviors()
    return true
  }

  async function refreshMcpDiagnostics(): Promise<void> {
    await loadToolDiagnostics()
  }

  async function refreshMemoryDiagnostics(): Promise<void> {
    await loadMemoryDiagnostics()
  }

  async function refreshEmbeddingConfig(): Promise<void> {
    state.embeddingConfig = await fetchEmbeddingConfig()
  }

  async function deployEmbedding(localDir?: string, force = false): Promise<boolean> {
    const result = await deployEmbeddingModel({ localDir, force })
    if (!result) return false
    state.embeddingConfig = result
    return true
  }

  async function cancelEmbedding(): Promise<boolean> {
    const result = await cancelEmbeddingDeploy()
    if (!result) return false
    state.embeddingConfig = result
    return true
  }

  async function backfillEmbedding(limit = 100, batchSize = 8): Promise<boolean> {
    const result = await backfillEmbeddingIndex({ limit, batchSize })
    if (!result) return false
    state.embeddingConfig = result
    return true
  }

  async function refreshTasks(): Promise<void> {
    await refreshPlanAndTasks()
  }

  async function createTaskFromPlan(item: PlanItem): Promise<boolean> {
    const task = await createTaskRequest({
      sessionId: state.activeSessionId,
      title: item.label,
      body: item.label,
      kind: 'agent_turn',
      source: 'plan',
      planItemId: item.id,
      priority: item.status === 'active' ? 1 : 0,
    })
    if (!task) return false
    await refreshPlanAndTasks()
    return true
  }

  async function loadTaskEvents(taskId: string): Promise<TaskEventItem[]> {
    return fetchTaskEvents(taskId)
  }

  async function cancelTask(taskId: string): Promise<boolean> {
    const task = await cancelTaskRequest(taskId)
    if (!task) return false
    await refreshPlanAndTasks()
    return true
  }

  async function resumeTask(taskId: string): Promise<boolean> {
    const task = await resumeTaskRequest(taskId)
    if (!task) return false
    await refreshPlanAndTasks()
    return true
  }

  async function approveTask(taskId: string): Promise<boolean> {
    const task = await approveTaskRequest(taskId)
    if (!task) return false
    maybeNotifyTask(task, 'review_approved')
    await refreshPlanAndTasks()
    return true
  }

  async function rerunTask(task: TaskItem): Promise<boolean> {
    const created = await createTaskRequest({
      sessionId: state.activeSessionId,
      title: task.title,
      body: task.detail || task.title,
      kind: task.kind,
      source: task.source === 'scheduled_job' ? 'api' : task.source || 'api',
      parentTaskId: task.id,
      planItemId: task.planItemId ?? undefined,
      workerType: task.workerType,
      artifacts: [
        ...task.artifacts,
        { type: 'rerun', sourceTaskId: task.id },
      ],
    })
    if (!created) return false
    await refreshPlanAndTasks()
    return true
  }

  async function createScheduledJob(input: {
    title?: string
    message: string
    schedule: string
    mode: 'message' | 'agent_task'
    repeatCount?: number | null
  }): Promise<boolean> {
    const job = await createScheduledJobRequest({
      sessionId: state.activeSessionId,
      ...input,
    })
    if (!job) return false
    const result = await fetchScheduledJobs(state.activeSessionId)
    state.scheduledJobs = scheduledToItems(result.jobs)
    state.scheduledCount = result.jobs.length
    return true
  }

  return {
    state,
    sendMessage,
    toggleSuggestedSkill,
    selectSession,
    selectCompanionSession,
    createSession,
    deleteSession,
    updateRole,
    respondPermission,
    saveApiConfig,
    saveAudioConfig,
    saveLive2dBehaviors,
    importLive2d,
    selectLive2d,
    refreshMcpDiagnostics,
    refreshMemoryDiagnostics,
    refreshEmbeddingConfig,
    deployEmbedding,
    cancelEmbedding,
    backfillEmbedding,
    refreshTasks,
    createTaskFromPlan,
    loadTaskEvents,
    cancelTask,
    resumeTask,
    approveTask,
    rerunTask,
    createScheduledJob,
  }
}
