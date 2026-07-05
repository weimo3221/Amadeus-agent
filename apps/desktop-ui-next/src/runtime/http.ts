import type {
  MemoryContextUsedPayload,
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
  live2dModel?: string
  ttsVoice?: string
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
    `/tasks?sessionId=${encodeURIComponent(sessionId)}&activeOnly=false&limit=${limit}`,
  )
  return { tasks: data?.tasks ?? [], summary: data?.summary ?? null }
}

export async function createTaskRequest(body: {
  sessionId: string
  title: string
  body?: string
  kind?: string
  source?: string
  parentTaskId?: string | null
  planItemId?: string
  workerType?: string
  artifacts?: Array<Record<string, unknown>>
  priority?: number
}): Promise<TaskRecord | null> {
  const data = await postJson<{ task: TaskRecord }>('/tasks', body)
  return data?.task ?? null
}

export interface TaskEventPayload {
  eventId: number
  taskId: string
  sessionId: string
  type: string
  status?: string | null
  message?: string | null
  metadata?: unknown
  createdAt: string
}

export async function fetchTaskEvents(taskId: string): Promise<TaskEventPayload[]> {
  const data = await getJson<{ events: TaskEventPayload[] }>(
    `/tasks/${encodeURIComponent(taskId)}/events?limit=100`,
  )
  return data?.events ?? []
}

export async function cancelTaskRequest(taskId: string, reason?: string): Promise<TaskRecord | null> {
  const data = await postJson<{ task: TaskRecord }>(
    `/tasks/${encodeURIComponent(taskId)}/cancel`,
    { reason: reason ?? 'User cancelled from Main UI' },
  )
  return data?.task ?? null
}

export async function fetchScheduledJobs(
  sessionId: string,
  limit = 20,
): Promise<{ jobs: ScheduledJobRecord[]; summary: ScheduledJobSummary | null }> {
  const data = await getJson<{ jobs: ScheduledJobRecord[]; summary: ScheduledJobSummary }>(
    `/scheduled-jobs?sessionId=${encodeURIComponent(sessionId)}&activeOnly=false&limit=${limit}`,
  )
  return { jobs: data?.jobs ?? [], summary: data?.summary ?? null }
}

export async function createScheduledJobRequest(body: {
  sessionId: string
  title?: string
  message: string
  schedule: string
  mode: 'message' | 'agent_task'
  repeatCount?: number | null
}): Promise<ScheduledJobRecord | null> {
  const data = await postJson<{ job: ScheduledJobRecord }>('/scheduled-jobs', body)
  return data?.job ?? null
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

export async function fetchMemoryContextDiagnostics(
  sessionId: string,
  limit = 8,
): Promise<MemoryContextUsedPayload[]> {
  const data = await getJson<{ diagnostics: MemoryContextUsedPayload[] }>(
    `/memory/context/diagnostics?sessionId=${encodeURIComponent(sessionId)}&limit=${limit}`,
  )
  return data?.diagnostics ?? []
}

export interface ProviderPreset {
  id: string
  label: string
  apiMode: string
  envVar: string
  baseUrl: string
  defaultModel: string
  requiresApiKey: boolean
  supportsStreaming: boolean
}

export async function fetchProviderPresets(): Promise<ProviderPreset[]> {
  const data = await getJson<{ presets?: ProviderPreset[]; providers?: ProviderPreset[] }>(
    '/runtime/config',
  )
  return data?.presets ?? data?.providers ?? []
}

export interface Live2dModelPayload {
  id: string
  path: string
  url: string
  active: boolean
}

export async function fetchLive2dModels(): Promise<Live2dModelPayload[]> {
  const data = await getJson<{ models: Live2dModelPayload[] }>('/live2d/models')
  return data?.models ?? []
}

export interface RuntimeApiConfig {
  provider: string
  providerLabel: string
  envVar: string
  requiresApiKey: boolean
  baseUrl: string
  model: string
  streaming: boolean
  maxTokens: number
  thinkingEnabled: boolean
  reasoningEffort: 'low' | 'medium' | 'high'
  apiKeyConfigured: boolean
  apiKeyPreview: string
}

export interface ProviderProfile {
  id: string
  label: string
  apiMode: string
  envVar: string
  baseUrl: string
  defaultModel: string
  requiresApiKey: boolean
  supportsStreaming: boolean
  maxTokens: number
  thinkingEnabled: boolean
  reasoningEffort: 'low' | 'medium' | 'high'
}

export interface RuntimeConfigResult {
  api: RuntimeApiConfig
  providers: ProviderProfile[]
  presets: ProviderPreset[]
  paths: { env: string; providersConfig: string; runtimeConfig: string }
}

export interface McpServerPayload {
  name: string
  url: string
  enabled: boolean
  permission: string
  timeoutSeconds: number
}

export interface McpConfigPayload {
  enabled: boolean
  permission: string
  servers: McpServerPayload[]
}

export interface ToolsConfigResult {
  mcp: McpConfigPayload
  paths: { toolsConfig: string }
  tools: Array<{ name: string; displayName?: string; permission?: string; enabled?: boolean }>
  schemas: Array<{ function?: { name?: string }; name?: string }>
}

export interface ToolAuditRecordPayload {
  recordId: string
  timestamp: string
  sessionId: string
  toolName: string
  decision: string
  ok?: boolean
  durationMs?: number
  failureCode?: string
  detail?: string
}

export interface RuntimeApiUpdate {
  provider?: string
  baseUrl?: string
  model?: string
  apiKey?: string
  streaming?: boolean
  maxTokens?: number
  thinkingEnabled?: boolean
  reasoningEffort?: 'low' | 'medium' | 'high'
  requiresApiKey?: boolean
  envVar?: string
  label?: string
}

export async function fetchRuntimeConfig(): Promise<RuntimeConfigResult | null> {
  const data = await getJson<RuntimeConfigResult>('/runtime/config')
  if (!data) return null
  return {
    api: data.api,
    providers: data.providers ?? [],
    presets: data.presets ?? [],
    paths: data.paths,
  }
}

export async function fetchToolsConfig(): Promise<ToolsConfigResult | null> {
  return getJson<ToolsConfigResult>('/tools/config')
}

export async function fetchToolAudit(
  options: { sessionId?: string; toolName?: string; limit?: number } = {},
): Promise<ToolAuditRecordPayload[]> {
  const params = new URLSearchParams()
  if (options.sessionId) params.set('sessionId', options.sessionId)
  if (options.toolName) params.set('toolName', options.toolName)
  params.set('limit', String(options.limit ?? 20))
  const data = await getJson<{ records: ToolAuditRecordPayload[] }>(`/tools/audit?${params.toString()}`)
  return data?.records ?? []
}

export async function updateRuntimeApiConfig(
  update: RuntimeApiUpdate,
): Promise<RuntimeConfigResult | null> {
  const data = await postJson<RuntimeConfigResult>('/runtime/config', { api: update })
  if (!data) return null
  return {
    api: data.api,
    providers: data.providers ?? [],
    presets: data.presets ?? [],
    paths: data.paths,
  }
}

export interface TtsProviderType {
  id: string
  label: string
  type: string
}

export interface MacosTtsConfig {
  voice: string
  rate: string
}

export interface GptSovitsConfig {
  baseUrl: string
  endpoint: string
  textLang: string
  promptLang: string
  promptText: string
  refAudioPath: string
  timeoutSeconds: string
  streamingMode: boolean
}

export interface AudioConfigResult {
  activeProvider: string
  runtimeProvider: string
  providerTypes: TtsProviderType[]
  macosAvailable: boolean
  macos: MacosTtsConfig
  gptSovits: GptSovitsConfig
  voices: TtsVoicePayload[]
  paths: { env: string; providersConfig: string }
}

export interface AudioConfigUpdate {
  provider?: string
  macos?: Partial<MacosTtsConfig>
  gptSovits?: Partial<GptSovitsConfig>
}

export async function fetchAudioConfig(): Promise<AudioConfigResult | null> {
  return getJson<AudioConfigResult>('/audio/config')
}

export async function updateAudioConfig(
  update: AudioConfigUpdate,
): Promise<AudioConfigResult | null> {
  return postJson<AudioConfigResult>('/audio/config', update)
}

export interface Live2dBehavior {
  emotion?: string
  expression?: string
  motion?: string
  intensity?: number
}

export interface Live2dBehaviorState {
  id: string
  label: string
}

export interface Live2dBehaviorsResult {
  states: Live2dBehaviorState[]
  audioPlaybackBehaviors: Record<string, Live2dBehavior>
  defaults: Record<string, Live2dBehavior>
  suggestions: { expressions: string[]; motions: string[] }
  paths: { harnessesConfig: string }
}

export async function fetchLive2dBehaviors(): Promise<Live2dBehaviorsResult | null> {
  return getJson<Live2dBehaviorsResult>('/live2d/behaviors')
}

export async function updateLive2dBehaviors(
  behaviors: Record<string, Live2dBehavior>,
): Promise<Live2dBehaviorsResult | null> {
  return postJson<Live2dBehaviorsResult>('/live2d/behaviors', {
    audioPlaybackBehaviors: behaviors,
  })
}

export interface Live2dImportResult {
  model: { id: string; path: string; url: string }
  models: Live2dModelPayload[]
}

export async function importLive2dModel(
  sourceDir: string,
  options: { modelId?: string; activate?: boolean } = {},
): Promise<Live2dImportResult | { error: string }> {
  try {
    const response = await fetch(`${AGENT_HTTP_URL}/live2d/import`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
      body: JSON.stringify({
        sourceDir,
        modelId: options.modelId,
        activate: options.activate ?? true,
      }),
    })
    const data = (await response.json()) as { ok?: boolean; error?: string } & Live2dImportResult
    if (!response.ok || data.ok === false) {
      return { error: data.error ?? '导入失败' }
    }
    return { model: data.model, models: data.models ?? [] }
  } catch (error) {
    return { error: error instanceof Error ? error.message : '导入失败' }
  }
}

export async function selectLive2dModel(modelId: string): Promise<boolean> {
  const data = await postJson<{ model: unknown }>('/live2d/select', { modelId })
  return data !== null
}

export interface TtsVoicePayload {
  id: string
  label: string
  locale?: string
  sample?: string
}

export interface TtsVoicesResult {
  provider: string
  supportsEnumeration: boolean
  voices: TtsVoicePayload[]
}

export async function fetchTtsVoices(): Promise<TtsVoicesResult> {
  const data = await getJson<TtsVoicesResult>('/audio/voices')
  return {
    provider: data?.provider ?? 'none',
    supportsEnumeration: data?.supportsEnumeration ?? false,
    voices: data?.voices ?? [],
  }
}
