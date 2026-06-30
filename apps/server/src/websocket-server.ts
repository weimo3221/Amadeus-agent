import type {
  AssistantState,
  ClientSurface,
  ClientRuntimeEvent,
  RuntimeEvent,
  ServerRuntimeEvent,
  MemoryReviewCandidatesPayload,
  MemoryReviewJobsPayload,
  MemoryReviewUpdatedPayload,
  ToolPermissionState,
} from '@amadeus-agent/amadeus/events'

import { randomUUID } from 'node:crypto'
import { createServer, type IncomingMessage, type Server as HttpServer } from 'node:http'
import { WebSocket, WebSocketServer } from 'ws'

export interface AmadeusBridgeServerOptions {
  model: string
  defaultSessionId: string
  getMemoryMessageCount(sessionId: string): number | Promise<number>
  getToolPermissions(): ToolPermissionState[] | Promise<ToolPermissionState[]>
  resetSession(sessionId: string): void | Promise<void>
  forwardToolPermissionToPython(requestId: string, approved: boolean): void | Promise<void>
  listMemoryReviewCandidates?(sessionId: string, status?: MemoryReviewCandidatesPayload['status']): MemoryReviewCandidatesPayload | Promise<MemoryReviewCandidatesPayload>
  listMemoryReviewJobs?(sessionId: string, status?: MemoryReviewJobsPayload['status']): MemoryReviewJobsPayload | Promise<MemoryReviewJobsPayload>
  runMemoryReview?(sessionId: string, force: boolean): MemoryReviewUpdatedPayload | Promise<MemoryReviewUpdatedPayload>
  acceptMemoryReviewCandidate?(candidateId: number): MemoryReviewUpdatedPayload | Promise<MemoryReviewUpdatedPayload>
  rejectMemoryReviewCandidate?(candidateId: number): MemoryReviewUpdatedPayload | Promise<MemoryReviewUpdatedPayload>
  observeDesktopFeedback?(event: Extract<ClientRuntimeEvent, {
    type:
      | 'desktop.capabilities'
      | 'audio.playback-started'
      | 'audio.playback-ended'
      | 'audio.playback-error'
  }>): Array<RuntimeEvent<string, unknown>> | Promise<Array<RuntimeEvent<string, unknown>>> | void | Promise<void>
  handleLive2DHttpRequest?(
    request: IncomingMessage,
    response: import('node:http').ServerResponse,
    requestUrl: string,
  ): void | Promise<void>
  handleSkillsHttpRequest?(
    request: IncomingMessage,
    response: import('node:http').ServerResponse,
    requestUrl: string,
  ): void | Promise<void>
  handleSessionHttpRequest?(
    request: IncomingMessage,
    response: import('node:http').ServerResponse,
    requestUrl: string,
  ): void | Promise<void>
  handleTaskHttpRequest?(
    request: IncomingMessage,
    response: import('node:http').ServerResponse,
    requestUrl: string,
  ): void | Promise<void>
  handleAgentHttpRequest?(
    request: IncomingMessage,
    response: import('node:http').ServerResponse,
    requestUrl: string,
  ): void | Promise<void>
  streamChat(socket: BridgeSocket, sessionId: string, text: string, skills?: string[]): void | Promise<void>
}

export interface AmadeusBridgeServer {
  httpServer: HttpServer
  wss: WebSocketServer
}

export interface BridgeSocket {
  send(data: string): void
}

interface ClientConnection {
  id: string
  socket: WebSocket
  sessionId: string
  surface?: ClientSurface
}

interface ConnectionParams {
  sessionId?: string
  surface?: ClientSurface
  error?: string
}

const CLIENT_SURFACES = new Set<ClientSurface>(['main-ui', 'companion', 'cli'])
const SESSION_ID_PATTERN = /^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$/

function logBridge(message: string, metadata: Record<string, unknown> = {}): void {
  console.info(`[amadeus:bridge] ${message} ${JSON.stringify(metadata)}`)
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

function send<TType extends ServerRuntimeEvent['type'], TPayload>(
  socket: BridgeSocket,
  type: TType,
  sessionId: string,
  payload: TPayload,
): void {
  socket.send(JSON.stringify(makeEvent(type, sessionId, payload)))
}

function sendState(socket: BridgeSocket, sessionId: string, state: AssistantState): void {
  send(socket, 'assistant.state', sessionId, { state })
}

async function sendHello(
  socket: BridgeSocket,
  sessionId: string,
  options: AmadeusBridgeServerOptions,
): Promise<void> {
  const memoryMessages = await Promise.resolve(options.getMemoryMessageCount(sessionId)).catch(() => 0)
  const toolPermissions = await Promise.resolve(options.getToolPermissions()).catch(() => [])

  send(socket, 'server.hello', sessionId, {
    name: 'amadeus-agent-server',
    model: options.model,
    memoryMessages,
    toolPermissions,
  })
}

async function sendMemoryReviewCandidates(
  socket: BridgeSocket,
  sessionId: string,
  options: AmadeusBridgeServerOptions,
  status: MemoryReviewCandidatesPayload['status'] = 'pending',
): Promise<void> {
  const payload = await Promise.resolve(options.listMemoryReviewCandidates?.(sessionId, status) ?? {
    status,
    candidateCount: 0,
    candidates: [],
  }).catch(() => ({
    status,
    candidateCount: 0,
    candidates: [],
  }))
  send(socket, 'memory.review.candidates', sessionId, payload)
}

async function sendMemoryReviewJobs(
  socket: BridgeSocket,
  sessionId: string,
  options: AmadeusBridgeServerOptions,
  status: MemoryReviewJobsPayload['status'] = 'all',
): Promise<void> {
  const payload = await Promise.resolve(options.listMemoryReviewJobs?.(sessionId, status) ?? {
    status,
    jobCount: 0,
    jobs: [],
  }).catch(() => ({
    status,
    jobCount: 0,
    jobs: [],
  }))
  send(socket, 'memory.review.jobs', sessionId, payload)
}

function isClientSurface(value: unknown): value is ClientSurface {
  return typeof value === 'string' && CLIENT_SURFACES.has(value as ClientSurface)
}

function isSessionId(value: unknown): value is string {
  return typeof value === 'string' && SESSION_ID_PATTERN.test(value)
}

function parseConnectionParams(request: IncomingMessage): ConnectionParams {
  const requestUrl = request.url ?? ''
  const queryStart = requestUrl.indexOf('?')
  if (queryStart < 0) {
    return {}
  }

  const params = new URLSearchParams(requestUrl.slice(queryStart + 1))
  const surface = params.get('surface')
  const sessionId = params.get('sessionId')
  if (surface !== null && !isClientSurface(surface)) {
    return { error: 'Invalid WebSocket surface. Expected one of: main-ui, companion, cli.' }
  }
  if (sessionId !== null && !isSessionId(sessionId)) {
    return { error: 'Invalid WebSocket sessionId. Use 1-128 characters: letters, numbers, ".", "_", ":", or "-"; first character must be a letter or number.' }
  }
  return {
    surface: surface ?? undefined,
    sessionId: sessionId ?? undefined,
  }
}

function parseEvent(raw: Buffer, fallbackSurface?: ClientSurface): ClientRuntimeEvent | undefined {
  try {
    const data = JSON.parse(raw.toString()) as ClientRuntimeEvent
    if (!data || typeof data.type !== 'string' || typeof data.sessionId !== 'string') {
      return undefined
    }
    if (!isSessionId(data.sessionId)) {
      return undefined
    }
    if (data.clientId !== undefined && typeof data.clientId !== 'string') {
      return undefined
    }
    if (data.surface !== undefined && !isClientSurface(data.surface)) {
      return undefined
    }
    if (data.surface === undefined && fallbackSurface) {
      return { ...data, surface: fallbackSurface }
    }
    return data
  }
  catch {
    return undefined
  }
}

function withClientMetadata<TEvent extends ClientRuntimeEvent>(
  event: TEvent,
  client: ClientConnection,
): TEvent {
  return {
    ...event,
    clientId: client.id,
    surface: event.surface ?? client.surface,
  }
}

function summarizeCapabilitiesPayload(payload: unknown): Record<string, unknown> {
  if (!payload || typeof payload !== 'object') {
    return {}
  }

  const record = payload as Record<string, unknown>
  const live2d = record.live2d && typeof record.live2d === 'object' ? record.live2d as Record<string, unknown> : {}
  const audio = record.audio && typeof record.audio === 'object' ? record.audio as Record<string, unknown> : {}
  return {
    live2dAvailable: Boolean(live2d.available),
    live2dModelId: typeof live2d.modelId === 'string' ? live2d.modelId : null,
    live2dExpressionCount: Array.isArray(live2d.expressions) ? live2d.expressions.length : 0,
    live2dMotionCount: Array.isArray(live2d.motions) ? live2d.motions.length : 0,
    runtimeAudio: Boolean(audio.runtimeAudio),
    speechSynthesis: Boolean(audio.speechSynthesis),
    voiceCount: typeof audio.voiceCount === 'number' ? audio.voiceCount : 0,
  }
}

function addClient(
  clientsBySession: Map<string, Map<string, ClientConnection>>,
  client: ClientConnection,
): void {
  const sessionClients = clientsBySession.get(client.sessionId) ?? new Map<string, ClientConnection>()
  sessionClients.set(client.id, client)
  clientsBySession.set(client.sessionId, sessionClients)
  logBridge('client registered', {
    clientId: client.id,
    sessionId: client.sessionId,
    surface: client.surface ?? null,
    sessionClientCount: sessionClients.size,
  })
}

function removeClient(
  clientsBySession: Map<string, Map<string, ClientConnection>>,
  client: ClientConnection,
): void {
  const sessionClients = clientsBySession.get(client.sessionId)
  if (!sessionClients) {
    return
  }

  sessionClients.delete(client.id)
  logBridge('client removed', {
    clientId: client.id,
    sessionId: client.sessionId,
    surface: client.surface ?? null,
    sessionClientCount: sessionClients.size,
  })
  if (!sessionClients.size) {
    clientsBySession.delete(client.sessionId)
    logBridge('session room removed', {
      sessionId: client.sessionId,
    })
  }
}

function moveClientToSession(
  clientsBySession: Map<string, Map<string, ClientConnection>>,
  client: ClientConnection,
  sessionId: string,
): void {
  if (client.sessionId === sessionId) {
    return
  }

  const previousSessionId = client.sessionId
  removeClient(clientsBySession, client)
  client.sessionId = sessionId
  addClient(clientsBySession, client)
  logBridge('client moved sessions', {
    clientId: client.id,
    surface: client.surface ?? null,
    previousSessionId,
    nextSessionId: sessionId,
  })
}

function broadcastRaw(
  clientsBySession: Map<string, Map<string, ClientConnection>>,
  sessionId: string,
  data: string,
): void {
  const sessionClients = clientsBySession.get(sessionId)
  if (!sessionClients) {
    return
  }

  for (const client of Array.from(sessionClients.values())) {
    if (client.socket.readyState !== WebSocket.OPEN) {
      removeClient(clientsBySession, client)
      continue
    }
    client.socket.send(data)
  }
}

function sessionBroadcaster(
  clientsBySession: Map<string, Map<string, ClientConnection>>,
  sessionId: string,
): BridgeSocket {
  return {
    send(data: string): void {
      broadcastRaw(clientsBySession, sessionId, data)
    },
  }
}

export function createAmadeusBridgeServer(options: AmadeusBridgeServerOptions): AmadeusBridgeServer {
  const clientsBySession = new Map<string, Map<string, ClientConnection>>()

  const httpServer = createServer((request, response) => {
    const requestUrl = request.url ?? '/'
    if (request.method === 'OPTIONS') {
      response.writeHead(204, {
        'Access-Control-Allow-Origin': '*',
        'Access-Control-Allow-Methods': 'GET,POST,PUT,OPTIONS',
        'Access-Control-Allow-Headers': 'Content-Type',
      })
      response.end()
      return
    }

    if (requestUrl === '/health') {
      response.writeHead(200, { 'Content-Type': 'application/json' })
      response.end(JSON.stringify({ ok: true, model: options.model }))
      return
    }

    if (requestUrl.startsWith('/live2d/') && options.handleLive2DHttpRequest) {
      void Promise.resolve(options.handleLive2DHttpRequest(request, response, requestUrl)).catch(() => {
        if (response.headersSent) {
          return
        }
        response.writeHead(502, { 'Content-Type': 'application/json' })
        response.end(JSON.stringify({ ok: false, error: 'live2d_proxy_unavailable' }))
      })
      return
    }

    if (requestUrl.startsWith('/skills/') && options.handleSkillsHttpRequest) {
      void Promise.resolve(options.handleSkillsHttpRequest(request, response, requestUrl)).catch(() => {
        if (response.headersSent) {
          return
        }
        response.writeHead(502, { 'Content-Type': 'application/json' })
        response.end(JSON.stringify({ ok: false, error: 'skills_proxy_unavailable' }))
      })
      return
    }

    if (requestUrl.startsWith('/sessions/') && options.handleSessionHttpRequest) {
      void Promise.resolve(options.handleSessionHttpRequest(request, response, requestUrl)).catch(() => {
        if (response.headersSent) {
          return
        }
        response.writeHead(502, { 'Content-Type': 'application/json' })
        response.end(JSON.stringify({ ok: false, error: 'session_proxy_unavailable' }))
      })
      return
    }

    if (requestUrl === '/tasks' || requestUrl.startsWith('/tasks?') || requestUrl.startsWith('/tasks/')) {
      if (options.handleTaskHttpRequest) {
        void Promise.resolve(options.handleTaskHttpRequest(request, response, requestUrl)).catch(() => {
          if (response.headersSent) {
            return
          }
          response.writeHead(502, { 'Content-Type': 'application/json' })
          response.end(JSON.stringify({ ok: false, error: 'task_proxy_unavailable' }))
        })
        return
      }
    }

    if (requestUrl.startsWith('/agent/') && options.handleAgentHttpRequest) {
      void Promise.resolve(options.handleAgentHttpRequest(request, response, requestUrl)).catch(() => {
        if (response.headersSent) {
          return
        }
        response.writeHead(502, { 'Content-Type': 'application/json' })
        response.end(JSON.stringify({ ok: false, error: 'agent_proxy_unavailable' }))
      })
      return
    }

    response.writeHead(404, { 'Content-Type': 'application/json' })
    response.end(JSON.stringify({ error: 'not_found' }))
  })

  const wss = new WebSocketServer({ server: httpServer, path: '/ws' })

  wss.on('connection', (socket, request) => {
    const connectionParams = parseConnectionParams(request)
    if (connectionParams.error) {
      const sessionId = isSessionId(options.defaultSessionId) ? options.defaultSessionId : 'default'
      logBridge('rejecting websocket connection params', {
        requestUrl: request.url ?? '',
        sessionId,
        reason: connectionParams.error,
      })
      send(socket, 'error', sessionId, {
        code: 'bad_connection_params',
        message: connectionParams.error,
      })
      socket.close(1008, 'bad_connection_params')
      return
    }

    const sessionId = connectionParams.sessionId ?? options.defaultSessionId
    const connectionSurface = connectionParams.surface
    logBridge('accepting websocket connection', {
      requestUrl: request.url ?? '',
      sessionId,
      surface: connectionSurface ?? null,
    })
    const client: ClientConnection = {
      id: randomUUID(),
      socket,
      sessionId,
      surface: connectionSurface,
    }
    addClient(clientsBySession, client)
    socket.on('close', () => {
      removeClient(clientsBySession, client)
    })

    void sendHello(socket, sessionId, options)
      .then(() => sendMemoryReviewCandidates(socket, sessionId, options, 'pending'))
      .then(() => sendMemoryReviewJobs(socket, sessionId, options, 'all'))
      .then(() => sendState(socket, sessionId, 'idle'))

    socket.on('message', (raw) => {
      const event = parseEvent(raw as Buffer, connectionSurface)
      if (!event) {
        send(socket, 'error', sessionId, {
          code: 'bad_event',
          message: 'Could not parse client event.',
        })
        return
      }
      const clientEvent = withClientMetadata(event, client)
      moveClientToSession(clientsBySession, client, clientEvent.sessionId)

      if (clientEvent.type === 'session.reset') {
        void Promise.resolve(options.resetSession(clientEvent.sessionId))
          .then(() => sendHello(sessionBroadcaster(clientsBySession, clientEvent.sessionId), clientEvent.sessionId, options))
          .then(() => sendMemoryReviewCandidates(sessionBroadcaster(clientsBySession, clientEvent.sessionId), clientEvent.sessionId, options, 'pending'))
          .then(() => sendMemoryReviewJobs(sessionBroadcaster(clientsBySession, clientEvent.sessionId), clientEvent.sessionId, options, 'all'))
          .then(() => sendState(sessionBroadcaster(clientsBySession, clientEvent.sessionId), clientEvent.sessionId, 'idle'))
          .catch((error: unknown) => {
            const broadcastSocket = sessionBroadcaster(clientsBySession, clientEvent.sessionId)
            sendState(broadcastSocket, clientEvent.sessionId, 'error')
            send(broadcastSocket, 'error', clientEvent.sessionId, {
              code: 'memory_reset_failed',
              message: error instanceof Error ? error.message : 'Memory reset failed.',
            })
          })
        return
      }

      if (clientEvent.type === 'memory.review.list') {
        const broadcastSocket = sessionBroadcaster(clientsBySession, clientEvent.sessionId)
        void sendMemoryReviewCandidates(broadcastSocket, clientEvent.sessionId, options, clientEvent.payload.status ?? 'pending')
          .then(() => sendMemoryReviewJobs(broadcastSocket, clientEvent.sessionId, options, 'all'))
        return
      }

      if (clientEvent.type === 'memory.review.run') {
        void Promise.resolve(options.runMemoryReview?.(clientEvent.sessionId, clientEvent.payload.force ?? true) ?? {
          reviewed: false,
          error: 'memory review is unavailable',
        })
          .then((payload) => {
            const broadcastSocket = sessionBroadcaster(clientsBySession, clientEvent.sessionId)
            send(broadcastSocket, 'memory.review.updated', clientEvent.sessionId, payload)
            return sendMemoryReviewCandidates(broadcastSocket, clientEvent.sessionId, options, 'pending')
              .then(() => sendMemoryReviewJobs(broadcastSocket, clientEvent.sessionId, options, 'all'))
          })
        return
      }

      if (clientEvent.type === 'memory.review.accept') {
        void Promise.resolve(options.acceptMemoryReviewCandidate?.(clientEvent.payload.candidateId) ?? {
          accepted: false,
          error: 'memory review is unavailable',
        })
          .then((payload) => {
            const broadcastSocket = sessionBroadcaster(clientsBySession, clientEvent.sessionId)
            send(broadcastSocket, 'memory.review.updated', clientEvent.sessionId, payload)
            sendHello(broadcastSocket, clientEvent.sessionId, options).catch(() => {})
            return sendMemoryReviewCandidates(broadcastSocket, clientEvent.sessionId, options, 'pending')
          })
        return
      }

      if (clientEvent.type === 'memory.review.reject') {
        void Promise.resolve(options.rejectMemoryReviewCandidate?.(clientEvent.payload.candidateId) ?? {
          rejected: false,
          error: 'memory review is unavailable',
        })
          .then((payload) => {
            const broadcastSocket = sessionBroadcaster(clientsBySession, clientEvent.sessionId)
            send(broadcastSocket, 'memory.review.updated', clientEvent.sessionId, payload)
            return sendMemoryReviewCandidates(broadcastSocket, clientEvent.sessionId, options, 'pending')
          })
        return
      }

      if (clientEvent.type === 'tool.permission.response') {
        void options.forwardToolPermissionToPython(clientEvent.payload.requestId, clientEvent.payload.approved)
        return
      }

      if (
        clientEvent.type === 'desktop.capabilities'
        || clientEvent.type === 'audio.playback-started'
        || clientEvent.type === 'audio.playback-ended'
        || clientEvent.type === 'audio.playback-error'
      ) {
        if (clientEvent.type === 'desktop.capabilities') {
          logBridge('received client capabilities feedback', {
            clientId: client.id,
            sessionId: clientEvent.sessionId,
            surface: clientEvent.surface ?? null,
            capabilities: summarizeCapabilitiesPayload(clientEvent.payload),
          })
        }
        void Promise.resolve(options.observeDesktopFeedback?.(clientEvent))
          .then((events) => {
            logBridge('runtime feedback processed', {
              clientId: client.id,
              sessionId: clientEvent.sessionId,
              surface: clientEvent.surface ?? null,
              eventType: clientEvent.type,
              emittedEventCount: Array.isArray(events) ? events.length : 0,
            })
            if (!Array.isArray(events)) {
              return
            }
            const broadcastSocket = sessionBroadcaster(clientsBySession, clientEvent.sessionId)
            for (const emitted of events) {
              broadcastSocket.send(JSON.stringify(emitted))
            }
          })
          .catch(() => {})
        return
      }

      if (clientEvent.type === 'user.message') {
        const broadcastSocket = sessionBroadcaster(clientsBySession, clientEvent.sessionId)
        void Promise.resolve(options.streamChat(broadcastSocket, clientEvent.sessionId, clientEvent.payload.text, clientEvent.payload.skills)).catch((error: unknown) => {
          sendState(broadcastSocket, clientEvent.sessionId, 'error')
          send(broadcastSocket, 'error', clientEvent.sessionId, {
            code: 'runtime_error',
            message: error instanceof Error ? error.message : 'Unknown runtime error.',
          })
        })
      }
    })
  })

  return { httpServer, wss }
}
