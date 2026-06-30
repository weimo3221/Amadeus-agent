import type { ClientRuntimeEvent, RuntimeEvent, ServerRuntimeEvent } from '@amadeus-agent/amadeus/events'
import type {
  MemoryReviewCandidate,
  MemoryReviewCandidatesPayload,
  MemoryReviewJob,
  MemoryReviewJobsPayload,
  MemoryReviewUpdatedPayload,
} from '@amadeus-agent/amadeus/events'

import { randomUUID } from 'node:crypto'
import type { IncomingMessage, ServerResponse } from 'node:http'

export interface SocketLike {
  send(data: string): void
}

export interface PythonBridgeOptions {
  runtimeUrl: string
  fetchImpl?: typeof fetch
}

export interface PythonLive2DProxyOptions extends PythonBridgeOptions {
  publicBaseUrl: string
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

interface MemoryCountResponse {
  ok?: boolean
  memoryMessages?: unknown
}

interface MemoryReviewJobsResponse {
  ok?: boolean
  jobs?: unknown
}

interface RuntimeFeedbackResponse {
  ok?: boolean
  events?: unknown
}

interface Live2DProxyModelLike {
  path?: unknown
  url?: unknown
}

interface SkillsListResponse {
  ok?: boolean
  skills?: unknown
}

function runtimeEndpoint(runtimeUrl: string, path: string): string {
  return `${runtimeUrl.replace(/\/$/, '')}${path}`
}

function bridgeLive2DUrl(publicBaseUrl: string, relativePath: string): string {
  return `${publicBaseUrl.replace(/\/$/, '')}/live2d/models/${encodeURI(relativePath)}`
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value)
}

async function readIncomingJson(request: IncomingMessage): Promise<unknown> {
  let body = ''
  for await (const chunk of request) {
    body += String(chunk)
  }
  if (!body.trim()) {
    return undefined
  }
  return JSON.parse(body) as unknown
}

function writeJson(response: ServerResponse, status: number, payload: Record<string, unknown>): void {
  response.writeHead(status, {
    'Content-Type': 'application/json; charset=utf-8',
    'Access-Control-Allow-Origin': '*',
    'Access-Control-Allow-Methods': 'GET,POST,PUT,OPTIONS',
    'Access-Control-Allow-Headers': 'Content-Type',
  })
  response.end(JSON.stringify(payload))
}

function rewriteLive2DModelUrl(value: unknown, publicBaseUrl: string): void {
  if (!isRecord(value)) {
    return
  }

  const model = value as Live2DProxyModelLike
  if (typeof model.path === 'string' && model.path) {
    value.url = bridgeLive2DUrl(publicBaseUrl, model.path)
  }
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
  activeSkills: string[] | undefined,
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
        ...(activeSkills && activeSkills.length ? { skills: activeSkills } : {}),
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

export async function fetchPythonMemoryCount(
  sessionId: string,
  options: PythonBridgeOptions,
): Promise<number> {
  const fetchImpl = options.fetchImpl ?? fetch
  const params = new URLSearchParams({ sessionId })
  try {
    const response = await fetchImpl(runtimeEndpoint(options.runtimeUrl, `/memory/count?${params.toString()}`), {
      method: 'GET',
    })
    const payload = await response.json().catch(() => undefined) as MemoryCountResponse | undefined
    if (!response.ok || !payload?.ok || typeof payload.memoryMessages !== 'number') {
      return 0
    }
    return payload.memoryMessages
  }
  catch {
    return 0
  }
}

export async function resetPythonMemory(
  sessionId: string,
  options: PythonBridgeOptions,
): Promise<{ ok: true; memoryMessages: number } | { ok: false; error: string }> {
  const fetchImpl = options.fetchImpl ?? fetch
  try {
    const response = await fetchImpl(runtimeEndpoint(options.runtimeUrl, '/memory/reset'), {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({ sessionId }),
    })
    const payload = await response.json().catch(() => undefined) as MemoryCountResponse & { error?: string } | undefined
    if (!response.ok || !payload?.ok || typeof payload.memoryMessages !== 'number') {
      return {
        ok: false,
        error: payload?.error || response.statusText || 'Memory reset request failed',
      }
    }
    return { ok: true, memoryMessages: payload.memoryMessages }
  }
  catch (error) {
    return {
      ok: false,
      error: error instanceof Error ? error.message : 'Memory reset request failed',
    }
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
        clientId: event.clientId,
        surface: event.surface,
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

export async function proxyPythonLive2DRequest(
  request: IncomingMessage,
  response: ServerResponse,
  requestUrl: string,
  options: PythonLive2DProxyOptions,
): Promise<void> {
  const fetchImpl = options.fetchImpl ?? fetch

  if (request.method === 'GET' && requestUrl === '/live2d/config') {
    try {
      const runtimeResponse = await fetchImpl(runtimeEndpoint(options.runtimeUrl, '/live2d/config'), {
        method: 'GET',
      })
      const payload = await runtimeResponse.json().catch(() => undefined) as Record<string, unknown> | undefined
      if (!payload || !isRecord(payload)) {
        writeJson(response, 502, { ok: false, error: 'live2d_proxy_invalid_response' })
        return
      }
      rewriteLive2DModelUrl(payload.model, options.publicBaseUrl)
      writeJson(response, runtimeResponse.status, payload)
      return
    }
    catch {
      writeJson(response, 502, { ok: false, error: 'live2d_proxy_unavailable' })
      return
    }
  }

  if (request.method === 'GET' && requestUrl === '/live2d/models') {
    try {
      const runtimeResponse = await fetchImpl(runtimeEndpoint(options.runtimeUrl, '/live2d/models'), {
        method: 'GET',
      })
      const payload = await runtimeResponse.json().catch(() => undefined) as Record<string, unknown> | undefined
      if (!payload || !isRecord(payload)) {
        writeJson(response, 502, { ok: false, error: 'live2d_proxy_invalid_response' })
        return
      }
      if (Array.isArray(payload.models)) {
        for (const model of payload.models) {
          rewriteLive2DModelUrl(model, options.publicBaseUrl)
        }
      }
      rewriteLive2DModelUrl(payload.activeModel, options.publicBaseUrl)
      writeJson(response, runtimeResponse.status, payload)
      return
    }
    catch {
      writeJson(response, 502, { ok: false, error: 'live2d_proxy_unavailable' })
      return
    }
  }

  if (request.method === 'POST' && requestUrl === '/live2d/select') {
    let body: unknown
    try {
      body = await readIncomingJson(request)
    }
    catch {
      writeJson(response, 400, { ok: false, error: 'invalid_json' })
      return
    }

    try {
      const runtimeResponse = await fetchImpl(runtimeEndpoint(options.runtimeUrl, '/live2d/select'), {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify(body ?? {}),
      })
      const payload = await runtimeResponse.json().catch(() => undefined) as Record<string, unknown> | undefined
      if (!payload || !isRecord(payload)) {
        writeJson(response, 502, { ok: false, error: 'live2d_proxy_invalid_response' })
        return
      }
      rewriteLive2DModelUrl(payload.model, options.publicBaseUrl)
      writeJson(response, runtimeResponse.status, payload)
      return
    }
    catch {
      writeJson(response, 502, { ok: false, error: 'live2d_proxy_unavailable' })
      return
    }
  }

  if (request.method === 'GET' && requestUrl.startsWith('/live2d/models/')) {
    try {
      const runtimeResponse = await fetchImpl(
        runtimeEndpoint(options.runtimeUrl, requestUrl),
        { method: 'GET' },
      )
      const body = Buffer.from(await runtimeResponse.arrayBuffer())
      response.writeHead(runtimeResponse.status, {
        'Content-Type': runtimeResponse.headers.get('content-type') || 'application/octet-stream',
        'Content-Length': String(body.length),
        'Access-Control-Allow-Origin': '*',
      })
      response.end(body)
      return
    }
    catch {
      writeJson(response, 502, { ok: false, error: 'live2d_proxy_unavailable' })
      return
    }
  }

  writeJson(response, 404, { ok: false, error: 'not_found' })
}

export async function proxyPythonSkillsRequest(
  request: IncomingMessage,
  response: ServerResponse,
  requestUrl: string,
  options: PythonBridgeOptions,
): Promise<void> {
  const fetchImpl = options.fetchImpl ?? fetch

  if (request.method === 'GET' && requestUrl === '/skills/list') {
    try {
      const runtimeResponse = await fetchImpl(runtimeEndpoint(options.runtimeUrl, '/skills/list'), {
        method: 'GET',
      })
      const payload = await runtimeResponse.json().catch(() => undefined) as SkillsListResponse | undefined
      if (!payload || !isRecord(payload) || !Array.isArray(payload.skills)) {
        writeJson(response, 502, { ok: false, error: 'skills_proxy_invalid_response' })
        return
      }
      writeJson(response, runtimeResponse.status, payload)
      return
    }
    catch {
      writeJson(response, 502, { ok: false, error: 'skills_proxy_unavailable' })
      return
    }
  }

  if (request.method === 'GET' && requestUrl.startsWith('/skills/view')) {
    try {
      const runtimeResponse = await fetchImpl(runtimeEndpoint(options.runtimeUrl, requestUrl), {
        method: 'GET',
      })
      const payload = await runtimeResponse.json().catch(() => undefined) as Record<string, unknown> | undefined
      if (!payload || !isRecord(payload)) {
        writeJson(response, 502, { ok: false, error: 'skills_proxy_invalid_response' })
        return
      }
      writeJson(response, runtimeResponse.status, payload)
      return
    }
    catch {
      writeJson(response, 502, { ok: false, error: 'skills_proxy_unavailable' })
      return
    }
  }

  writeJson(response, 404, { ok: false, error: 'not_found' })
}

export async function proxyPythonSessionRequest(
  request: IncomingMessage,
  response: ServerResponse,
  requestUrl: string,
  options: PythonBridgeOptions,
): Promise<void> {
  const fetchImpl = options.fetchImpl ?? fetch

  if (!/^\/sessions\/[^/]+\/plan$/.test(requestUrl)) {
    writeJson(response, 404, { ok: false, error: 'not_found' })
    return
  }

  if (request.method !== 'GET' && request.method !== 'PUT') {
    writeJson(response, 405, { ok: false, error: 'method_not_allowed' })
    return
  }

  try {
    const body = request.method === 'PUT' ? await readIncomingJson(request) : undefined
    const runtimeResponse = await fetchImpl(runtimeEndpoint(options.runtimeUrl, requestUrl), {
      method: request.method,
      headers: request.method === 'PUT' ? { 'Content-Type': 'application/json' } : undefined,
      body: request.method === 'PUT' ? JSON.stringify(body ?? {}) : undefined,
    })
    const payload = await runtimeResponse.json().catch(() => undefined) as Record<string, unknown> | undefined
    if (!payload || !isRecord(payload)) {
      writeJson(response, 502, { ok: false, error: 'session_proxy_invalid_response' })
      return
    }
    writeJson(response, runtimeResponse.status, payload)
    return
  }
  catch {
    writeJson(response, 502, { ok: false, error: 'session_proxy_unavailable' })
  }
}

export async function proxyPythonTaskRequest(
  request: IncomingMessage,
  response: ServerResponse,
  requestUrl: string,
  options: PythonBridgeOptions,
): Promise<void> {
  const fetchImpl = options.fetchImpl ?? fetch
  const isSupportedPath = requestUrl === '/tasks'
    || /^\/tasks\/[^/]+\/cancel$/.test(requestUrl)
    || /^\/tasks\/[^/]+\/events(?:\?.*)?$/.test(requestUrl)
    || requestUrl.startsWith('/tasks?')

  if (!isSupportedPath) {
    writeJson(response, 404, { ok: false, error: 'not_found' })
    return
  }

  if (request.method !== 'GET' && request.method !== 'POST') {
    writeJson(response, 405, { ok: false, error: 'method_not_allowed' })
    return
  }

  try {
    const body = request.method === 'POST' ? await readIncomingJson(request) : undefined
    const runtimeResponse = await fetchImpl(runtimeEndpoint(options.runtimeUrl, requestUrl), {
      method: request.method,
      headers: request.method === 'POST' ? { 'Content-Type': 'application/json' } : undefined,
      body: request.method === 'POST' ? JSON.stringify(body ?? {}) : undefined,
    })
    const payload = await runtimeResponse.json().catch(() => undefined) as Record<string, unknown> | undefined
    if (!payload || !isRecord(payload)) {
      writeJson(response, 502, { ok: false, error: 'task_proxy_invalid_response' })
      return
    }
    writeJson(response, runtimeResponse.status, payload)
  }
  catch {
    writeJson(response, 502, { ok: false, error: 'task_proxy_unavailable' })
  }
}

function isRuntimeEvent(value: unknown): value is RuntimeEvent<string, unknown> {
  const event = isRecord(value) ? value as Partial<RuntimeEvent<string, unknown>> : undefined
  if (!event) {
    return false
  }

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
  if (!isRecord(value)) {
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
  if (!isRecord(value)) {
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
