import type {
  AssistantState,
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
  streamChat(socket: WebSocket, sessionId: string, text: string): void | Promise<void>
}

export interface AmadeusBridgeServer {
  httpServer: HttpServer
  wss: WebSocketServer
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
  socket: WebSocket,
  type: TType,
  sessionId: string,
  payload: TPayload,
): void {
  socket.send(JSON.stringify(makeEvent(type, sessionId, payload)))
}

function sendState(socket: WebSocket, sessionId: string, state: AssistantState): void {
  send(socket, 'assistant.state', sessionId, { state })
}

async function sendHello(
  socket: WebSocket,
  sessionId: string,
  options: AmadeusBridgeServerOptions,
): Promise<void> {
  const memoryMessages = await Promise.resolve(options.getMemoryMessageCount(sessionId)).catch(() => 0)
  const toolPermissions = await Promise.resolve(options.getToolPermissions()).catch(() => [])
  if (socket.readyState !== WebSocket.OPEN) {
    return
  }

  send(socket, 'server.hello', sessionId, {
    name: 'amadeus-agent-server',
    model: options.model,
    memoryMessages,
    toolPermissions,
  })
}

async function sendMemoryReviewCandidates(
  socket: WebSocket,
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
  if (socket.readyState !== WebSocket.OPEN) {
    return
  }

  send(socket, 'memory.review.candidates', sessionId, payload)
}

async function sendMemoryReviewJobs(
  socket: WebSocket,
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
  if (socket.readyState !== WebSocket.OPEN) {
    return
  }

  send(socket, 'memory.review.jobs', sessionId, payload)
}

function parseEvent(raw: Buffer): ClientRuntimeEvent | undefined {
  try {
    const data = JSON.parse(raw.toString()) as ClientRuntimeEvent
    if (!data || typeof data.type !== 'string' || typeof data.sessionId !== 'string') {
      return undefined
    }
    return data
  }
  catch {
    return undefined
  }
}

export function createAmadeusBridgeServer(options: AmadeusBridgeServerOptions): AmadeusBridgeServer {
  const httpServer = createServer((request, response) => {
    const requestUrl = request.url ?? '/'
    if (request.method === 'OPTIONS') {
      response.writeHead(204, {
        'Access-Control-Allow-Origin': '*',
        'Access-Control-Allow-Methods': 'GET,POST,OPTIONS',
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

    response.writeHead(404, { 'Content-Type': 'application/json' })
    response.end(JSON.stringify({ error: 'not_found' }))
  })

  const wss = new WebSocketServer({ server: httpServer, path: '/ws' })

  wss.on('connection', (socket) => {
    const sessionId = options.defaultSessionId
    void sendHello(socket, sessionId, options)
      .then(() => sendMemoryReviewCandidates(socket, sessionId, options, 'pending'))
      .then(() => sendMemoryReviewJobs(socket, sessionId, options, 'all'))
      .then(() => sendState(socket, sessionId, 'idle'))

    socket.on('message', (raw) => {
      const event = parseEvent(raw as Buffer)
      if (!event) {
        send(socket, 'error', sessionId, {
          code: 'bad_event',
          message: 'Could not parse client event.',
        })
        return
      }

      if (event.type === 'session.reset') {
        void Promise.resolve(options.resetSession(event.sessionId))
          .then(() => sendHello(socket, event.sessionId, options))
          .then(() => sendMemoryReviewCandidates(socket, event.sessionId, options, 'pending'))
          .then(() => sendMemoryReviewJobs(socket, event.sessionId, options, 'all'))
          .then(() => sendState(socket, event.sessionId, 'idle'))
          .catch((error: unknown) => {
            sendState(socket, event.sessionId, 'error')
            send(socket, 'error', event.sessionId, {
              code: 'memory_reset_failed',
              message: error instanceof Error ? error.message : 'Memory reset failed.',
            })
          })
        return
      }

      if (event.type === 'memory.review.list') {
        void sendMemoryReviewCandidates(socket, event.sessionId, options, event.payload.status ?? 'pending')
          .then(() => sendMemoryReviewJobs(socket, event.sessionId, options, 'all'))
        return
      }

      if (event.type === 'memory.review.run') {
        void Promise.resolve(options.runMemoryReview?.(event.sessionId, event.payload.force ?? true) ?? {
          reviewed: false,
          error: 'memory review is unavailable',
        })
          .then((payload) => {
            send(socket, 'memory.review.updated', event.sessionId, payload)
            return sendMemoryReviewCandidates(socket, event.sessionId, options, 'pending')
              .then(() => sendMemoryReviewJobs(socket, event.sessionId, options, 'all'))
          })
        return
      }

      if (event.type === 'memory.review.accept') {
        void Promise.resolve(options.acceptMemoryReviewCandidate?.(event.payload.candidateId) ?? {
          accepted: false,
          error: 'memory review is unavailable',
        })
          .then((payload) => {
            send(socket, 'memory.review.updated', event.sessionId, payload)
            sendHello(socket, event.sessionId, options).catch(() => {})
            return sendMemoryReviewCandidates(socket, event.sessionId, options, 'pending')
          })
        return
      }

      if (event.type === 'memory.review.reject') {
        void Promise.resolve(options.rejectMemoryReviewCandidate?.(event.payload.candidateId) ?? {
          rejected: false,
          error: 'memory review is unavailable',
        })
          .then((payload) => {
            send(socket, 'memory.review.updated', event.sessionId, payload)
            return sendMemoryReviewCandidates(socket, event.sessionId, options, 'pending')
          })
        return
      }

      if (event.type === 'tool.permission.response') {
        void options.forwardToolPermissionToPython(event.payload.requestId, event.payload.approved)
        return
      }

      if (
        event.type === 'desktop.capabilities'
        || event.type === 'audio.playback-started'
        || event.type === 'audio.playback-ended'
        || event.type === 'audio.playback-error'
      ) {
        void Promise.resolve(options.observeDesktopFeedback?.(event))
          .then((events) => {
            if (!Array.isArray(events) || socket.readyState !== WebSocket.OPEN) {
              return
            }
            for (const emitted of events) {
              socket.send(JSON.stringify(emitted))
            }
          })
          .catch(() => {})
        return
      }

      if (event.type === 'user.message') {
        void Promise.resolve(options.streamChat(socket, event.sessionId, event.payload.text)).catch((error: unknown) => {
          sendState(socket, event.sessionId, 'error')
          send(socket, 'error', event.sessionId, {
            code: 'runtime_error',
            message: error instanceof Error ? error.message : 'Unknown runtime error.',
          })
        })
      }
    })
  })

  return { httpServer, wss }
}
