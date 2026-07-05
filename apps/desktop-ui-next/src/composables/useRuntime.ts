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
  ConnectionState,
  MemoryItem,
  PlanItem,
  RoleProfile,
  SessionContext,
  ScheduledJob,
  SkillActivation,
  SessionItem,
  SkillItem,
  TaskItem,
  TaskStatus,
  ToolTone,
} from '@/types'
import { AgentRuntimeClient, type ConnectionPhase, type ToolPermissionPrompt } from '@/runtime/client'
import { COMPANION_SESSION_ID, SESSION_ID } from '@/runtime/config'
import {
  createSessionRequest,
  deleteSessionRequest,
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
  fetchSessions,
  fetchSkills,
  fetchToolAudit,
  fetchToolsConfig,
  fetchTasks,
  fetchTtsVoices,
  importLive2dModel,
  selectLive2dModel,
  updateAudioConfig,
  updateLive2dBehaviors,
  updateRoleRequest,
  updateRuntimeApiConfig,
  type AudioConfigResult,
  type AudioConfigUpdate,
  type Live2dBehavior,
  type Live2dBehaviorsResult,
  type Live2dImportResult,
  type Live2dModelPayload,
  type MemoryItemPayload,
  type ProviderPreset,
  type RolePayload,
  type RoleUpdate,
  type RuntimeApiUpdate,
  type RuntimeConfigResult,
  type SessionPayload,
  type SkillPayload,
  type StoredMessage,
  type ToolAuditRecordPayload,
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
  audioConfig: AudioConfigResult | null
  live2dBehaviors: Live2dBehaviorsResult | null
  toolsConfig: ToolsConfigResult | null
  mcpAuditRecords: ToolAuditRecordPayload[]
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
  audioConfig: null,
  live2dBehaviors: null,
  toolsConfig: null,
  mcpAuditRecords: [],
})

let client: AgentRuntimeClient | null = null
let started = false
let activeRoleId = 'amadeus'
const pendingAssistantId = ref<string | null>(null)

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

function messagesToChat(messages: StoredMessage[]): ChatMessage[] {
  return messages
    .filter((m) => m.role === 'user' || m.role === 'assistant')
    .map((m, index) => ({
      id: `h-${index}`,
      role: m.role === 'user' ? 'user' : 'assistant',
      content: m.content ?? '',
      createdAt: '',
    }))
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
  }))
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

function ensurePendingAssistant(): ChatMessage {
  if (pendingAssistantId.value) {
    const existing = state.chat.find((m) => m.id === pendingAssistantId.value)
    if (existing) return existing
  }
  const message: ChatMessage = {
    id: `a-${Date.now()}`,
    role: 'assistant',
    content: '',
    createdAt: timeLabel(),
    pending: true,
  }
  state.chat.push(message)
  pendingAssistantId.value = message.id
  return message
}

async function loadSessionData(sessionId: string): Promise<void> {
  const [messages, plan, tasksResult, scheduledResult] = await Promise.all([
    fetchSessionMessages(sessionId),
    fetchSessionPlan(sessionId),
    fetchTasks(sessionId),
    fetchScheduledJobs(sessionId),
  ])
  state.chat = messagesToChat(messages)
  state.plan = planToItems(plan)
  state.tasks = tasksToItems(tasksResult.tasks)
  state.scheduledJobs = scheduledToItems(scheduledResult.jobs)
  state.scheduledCount = scheduledResult.jobs.length
  pendingAssistantId.value = null
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
  const [presets, live2dModels, ttsVoices, runtimeConfig, audioConfig, live2dBehaviors] =
    await Promise.all([
      fetchProviderPresets(),
      fetchLive2dModels(),
      fetchTtsVoices(),
      fetchRuntimeConfig(),
      fetchAudioConfig(),
      fetchLive2dBehaviors(),
    ])
  state.providerPresets = presets
  state.live2dModels = live2dModels
  state.ttsVoices = ttsVoices.voices
  state.ttsProvider = ttsVoices.provider
  state.ttsSupportsEnumeration = ttsVoices.supportsEnumeration
  state.runtimeConfig = runtimeConfig
  state.audioConfig = audioConfig
  state.live2dBehaviors = live2dBehaviors
}

async function loadToolDiagnostics(): Promise<void> {
  const [toolsConfig, auditRecords] = await Promise.all([
    fetchToolsConfig(),
    fetchToolAudit({ sessionId: state.activeSessionId, limit: 100 }),
  ])
  state.toolsConfig = toolsConfig
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
      onAssistantReasoningDelta: (text) => {
        const message = ensurePendingAssistant()
        message.reasoning = `${message.reasoning ?? ''}${text}`
        message.pending = true
      },
      onAssistantDelta: (text) => {
        const message = ensurePendingAssistant()
        message.content += text
        message.pending = true
      },
      onAssistantMessage: (text) => {
        const message = pendingAssistantId.value
          ? state.chat.find((m) => m.id === pendingAssistantId.value)
          : null
        if (message) {
          if (text) message.content = text
          message.pending = false
        } else if (text) {
          state.chat.push({
            id: `a-${Date.now()}`,
            role: 'assistant',
            content: text,
            createdAt: timeLabel(),
          })
        }
        pendingAssistantId.value = null
      },
      onToolStarted: (_toolName, displayName) => {
        const message = ensurePendingAssistant()
        message.toolName = displayName
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
        state.plan = planToItems(payload)
      },
      onTaskUpdated: (payload: TaskUpdatedPayload) => {
        if (payload.task.sessionId && payload.task.sessionId !== state.activeSessionId) return
        void fetchTasks(state.activeSessionId).then((result) => {
          state.tasks = tasksToItems(result.tasks)
        })
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
    state.chat.push({
      id: `u-${Date.now()}`,
      role: 'user',
      content: trimmed,
      createdAt: timeLabel(),
    })
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
  }
}
