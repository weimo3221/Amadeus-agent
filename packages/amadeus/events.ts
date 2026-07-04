export type AssistantState =
  | 'idle'
  | 'listening'
  | 'thinking'
  | 'speaking'
  | 'tool-running'
  | 'error'

export type ClientSurface =
  | 'main-ui'
  | 'companion'
  | 'cli'

export interface RuntimeEvent<TType extends string = string, TPayload = unknown> {
  id: string
  type: TType
  sessionId: string
  clientId?: string
  surface?: ClientSurface
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
  | RuntimeEvent<'desktop.capabilities', DesktopCapabilitiesPayload>
  | RuntimeEvent<'audio.playback-started', AudioPlaybackStartedPayload>
  | RuntimeEvent<'audio.playback-ended', AudioPlaybackEndedPayload>
  | RuntimeEvent<'audio.playback-error', AudioPlaybackErrorPayload>
  | RuntimeEvent<'memory.review.list', MemoryReviewListRequestPayload>
  | RuntimeEvent<'memory.review.run', MemoryReviewRunRequestPayload>
  | RuntimeEvent<'memory.review.accept', MemoryReviewCandidateActionPayload>
  | RuntimeEvent<'memory.review.reject', MemoryReviewCandidateActionPayload>

export type ServerRuntimeEvent =
  | RuntimeEvent<'server.hello', HelloPayload>
  | RuntimeEvent<'memory.updated', MemoryUpdatedPayload>
  | RuntimeEvent<'memory.context.used', MemoryContextUsedPayload>
  | RuntimeEvent<'agent.turn.started', AgentTurnStartedPayload>
  | RuntimeEvent<'agent.turn.cancelled', AgentTurnCancelledPayload>
  | RuntimeEvent<'assistant.reasoning.delta', AssistantReasoningDeltaPayload>
  | RuntimeEvent<'assistant.delta', AssistantDeltaPayload>
  | RuntimeEvent<'assistant.message', AssistantMessagePayload>
  | RuntimeEvent<'assistant.state', AssistantStatePayload>
  | RuntimeEvent<'character.behavior', CharacterBehaviorPayload>
  | RuntimeEvent<'audio.lipsync-cues', AudioLipsyncCuesPayload>
  | RuntimeEvent<'audio.tts-ready', AudioTtsReadyPayload>
  | RuntimeEvent<'skill.started', SkillStartedPayload>
  | RuntimeEvent<'skill.finished', SkillFinishedPayload>
  | RuntimeEvent<'tool.started', ToolStartedPayload>
  | RuntimeEvent<'tool.finished', ToolFinishedPayload>
  | RuntimeEvent<'tool.audit', ToolAuditPayload>
  | RuntimeEvent<'tool.permission.request', ToolPermissionRequestPayload>
  | RuntimeEvent<'task.plan.updated', TaskPlanPayload>
  | RuntimeEvent<'task.updated', TaskUpdatedPayload>
  | RuntimeEvent<'scheduled.updated', ScheduledJobUpdatedPayload>
  | RuntimeEvent<'memory.review.candidates', MemoryReviewCandidatesPayload>
  | RuntimeEvent<'memory.review.jobs', MemoryReviewJobsPayload>
  | RuntimeEvent<'memory.review.updated', MemoryReviewUpdatedPayload>
  | RuntimeEvent<'error', ErrorPayload>

export interface UserMessagePayload {
  text: string
  inputMode: 'text' | 'voice'
  skills?: string[]
}

export interface AgentTurnStartedPayload {
  sessionId: string
  turnId: string
  startedAt: string
}

export interface AgentTurnCancelledPayload {
  sessionId: string
  turnId: string
  phase: string
}

export interface AssistantDeltaPayload {
  text: string
}

export interface AssistantReasoningDeltaPayload {
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

export interface DesktopCapabilitiesPayload {
  desktop: {
    runtime: 'electron'
    protocolVersion: number
  }
  live2d: {
    available: boolean
    modelId?: string
    expressions: string[]
    motions: string[]
  }
  audio: {
    runtimeAudio: boolean
    speechSynthesis: boolean
    voiceCount: number
  }
}

export interface AudioTtsReadyPayload {
  audioUrl: string
  durationMs?: number | null
}

export interface AudioLipsyncCue {
  offsetMs: number
  mouthOpen: number
  viseme?: string
  phoneme?: string
}

export interface AudioLipsyncCuesPayload {
  source: 'runtime_audio' | 'speech_synthesis'
  audioUrl?: string
  durationMs?: number | null
  cues: AudioLipsyncCue[]
}

export interface AudioPlaybackStartedPayload {
  source: 'runtime_audio' | 'speech_synthesis'
  audioUrl?: string
  durationMs?: number | null
  runtimeCuesActive?: boolean
}

export interface AudioPlaybackEndedPayload {
  source: 'runtime_audio' | 'speech_synthesis'
  audioUrl?: string
}

export interface AudioPlaybackErrorPayload {
  source: 'runtime_audio' | 'speech_synthesis'
  audioUrl?: string
  reason: string
}

export interface SkillStartedPayload {
  skillName: string
  displayName: string
  source: 'skill_view'
}

export interface SkillFinishedPayload {
  skillName: string
  displayName: string
  ok: boolean
  source: 'skill_view'
  identifier?: string | null
  failureCode?: string | null
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
  metadata?: Record<string, unknown> | null
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
  sessionId: string
  turnId: string
  phase: 'turn_start' | 'tool_decision_retry' | 'final_response_retry' | string
  timestamp: string
  sources: MemoryContextSource[]
}

export type TaskPlanItemStatus = 'pending' | 'in_progress' | 'completed' | 'cancelled'

export interface TaskPlanItem {
  id: string
  content: string
  status: TaskPlanItemStatus
}

export interface TaskPlanSummary {
  total: number
  pending: number
  inProgress: number
  completed: number
  cancelled: number
}

export interface TaskPlanPayload {
  sessionId: string
  items: TaskPlanItem[]
  summary: TaskPlanSummary
  updatedAt?: string | null
  changed?: boolean
  merge?: boolean
}

export type TaskStatus = 'queued' | 'running' | 'blocked' | 'succeeded' | 'failed' | 'cancelled'

export interface TaskRecord {
  id: string
  sessionId: string
  title: string
  body: string
  status: TaskStatus
  priority: number
  dueAt?: string | null
  attemptCount?: number
  maxAttempts?: number
  nextRunAt?: string | null
  claimLock?: string | null
  lastHeartbeat?: string | null
  result?: string | null
  error?: string | null
  createdAt: string
  updatedAt: string
  finishedAt?: string | null
}

export interface TaskSummary {
  total: number
  queued: number
  running: number
  blocked: number
  succeeded: number
  failed: number
  cancelled: number
}

export interface TaskUpdatedPayload {
  task: TaskRecord
  action: 'created' | 'updated' | 'running' | 'cancelled' | 'succeeded' | 'failed' | 'blocked'
}

export type ScheduledJobStatus = 'scheduled' | 'running' | 'paused' | 'completed' | 'cancelled' | 'failed'

export interface ScheduledJobRecord {
  id: string
  sessionId: string
  title: string
  message: string
  schedule: Record<string, unknown>
  scheduleDisplay: string
  status: ScheduledJobStatus
  repeatCount?: number | null
  completedRuns: number
  nextRunAt?: string | null
  lastRunAt?: string | null
  lastError?: string | null
  createdAt: string
  updatedAt: string
  finishedAt?: string | null
}

export interface ScheduledJobSummary {
  total: number
  scheduled: number
  running: number
  paused: number
  completed: number
  cancelled: number
  failed: number
}

export interface ScheduledJobUpdatedPayload {
  job: ScheduledJobRecord
  action: 'created' | 'running' | 'fired' | 'scheduled' | 'paused' | 'resumed' | 'cancelled' | 'completed' | 'failed'
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
