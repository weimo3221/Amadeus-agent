export type AssistantState =
  | 'idle'
  | 'listening'
  | 'thinking'
  | 'speaking'
  | 'tool-running'
  | 'error'

export interface RuntimeEvent<TType extends string = string, TPayload = unknown> {
  id: string
  type: TType
  sessionId: string
  timestamp: string
  payload: TPayload
}

export interface ErrorPayload {
  code: string
  message: string
}

export interface HelloPayload {
  name: string
  model: string
  memoryMessages: number
  toolPermissions: ToolPermissionState[]
}

export interface ToolPermissionState {
  name: string
  displayName: string
  enabled: boolean
  permission: 'allow' | 'ask' | 'deny'
}

export type ClientRuntimeEvent =
  | RuntimeEvent<'user.message', UserMessagePayload>
  | RuntimeEvent<'session.reset', Record<string, never>>
  | RuntimeEvent<'tool.permission.response', ToolPermissionResponsePayload>
  | RuntimeEvent<'memory.review.list', MemoryReviewListRequestPayload>
  | RuntimeEvent<'memory.review.run', MemoryReviewRunRequestPayload>
  | RuntimeEvent<'memory.review.accept', MemoryReviewCandidateActionPayload>
  | RuntimeEvent<'memory.review.reject', MemoryReviewCandidateActionPayload>

export type ServerRuntimeEvent =
  | RuntimeEvent<'server.hello', HelloPayload>
  | RuntimeEvent<'memory.updated', MemoryUpdatedPayload>
  | RuntimeEvent<'memory.context.used', MemoryContextUsedPayload>
  | RuntimeEvent<'assistant.delta', AssistantDeltaPayload>
  | RuntimeEvent<'assistant.message', AssistantMessagePayload>
  | RuntimeEvent<'assistant.state', AssistantStatePayload>
  | RuntimeEvent<'character.behavior', CharacterBehaviorPayload>
  | RuntimeEvent<'audio.tts-ready', AudioTtsReadyPayload>
  | RuntimeEvent<'tool.started', ToolStartedPayload>
  | RuntimeEvent<'tool.finished', ToolFinishedPayload>
  | RuntimeEvent<'tool.audit', ToolAuditPayload>
  | RuntimeEvent<'tool.permission.request', ToolPermissionRequestPayload>
  | RuntimeEvent<'memory.review.candidates', MemoryReviewCandidatesPayload>
  | RuntimeEvent<'memory.review.jobs', MemoryReviewJobsPayload>
  | RuntimeEvent<'memory.review.updated', MemoryReviewUpdatedPayload>
  | RuntimeEvent<'error', ErrorPayload>

export interface UserMessagePayload {
  text: string
  inputMode: 'text' | 'voice'
}

export interface AssistantDeltaPayload {
  text: string
}

export interface AssistantMessagePayload {
  text: string
}

export interface AssistantStatePayload {
  state: AssistantState
}

export interface CharacterBehaviorPayload {
  emotion: string
  expression: string
  motion: string
  intensity?: number
}

export interface AudioTtsReadyPayload {
  audioUrl: string
  durationMs?: number | null
}

export interface ToolStartedPayload {
  toolName: string
  displayName: string
}

export interface ToolFinishedPayload {
  toolName: string
  ok: boolean
  durationMs?: number | null
  failureCode?: string | null
  resultPreview?: string | null
  outputTruncated?: boolean | null
}

export interface ToolAuditPayload {
  recordId: string
  timestamp: string
  sessionId: string
  toolName: string
  decision: 'started' | 'finished' | 'denied' | 'blocked' | 'failed'
  ok?: boolean | null
  durationMs?: number | null
  failureCode?: string | null
  detail?: string | null
}

export interface ToolPermissionRequestPayload {
  requestId: string
  toolName: string
  displayName: string
  reason: string
}

export interface ToolPermissionResponsePayload {
  requestId: string
  approved: boolean
}

export interface MemoryUpdatedPayload {
  memoryMessages: number
}

export interface MemoryContextSource {
  kind: string
  sourceId: string
  contentChars: number
  reason: string
  metadata?: Record<string, unknown>
}

export interface MemoryContextUsedPayload {
  sourceCounts: Record<string, number>
  sourceCount: number
  coveredThroughMessageId: number
  sources: MemoryContextSource[]
}

export interface MemoryReviewCandidate {
  candidateId: number
  sessionId: string
  scope: 'user' | 'agent' | 'project'
  content: string
  confidence: number
  reason?: string | null
  scopeReason?: string | null
  safetyLabels?: string[]
  retentionType?: 'long_term' | 'stable_preference' | 'durable_project_fact' | 'agent_instruction'
  sourceMessageStartId?: number | null
  sourceMessageEndId?: number | null
  status: 'pending' | 'accepted' | 'rejected' | 'superseded'
  memoryItemId?: number | null
  createdAt?: string
  updatedAt?: string
  duplicate?: boolean
  suppressed?: boolean
}

export interface MemoryReviewListRequestPayload {
  status?: MemoryReviewCandidate['status']
}

export interface MemoryReviewRunRequestPayload {
  force?: boolean
}

export interface MemoryReviewCandidateActionPayload {
  candidateId: number
}

export interface MemoryReviewCandidatesPayload {
  status: MemoryReviewCandidate['status'] | 'all'
  candidateCount: number
  candidates: MemoryReviewCandidate[]
}

export interface MemoryReviewJob {
  jobId: number
  sessionId: string
  trigger: 'manual' | 'auto' | 'compaction'
  status: 'running' | 'completed' | 'skipped' | 'failed'
  reason?: string | null
  error?: string | null
  sourceMessageStartId?: number | null
  sourceMessageEndId?: number | null
  sourceMessageCount: number
  proposedCandidateCount: number
  savedCandidateCount: number
  suppressedCandidateCount: number
  startedAt: string
  finishedAt?: string | null
  durationMs?: number | null
}

export interface MemoryReviewJobsPayload {
  status: MemoryReviewJob['status'] | 'all'
  jobCount: number
  jobs: MemoryReviewJob[]
}

export interface MemoryReviewUpdatedPayload {
  reviewed?: boolean
  sessionId?: string
  sourceMessageCount?: number
  proposedCandidateCount?: number
  candidateCount?: number
  suppressedCandidateCount?: number
  candidates?: MemoryReviewCandidate[]
  accepted?: boolean
  rejected?: boolean
  candidate?: MemoryReviewCandidate
  item?: unknown
  error?: string
  reason?: string
  jobId?: number
  job?: MemoryReviewJob
}
