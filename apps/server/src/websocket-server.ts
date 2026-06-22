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
import {
  type LocalLive2DModelLibrary,
  writeLive2DConfig,
  writeLive2DModels,
  writeLive2DModelFile,
  writeLive2DSelection,
} from './live2d.js'

export interface AmadeusBridgeServerOptions {
  model: string
  defaultSessionId: string
  countPersistedMessages(sessionId: string): number
  getToolPermissions(): ToolPermissionState[] | Promise<ToolPermissionState[]>
  resetSession(sessionId: string): void
  resolvePendingToolPermission(requestId: string, approved: boolean): boolean
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
  }>): void | Promise<void>
  live2dLibrary?: LocalLive2DModelLibrary
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

async function readRequestJson(request: IncomingMessage): Promise<unknown> {
  let body = ''
  for await (const chunk of request) {
    body += String(chunk)
  }
  if (!body.trim()) {
    return undefined
  }
  return JSON.parse(body) as unknown
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

    if (requestUrl === '/live2d/config' && options.live2dLibrary) {
      writeLive2DConfig(response, options.live2dLibrary)
      return
    }

    if (requestUrl === '/live2d/models' && request.method === 'GET' && options.live2dLibrary) {
      writeLive2DModels(response, options.live2dLibrary)
      return
    }

    if (requestUrl === '/live2d/select' && request.method === 'POST' && options.live2dLibrary) {
      void readRequestJson(request)
        .then((payload) => writeLive2DSelection(response, options.live2dLibrary!, payload))
        .catch(() => {
          response.writeHead(400, { 'Content-Type': 'application/json' })
          response.end(JSON.stringify({ ok: false, error: 'invalid_json' }))
        })
      return
    }

    if (requestUrl.startsWith('/live2d/models/') && options.live2dLibrary) {
      writeLive2DModelFile(response, options.live2dLibrary, requestUrl.slice('/live2d/models/'.length))
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
        options.resetSession(event.sessionId)
        void sendHello(socket, event.sessionId, options, 0)
          .then(() => sendMemoryReviewCandidates(socket, event.sessionId, options, 'pending'))
          .then(() => sendMemoryReviewJobs(socket, event.sessionId, options, 'all'))
          .then(() => sendState(socket, event.sessionId, 'idle'))
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

      if (
        event.type === 'desktop.capabilities'
        || event.type === 'audio.playback-started'
        || event.type === 'audio.playback-ended'
        || event.type === 'audio.playback-error'
      ) {
        void options.observeDesktopFeedback?.(event)
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
