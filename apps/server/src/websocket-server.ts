import type {
  AssistantState,
  ClientRuntimeEvent,
  RuntimeEvent,
  ServerRuntimeEvent,
  ToolPermissionState,
} from '@amadeus-agent/amadeus/events'

import { randomUUID } from 'node:crypto'
import { createServer, type Server as HttpServer } from 'node:http'
import { WebSocketServer, type WebSocket } from 'ws'

export interface AmadeusBridgeServerOptions {
  model: string
  defaultSessionId: string
  countPersistedMessages(sessionId: string): number
  getToolPermissions(): ToolPermissionState[]
  resetSession(sessionId: string): void
  resolvePendingToolPermission(requestId: string, approved: boolean): boolean
  forwardToolPermissionToPython(requestId: string, approved: boolean): void | Promise<void>
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
    send(socket, 'server.hello', sessionId, {
      name: 'amadeus-agent-server',
      model: options.model,
      memoryMessages: options.countPersistedMessages(sessionId),
      toolPermissions: options.getToolPermissions(),
    })
    sendState(socket, sessionId, 'idle')

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
        send(socket, 'server.hello', event.sessionId, {
          name: 'amadeus-agent-server',
          model: options.model,
          memoryMessages: 0,
          toolPermissions: options.getToolPermissions(),
        })
        sendState(socket, event.sessionId, 'idle')
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
