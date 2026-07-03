import type {
  ScheduledJobRecord,
  ScheduledJobSummary,
  TaskPlanPayload,
  TaskRecord,
  TaskSummary,
} from '@amadeus-agent/amadeus/events'
import { AGENT_HTTP_URL } from './config'

export interface SessionPayload {
  id: string
  roleId: string
  title: string
  archived: boolean
  roleName: string
  messageCount: number
  createdAt: string
  updatedAt: string
}

export interface StoredMessage {
  role?: string
  content?: string
}

export interface SkillPayload {
  name: string
  identifier: string
  description: string
  category?: string
}

export interface RolePayload {
  id: string
  name: string
  description: string
  persona: string
  style: string
  provider: string
  model: string
  live2dModel: string
  ttsVoice: string
  archived: boolean
  createdAt: string
  updatedAt: string
}

export interface RoleUpdate {
  name?: string
  persona?: string
  style?: string
  provider?: string
  model?: string
}

async function getJson<T>(path: string): Promise<T | null> {
  try {
    const response = await fetch(`${AGENT_HTTP_URL}${path}`, {
      headers: { Accept: 'application/json' },
    })
    if (!response.ok) return null
    const data = (await response.json()) as { ok?: boolean } & T
    if (data && data.ok === false) return null
    return data
  } catch {
    return null
  }
}

async function postJson<T>(path: string, body: unknown): Promise<T | null> {
  try {
    const response = await fetch(`${AGENT_HTTP_URL}${path}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
      body: JSON.stringify(body),
    })
    if (!response.ok) return null
    const data = (await response.json()) as { ok?: boolean } & T
    if (data && data.ok === false) return null
    return data
  } catch {
    return null
  }
}

async function sendJson<T>(method: 'PUT' | 'DELETE', path: string, body?: unknown): Promise<T | null> {
  try {
    const response = await fetch(`${AGENT_HTTP_URL}${path}`, {
      method,
      headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
      ...(body === undefined ? {} : { body: JSON.stringify(body) }),
    })
    if (!response.ok) return null
    const data = (await response.json()) as { ok?: boolean } & T
    if (data && data.ok === false) return null
    return data
  } catch {
    return null
  }
}

export async function fetchSessions(): Promise<SessionPayload[]> {
  const data = await getJson<{ sessions: SessionPayload[] }>('/sessions')
  return data?.sessions ?? []
}

export async function fetchSessionMessages(sessionId: string, limit = 80): Promise<StoredMessage[]> {
  const data = await getJson<{ messages: StoredMessage[] }>(
    `/memory/messages?sessionId=${encodeURIComponent(sessionId)}&limit=${limit}`,
  )
  return data?.messages ?? []
}

export async function fetchSessionPlan(sessionId: string): Promise<TaskPlanPayload | null> {
  const data = await getJson<{ plan: TaskPlanPayload }>(
    `/sessions/${encodeURIComponent(sessionId)}/plan`,
  )
  return data?.plan ?? null
}

export async function fetchTasks(
  sessionId: string,
  limit = 20,
): Promise<{ tasks: TaskRecord[]; summary: TaskSummary | null }> {
  const data = await getJson<{ tasks: TaskRecord[]; summary: TaskSummary }>(
    `/tasks?sessionId=${encodeURIComponent(sessionId)}&activeOnly=true&limit=${limit}`,
  )
  return { tasks: data?.tasks ?? [], summary: data?.summary ?? null }
}

export async function fetchScheduledJobs(
  sessionId: string,
  limit = 20,
): Promise<{ jobs: ScheduledJobRecord[]; summary: ScheduledJobSummary | null }> {
  const data = await getJson<{ jobs: ScheduledJobRecord[]; summary: ScheduledJobSummary }>(
    `/scheduled-jobs?sessionId=${encodeURIComponent(sessionId)}&activeOnly=true&limit=${limit}`,
  )
  return { jobs: data?.jobs ?? [], summary: data?.summary ?? null }
}

export async function fetchSkills(): Promise<SkillPayload[]> {
  const data = await getJson<{ skills: SkillPayload[] }>('/skills/list')
  return data?.skills ?? []
}

export async function createSessionRequest(roleId: string): Promise<SessionPayload | null> {
  const data = await postJson<{ session: SessionPayload }>('/sessions', { roleId })
  return data?.session ?? null
}

export async function deleteSessionRequest(sessionId: string): Promise<boolean> {
  const data = await sendJson<{ session: SessionPayload }>(
    'DELETE',
    `/sessions/${encodeURIComponent(sessionId)}`,
  )
  return data !== null
}

export async function fetchRoles(): Promise<RolePayload[]> {
  const data = await getJson<{ roles: RolePayload[] }>('/roles')
  return data?.roles ?? []
}

export async function updateRoleRequest(
  roleId: string,
  update: RoleUpdate,
): Promise<RolePayload | null> {
  const data = await sendJson<{ role: RolePayload }>(
    'PUT',
    `/roles/${encodeURIComponent(roleId)}`,
    update,
  )
  return data?.role ?? null
}

export interface MemoryItemPayload {
  memoryItemId: number
  scope: string
  content: string
  confidence: number
  updatedAt: string
}

export async function fetchMemoryItems(limit = 50): Promise<MemoryItemPayload[]> {
  const data = await getJson<{ items: MemoryItemPayload[] }>(`/memory/items?limit=${limit}`)
  return data?.items ?? []
}
