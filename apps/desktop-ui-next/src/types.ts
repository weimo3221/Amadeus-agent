export type ConnectionState = 'online' | 'connecting' | 'offline'

export type ChatRole = 'user' | 'assistant' | 'system'

export interface ChatMessage {
  id: string
  messageId?: number
  role: ChatRole
  content: string
  reasoning?: string
  createdAt: string
  turnId?: string
  pending?: boolean
  toolName?: string
  plan?: PlanItem[]
  planArchived?: boolean
  planIncomplete?: boolean
  planCollapsed?: boolean
}

export interface SessionItem {
  id: string
  title: string
  roleName: string
  messageCount: number
  updatedAt: string
  active?: boolean
}

export interface SessionContext {
  activeId: string
  activeTitle: string
  companionId: string
  companionTitle: string
  companionMessageCount: number
  companionUpdatedAt: string
  viewingCompanion: boolean
  hasCompanionSession: boolean
}

export type PlanStatus = 'done' | 'active' | 'pending'

export interface PlanItem {
  id: string
  label: string
  status: PlanStatus
}

export type PlanRunStatus = 'active' | 'completed' | 'incomplete' | 'cancelled'

export interface PlanRunItem {
  turnId: string
  userMessageId?: number | null
  assistantMessageId?: number | null
  status: PlanRunStatus | string
  items: PlanItem[]
  updatedAt: string
  archivedAt?: string | null
}

export type TaskStatus = 'queued' | 'running' | 'blocked' | 'succeeded' | 'failed' | 'cancelled'

export interface TaskItem {
  id: string
  title: string
  detail: string
  result: string
  error: string
  status: TaskStatus
  updatedAt: string
  attempts: number
  maxAttempts: number
  kind: string
  source: string
  parentTaskId?: string | null
  planItemId?: string | null
  workerType: string
  blockedReason?: string | null
  reviewRequired: boolean
  dueAt?: string | null
  nextRunAt?: string | null
  leaseOwner?: string | null
  leaseExpiresAt?: string | null
  runnerKind?: string | null
  lastHeartbeat?: string | null
  finishedAt?: string | null
  artifacts: TaskArtifact[]
}

export type TaskArtifactType = 'file' | 'diff' | 'command_output' | 'summary' | 'link' | string

export interface TaskArtifact {
  type: TaskArtifactType
  title?: string
  path?: string
  url?: string
  content?: string
  summary?: string
  language?: string
  exitCode?: number | string
  sourceTaskId?: string
  jobId?: string
  [key: string]: unknown
}

export interface TaskNotification {
  id: string
  taskId: string
  title: string
  status: TaskStatus
  tone: ToolTone
  createdAt: string
}

export interface TaskEventItem {
  eventId: number
  type: string
  status?: string | null
  message?: string | null
  metadata?: unknown
  createdAt: string
}

export type ToolTone = 'brand' | 'success' | 'warning' | 'danger' | 'info' | 'neutral'

export interface StatusTile {
  key: string
  label: string
  value: string
  icon: string
  tone: ToolTone
}

export interface SkillItem {
  id: string
  name: string
  category: string
  summary: string
  score?: number
}

export type SkillActivationStatus = 'loading' | 'active' | 'failed'

export interface SkillActivation {
  id: string
  name: string
  displayName: string
  status: SkillActivationStatus
  failureCode?: string | null
}

export interface ScheduledJob {
  id: string
  title: string
  mode: 'message' | 'agent_task' | string
  lastTaskId?: string | null
  schedule: string
  nextRun: string
  lastRun: string
  repeat: number
  completedRuns: number
  status: 'scheduled' | 'running' | 'paused' | 'completed' | 'cancelled' | 'failed'
  statusLabel: string
  statusTone: ToolTone
  enabled: boolean
}

export interface RoleProfile {
  id: string
  name: string
  description: string
  persona: string
  style: string
  provider: string
  model: string
  live2dModel: string
  ttsVoice: string
  runtimeScope?: {
    tools: string[]
    skills: string[]
    mcpServers: string[]
  }
}

export interface MemoryItem {
  id: string
  scope: string
  content: string
  confidence: number
  updatedAt: string
}
