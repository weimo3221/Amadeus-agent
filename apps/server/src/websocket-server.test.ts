import { describe, it } from 'node:test'
import assert from 'node:assert/strict'

import { createServer, type IncomingMessage, type ServerResponse } from 'node:http'
import { once } from 'node:events'
import { WebSocket } from 'ws'

import type { RuntimeEvent } from '@amadeus-agent/amadeus/events'

import { forwardToolPermissionToPython, relayPythonTurn } from './bridge.js'
import { createAmadeusBridgeServer } from './websocket-server.js'

async function readBody(request: IncomingMessage): Promise<string> {
  let body = ''
  for await (const chunk of request) {
    body += String(chunk)
  }
  return body
}

async function listen(server: ReturnType<typeof createServer>): Promise<number> {
  server.listen(0, '127.0.0.1')
  await once(server, 'listening')
  const address = server.address()
  assert(address && typeof address === 'object')
  return address.port
}

async function closeServer(server: ReturnType<typeof createServer>): Promise<void> {
  if (!server.listening) {
    return
  }
  await new Promise<void>((resolve, reject) => {
    server.close((error) => {
      if (error) {
        reject(error)
        return
      }
      resolve()
    })
  })
}

async function openWebSocket(url: string): Promise<WebSocket> {
  const socket = new WebSocket(url)
  await once(socket, 'open')
  return socket
}

function waitForEvent(
  socket: WebSocket,
  predicate: (event: RuntimeEvent<string, unknown>) => boolean,
): Promise<RuntimeEvent<string, unknown>> {
  return new Promise((resolve, reject) => {
    const timeout = setTimeout(() => {
      socket.off('message', onMessage)
      reject(new Error('Timed out waiting for WebSocket event'))
    }, 5000)

    function onMessage(raw: Buffer): void {
      const event = JSON.parse(raw.toString()) as RuntimeEvent<string, unknown>
      if (!predicate(event)) {
        return
      }
      clearTimeout(timeout)
      socket.off('message', onMessage)
      resolve(event)
    }

    socket.on('message', onMessage)
  })
}

function closeWebSocket(socket: WebSocket): void {
  if (socket.readyState === WebSocket.OPEN || socket.readyState === WebSocket.CONNECTING) {
    socket.close()
  }
}

describe('WebSocket Python-first integration', () => {
  it('relays desktop user messages through Python /agent/turn and returns runtime events', async (t) => {
    let receivedTurnBody: Record<string, unknown> | undefined
    const pythonRuntime = createServer(async (request: IncomingMessage, response: ServerResponse) => {
      if (request.method === 'POST' && request.url === '/agent/turn') {
        receivedTurnBody = JSON.parse(await readBody(request)) as Record<string, unknown>
        const events = [
          {
            id: 'python-event-1',
            type: 'assistant.delta',
            sessionId: 'default',
            timestamp: '2026-06-19T00:00:00.000Z',
            payload: { text: 'hel' },
          },
          {
            id: 'python-event-2',
            type: 'assistant.message',
            sessionId: 'default',
            timestamp: '2026-06-19T00:00:01.000Z',
            payload: { text: 'hello' },
          },
        ]
        response.writeHead(200, { 'Content-Type': 'application/x-ndjson' })
        response.write(`${JSON.stringify(events[0])}\n`)
        response.end(`${JSON.stringify(events[1])}\n`)
        return
      }

      response.writeHead(404)
      response.end()
    })
    const pythonPort = await listen(pythonRuntime)
    t.after(() => {
      void closeServer(pythonRuntime)
    })

    const bridge = createAmadeusBridgeServer({
      model: 'test-model',
      defaultSessionId: 'default',
      countPersistedMessages: () => 0,
      getToolPermissions: () => [],
      resetSession: () => {},
      resolvePendingToolPermission: () => false,
      forwardToolPermissionToPython: () => {},
      async streamChat(socket, sessionId, text) {
        await relayPythonTurn(socket, sessionId, text, {
          runtimeUrl: `http://127.0.0.1:${pythonPort}`,
        })
      },
    })
    const bridgePort = await listen(bridge.httpServer)
    t.after(() => {
      bridge.wss.close()
      void closeServer(bridge.httpServer)
    })

    const socket = await openWebSocket(`ws://127.0.0.1:${bridgePort}/ws`)
    t.after(() => {
      closeWebSocket(socket)
    })

    const assistantMessage = waitForEvent(socket, (event) => event.type === 'assistant.message')
    socket.send(JSON.stringify({
      id: 'client-event-1',
      type: 'user.message',
      sessionId: 'default',
      timestamp: '2026-06-19T00:00:00.000Z',
      payload: {
        text: 'hello',
        inputMode: 'text',
      },
    }))

    const event = await assistantMessage

    assert.deepEqual(receivedTurnBody, {
      sessionId: 'default',
      text: 'hello',
      inputMode: 'text',
    })
    assert.equal(event.id, 'python-event-2')
    assert.equal(event.type, 'assistant.message')
    assert.deepEqual(event.payload, { text: 'hello' })
  })

  it('forwards desktop permission responses to Python when no local pending request owns them', async (t) => {
    let resolvePermissionBody: (body: Record<string, unknown>) => void
    const permissionBody = new Promise<Record<string, unknown>>((resolve) => {
      resolvePermissionBody = resolve
    })
    const pythonRuntime = createServer(async (request: IncomingMessage, response: ServerResponse) => {
      if (request.method === 'POST' && request.url === '/tools/permission') {
        resolvePermissionBody(JSON.parse(await readBody(request)) as Record<string, unknown>)
        response.writeHead(200, { 'Content-Type': 'application/json' })
        response.end(JSON.stringify({ ok: true, resolved: true }))
        return
      }

      response.writeHead(404)
      response.end()
    })
    const pythonPort = await listen(pythonRuntime)
    t.after(() => {
      void closeServer(pythonRuntime)
    })

    const bridge = createAmadeusBridgeServer({
      model: 'test-model',
      defaultSessionId: 'default',
      countPersistedMessages: () => 0,
      getToolPermissions: () => [],
      resetSession: () => {},
      resolvePendingToolPermission: () => false,
      forwardToolPermissionToPython(requestId, approved) {
        return forwardToolPermissionToPython(requestId, approved, {
          runtimeUrl: `http://127.0.0.1:${pythonPort}`,
        })
      },
      streamChat: () => {},
    })
    const bridgePort = await listen(bridge.httpServer)
    t.after(() => {
      bridge.wss.close()
      void closeServer(bridge.httpServer)
    })

    const socket = await openWebSocket(`ws://127.0.0.1:${bridgePort}/ws`)
    t.after(() => {
      closeWebSocket(socket)
    })

    socket.send(JSON.stringify({
      id: 'client-event-2',
      type: 'tool.permission.response',
      sessionId: 'default',
      timestamp: '2026-06-19T00:00:00.000Z',
      payload: {
        requestId: 'permission-1',
        approved: true,
      },
    }))

    assert.deepEqual(await permissionBody, {
      requestId: 'permission-1',
      approved: true,
    })
  })
})
