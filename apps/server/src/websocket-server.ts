import type {
  AssistantState,
  ClientRuntimeEvent,
  RuntimeEvent,
  ServerRuntimeEvent,
  MemoryReviewCandidatesPayload,
  MemoryReviewUpdatedPayload,
  ToolPermissionState,
} from '@amadeus-agent/amadeus/events'

import { randomUUID } from 'node:crypto'
import { createServer, type Server as HttpServer } from 'node:http'
import { WebSocket, WebSocketServer } from 'ws'

export interface AmadeusBridgeServerOptions {
  model: string
  defaultSessionId: string
  countPersistedMessages(sessionId: string): number
  getToolPermissions(): ToolPermissionState[] | Promise<ToolPermissionState[]>
  resetSession(sessionId: string): void
  resolvePendingToolPermission(requestId: string, approved: boolean): boolean
  forwardToolPermissionToPython(requestId: string, approved: boolean): void | Promise<void>
  listMemoryReviewCandidates?(sessionId: string, status?: MemoryReviewCandidatesPayload['status']): MemoryReviewCandidatesPayload | Promise<MemoryReviewCandidatesPayload>
  runMemoryReview?(sessionId: string, force: boolean): MemoryReviewUpdatedPayload | Promise<MemoryReviewUpdatedPayload>
  acceptMemoryReviewCandidate?(candidateId: number): MemoryReviewUpdatedPayload | Promise<MemoryReviewUpdatedPayload>
  rejectMemoryReviewCandidate?(candidateId: number): MemoryReviewUpdatedPayload | Promise<MemoryReviewUpdatedPayload>
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
  memoryMessages: number,
): Promise<void> {
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
    if (request.url === '/health') {
      response.writeHead(200, { 'Content-Type': 'application/json' })
      response.end(JSON.stringify({ ok: true, model: options.model }))
      return
    }

    response.writeHead(404, { 'Content-Type': 'application/json' })
    response.end(JSON.stringify({ error: 'not_found' }))
  })

  const wss = new WebSocketServer({ server: httpServer, path: '/ws' })

  wss.on('connection', (socket) => {
    const sessionId = options.defaultSessionId
    void sendHello(socket, sessionId, options, options.countPersistedMessages(sessionId))
      .then(() => sendMemoryReviewCandidates(socket, sessionId, options, 'pending'))
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
        options.resetSession(event.sessionId)
        void sendHello(socket, event.sessionId, options, 0)
          .then(() => sendMemoryReviewCandidates(socket, event.sessionId, options, 'pending'))
          .then(() => sendState(socket, event.sessionId, 'idle'))
        return
      }

      if (event.type === 'memory.review.list') {
        void sendMemoryReviewCandidates(socket, event.sessionId, options, event.payload.status ?? 'pending')
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
            sendHello(socket, event.sessionId, options, options.countPersistedMessages(event.sessionId)).catch(() => {})
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
        const resolvedLocally = options.resolvePendingToolPermission(
          event.payload.requestId,
          event.payload.approved,
        )
        if (resolvedLocally) {
          return
        }

        void options.forwardToolPermissionToPython(event.payload.requestId, event.payload.approved)
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
