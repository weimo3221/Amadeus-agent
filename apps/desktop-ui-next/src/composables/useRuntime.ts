import { reactive, ref } from 'vue'
import type {
  ScheduledJobRecord,
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
  ScheduledJob,
  SessionItem,
  SkillItem,
  TaskItem,
  TaskStatus,
} from '@/types'
import { AgentRuntimeClient, type ConnectionPhase, type ToolPermissionPrompt } from '@/runtime/client'
import { SESSION_ID } from '@/runtime/config'
import {
  createSessionRequest,
  deleteSessionRequest,
  fetchMemoryItems,
  fetchRoles,
  fetchScheduledJobs,
  fetchSessionMessages,
  fetchSessionPlan,
  fetchSessions,
  fetchSkills,
  fetchTasks,
  updateRoleRequest,
  type MemoryItemPayload,
  type RolePayload,
  type RoleUpdate,
  type SessionPayload,
  type SkillPayload,
  type StoredMessage,
} from '@/runtime/http'

interface RuntimeState {
  connection: ConnectionState
  chat: ChatMessage[]
  plan: PlanItem[]
  tasks: TaskItem[]
  skills: SkillItem[]
  sessions: SessionItem[]
  roles: RoleProfile[]
  activeRole: RoleProfile | null
  memoryItems: MemoryItem[]
  scheduledJobs: ScheduledJob[]
  activeSessionId: string
  roleName: string
  scheduledCount: number
  toolPermission: ToolPermissionPrompt | null
}

const state = reactive<RuntimeState>({
  connection: 'connecting',
  chat: [],
  plan: [],
  tasks: [],
  skills: [],
  sessions: [],
  roles: [],
  activeRole: null,
  memoryItems: [],
  scheduledJobs: [],
  activeSessionId: SESSION_ID,
  roleName: 'Amadeus',
  scheduledCount: 0,
  toolPermission: null,
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

function scheduledToItems(jobs: ScheduledJobRecord[]): ScheduledJob[] {
  return jobs.map((job) => ({
    id: job.id,
    title: job.title || '未命名定时任务',
    schedule: job.scheduleDisplay || '',
    nextRun: relativeLabel(job.nextRunAt),
    repeat: job.repeatCount ?? 0,
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

async function bootstrap(): Promise<void> {
  await Promise.all([
    loadSessionList(),
    loadSessionData(state.activeSessionId),
    loadRoles(),
    loadMemoryItems(),
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
      onToolPermissionRequest: (prompt) => {
        state.toolPermission = prompt
      },
      onToolPermissionResolved: () => {
        state.toolPermission = null
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
    state.chat.push({
      id: `u-${Date.now()}`,
      role: 'user',
      content: trimmed,
      createdAt: timeLabel(),
    })
    pendingAssistantId.value = null
    client?.sendUserMessage(trimmed)
  }

  function selectSession(id: string): void {
    if (id === state.activeSessionId) return
    const url = new URL(window.location.href)
    url.searchParams.set('sessionId', id)
    window.location.href = url.toString()
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

  return {
    state,
    sendMessage,
    selectSession,
    createSession,
    deleteSession,
    updateRole,
    respondPermission,
  }
}
