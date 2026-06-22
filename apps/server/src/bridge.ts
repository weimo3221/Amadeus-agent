import type { ClientRuntimeEvent, RuntimeEvent, ServerRuntimeEvent } from '@amadeus-agent/amadeus/events'
import type {
  MemoryReviewCandidate,
  MemoryReviewCandidatesPayload,
  MemoryReviewJob,
  MemoryReviewJobsPayload,
  MemoryReviewUpdatedPayload,
} from '@amadeus-agent/amadeus/events'

import { randomUUID } from 'node:crypto'

export interface SocketLike {
  send(data: string): void
}

export interface PythonBridgeOptions {
  runtimeUrl: string
  fetchImpl?: typeof fetch
}

interface MemoryReviewCandidatesResponse {
  ok?: boolean
  candidates?: unknown
  candidateCount?: number
}

interface MemoryReviewActionResponse {
  ok?: boolean
  result?: unknown
  error?: string
}

interface MemoryReviewJobsResponse {
  ok?: boolean
  jobs?: unknown
}

interface RuntimeFeedbackResponse {
  ok?: boolean
  events?: unknown
}

function runtimeEndpoint(runtimeUrl: string, path: string): string {
  return `${runtimeUrl.replace(/\/$/, '')}${path}`
}

function makeEvent<TType extends ServerRuntimeEvent['type'], TPayload>(
  type: TType,
  sessionId: string,
  payload: TPayload,
): RuntimeEvent<TType, TPayload> {
  return {
    id: randomUUID(),
    type,
    sessionId,
    timestamp: new Date().toISOString(),
    payload,
  }
}

function sendError(socket: SocketLike, sessionId: string, code: string, message: string): void {
  socket.send(JSON.stringify(makeEvent('error', sessionId, { code, message })))
}

export async function relayPythonTurn(
  socket: SocketLike,
  sessionId: string,
  userText: string,
  options: PythonBridgeOptions,
): Promise<boolean> {
  const fetchImpl = options.fetchImpl ?? fetch
  let response: Response
  try {
    response = await fetchImpl(runtimeEndpoint(options.runtimeUrl, '/agent/turn'), {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        sessionId,
        text: userText,
        inputMode: 'text',
      }),
    })
  }
  catch {
    return false
  }

  if (!response.ok || !response.body) {
    return false
  }

  const decoder = new TextDecoder()
  let buffer = ''

  for await (const chunk of response.body) {
    buffer += decoder.decode(chunk, { stream: true })
    const lines = buffer.split('\n')
    buffer = lines.pop() || ''

    for (const line of lines) {
      const trimmed = line.trim()
      if (!trimmed) {
        continue
      }

      try {
        const event = JSON.parse(trimmed) as RuntimeEvent<string, unknown>
        socket.send(JSON.stringify(event))
      }
      catch {
        sendError(socket, sessionId, 'bad_python_event', 'Python runtime emitted an invalid event.')
      }
    }
  }

  const tail = buffer.trim()
  if (tail) {
    try {
      const event = JSON.parse(tail) as RuntimeEvent<string, unknown>
      socket.send(JSON.stringify(event))
    }
    catch {
      sendError(socket, sessionId, 'bad_python_event', 'Python runtime emitted an invalid trailing event.')
    }
  }

  return true
}

export async function forwardToolPermissionToPython(
  requestId: string,
  approved: boolean,
  options: PythonBridgeOptions,
): Promise<void> {
  const fetchImpl = options.fetchImpl ?? fetch
  try {
    await fetchImpl(runtimeEndpoint(options.runtimeUrl, '/tools/permission'), {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({ requestId, approved }),
    })
  }
  catch {
    // The legacy TypeScript tool loop may own this request, or the Python turn
    // may have already timed out. Either way, there is nothing else to do here.
  }
}

export async function forwardRuntimeFeedbackToPython(
  event: Extract<ClientRuntimeEvent, {
    type:
      | 'desktop.capabilities'
      | 'audio.playback-started'
      | 'audio.playback-ended'
      | 'audio.playback-error'
  }>,
  options: PythonBridgeOptions,
): Promise<Array<RuntimeEvent<string, unknown>>> {
  const fetchImpl = options.fetchImpl ?? fetch
  try {
    const response = await fetchImpl(runtimeEndpoint(options.runtimeUrl, '/runtime/feedback'), {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        sessionId: event.sessionId,
        type: event.type,
        timestamp: event.timestamp,
        payload: event.payload,
      }),
    })
    const payload = await response.json().catch(() => undefined) as RuntimeFeedbackResponse | undefined
    if (!response.ok || !payload?.ok || !Array.isArray(payload.events)) {
      return []
    }
    return payload.events.filter(isRuntimeEvent)
  }
  catch {
    // Feedback is diagnostic/policy input. Dropping it must not interrupt chat.
    return []
  }
}

function isRuntimeEvent(value: unknown): value is RuntimeEvent<string, unknown> {
  if (!value || typeof value !== 'object') {
    return false
  }

  const event = value as Partial<RuntimeEvent<string, unknown>>
  return (
    typeof event.id === 'string'
    && typeof event.type === 'string'
    && typeof event.sessionId === 'string'
    && typeof event.timestamp === 'string'
    && typeof event.payload === 'object'
    && event.payload !== null
  )
}

function isMemoryReviewCandidate(value: unknown): value is MemoryReviewCandidate {
  if (!value || typeof value !== 'object') {
    return false
  }

  const candidate = value as Partial<MemoryReviewCandidate>
    const hasValidRetentionType = candidate.retentionType === undefined
      || candidate.retentionType === 'long_term'
      || candidate.retentionType === 'stable_preference'
      || candidate.retentionType === 'durable_project_fact'
      || candidate.retentionType === 'agent_instruction'
  return (
    typeof candidate.candidateId === 'number'
    && typeof candidate.sessionId === 'string'
    && (candidate.scope === 'user' || candidate.scope === 'agent' || candidate.scope === 'project')
    && typeof candidate.content === 'string'
    && typeof candidate.confidence === 'number'
      && (candidate.scopeReason === undefined || candidate.scopeReason === null || typeof candidate.scopeReason === 'string')
      && (candidate.safetyLabels === undefined || (
        Array.isArray(candidate.safetyLabels)
        && candidate.safetyLabels.every((label) => typeof label === 'string')
      ))
      && hasValidRetentionType
    && (
      candidate.status === 'pending'
      || candidate.status === 'accepted'
      || candidate.status === 'rejected'
      || candidate.status === 'superseded'
    )
  )
}

function isMemoryReviewJob(value: unknown): value is MemoryReviewJob {
  if (!value || typeof value !== 'object') {
    return false
  }

  const job = value as Partial<MemoryReviewJob>
  return (
    typeof job.jobId === 'number'
    && typeof job.sessionId === 'string'
    && (job.trigger === 'manual' || job.trigger === 'auto' || job.trigger === 'compaction')
    && (job.status === 'running' || job.status === 'completed' || job.status === 'skipped' || job.status === 'failed')
    && typeof job.sourceMessageCount === 'number'
    && typeof job.proposedCandidateCount === 'number'
    && typeof job.savedCandidateCount === 'number'
    && typeof job.suppressedCandidateCount === 'number'
    && typeof job.startedAt === 'string'
  )
}

export async function listPythonMemoryReviewCandidates(
  sessionId: string,
  status: MemoryReviewCandidatesPayload['status'] = 'pending',
  options: PythonBridgeOptions,
): Promise<MemoryReviewCandidatesPayload> {
  const fetchImpl = options.fetchImpl ?? fetch
  const params = new URLSearchParams({ sessionId, limit: '20' })
  if (status !== 'all') {
    params.set('status', status)
  }

  try {
    const response = await fetchImpl(runtimeEndpoint(options.runtimeUrl, `/memory/review/candidates?${params.toString()}`), {
      method: 'GET',
    })
    const payload = await response.json().catch(() => undefined) as MemoryReviewCandidatesResponse | undefined
    if (!response.ok || !payload?.ok || !Array.isArray(payload.candidates)) {
      return { status, candidateCount: 0, candidates: [] }
    }

    const candidates = payload.candidates.filter(isMemoryReviewCandidate)
    return {
      status,
      candidateCount: candidates.length,
      candidates,
    }
  }
  catch {
    return { status, candidateCount: 0, candidates: [] }
  }
}

export async function listPythonMemoryReviewJobs(
  sessionId: string,
  status: MemoryReviewJobsPayload['status'] = 'all',
  options: PythonBridgeOptions,
): Promise<MemoryReviewJobsPayload> {
  const fetchImpl = options.fetchImpl ?? fetch
  const params = new URLSearchParams({ sessionId, limit: '10' })
  if (status !== 'all') {
    params.set('status', status)
  }

  try {
    const response = await fetchImpl(runtimeEndpoint(options.runtimeUrl, `/memory/review/jobs?${params.toString()}`), {
      method: 'GET',
    })
    const payload = await response.json().catch(() => undefined) as MemoryReviewJobsResponse | undefined
    if (!response.ok || !payload?.ok || !Array.isArray(payload.jobs)) {
      return { status, jobCount: 0, jobs: [] }
    }

    const jobs = payload.jobs.filter(isMemoryReviewJob)
    return {
      status,
      jobCount: jobs.length,
      jobs,
    }
  }
  catch {
    return { status, jobCount: 0, jobs: [] }
  }
}

export async function runPythonMemoryReview(
  sessionId: string,
  force: boolean,
  options: PythonBridgeOptions,
): Promise<MemoryReviewUpdatedPayload> {
  return postPythonMemoryReviewAction('/memory/review/run', { sessionId, force }, options)
}

export async function acceptPythonMemoryReviewCandidate(
  candidateId: number,
  options: PythonBridgeOptions,
): Promise<MemoryReviewUpdatedPayload> {
  return postPythonMemoryReviewAction('/memory/review/accept', { candidateId }, options)
}

export async function rejectPythonMemoryReviewCandidate(
  candidateId: number,
  options: PythonBridgeOptions,
): Promise<MemoryReviewUpdatedPayload> {
  return postPythonMemoryReviewAction('/memory/review/reject', { candidateId }, options)
}

async function postPythonMemoryReviewAction(
  path: string,
  body: Record<string, unknown>,
  options: PythonBridgeOptions,
): Promise<MemoryReviewUpdatedPayload> {
  const fetchImpl = options.fetchImpl ?? fetch
  try {
    const response = await fetchImpl(runtimeEndpoint(options.runtimeUrl, path), {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(body),
    })
    const payload = await response.json().catch(() => undefined) as MemoryReviewActionResponse | undefined
    if (!response.ok || !payload?.ok) {
      return { error: payload?.error || response.statusText || 'Memory review request failed' }
    }
    if (payload.result && typeof payload.result === 'object') {
      return payload.result as MemoryReviewUpdatedPayload
    }
    const { ok: _ok, ...rest } = payload
    return rest as MemoryReviewUpdatedPayload
  }
  catch (error) {
    return {
      error: error instanceof Error ? error.message : 'Memory review request failed',
    }
  }
}
