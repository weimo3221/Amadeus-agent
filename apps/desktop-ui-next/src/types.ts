export type ConnectionState = 'online' | 'connecting' | 'offline'

export type ChatRole = 'user' | 'assistant' | 'system'

export interface ChatMessage {
  id: string
  role: ChatRole
  content: string
  createdAt: string
  pending?: boolean
  toolName?: string
}

export interface SessionItem {
  id: string
  title: string
  roleName: string
  messageCount: number
  updatedAt: string
  active?: boolean
}

export type PlanStatus = 'done' | 'active' | 'pending'

export interface PlanItem {
  id: string
  label: string
  status: PlanStatus
}

export type TaskStatus = 'queued' | 'running' | 'blocked' | 'done' | 'failed'

export interface TaskItem {
  id: string
  title: string
  detail: string
  status: TaskStatus
  updatedAt: string
  attempts: number
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
  score: number
}

export interface ScheduledJob {
  id: string
  title: string
  schedule: string
  nextRun: string
  repeat: number
  enabled: boolean
}
