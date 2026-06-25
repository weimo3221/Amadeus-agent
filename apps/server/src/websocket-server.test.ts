import { describe, it } from 'node:test'
import assert from 'node:assert/strict'

import { createServer, type IncomingMessage, type ServerResponse } from 'node:http'
import { once } from 'node:events'
import { WebSocket } from 'ws'

import type { RuntimeEvent } from '@amadeus-agent/amadeus/events'

import { forwardToolPermissionToPython, proxyPythonLive2DRequest, proxyPythonSkillsRequest, relayPythonTurn } from './bridge.js'
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

async function delay(ms: number): Promise<void> {
  await new Promise((resolve) => setTimeout(resolve, ms))
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
  it('proxies Live2D HTTP requests through the Python runtime and rewrites model URLs to the bridge origin', async (t) => {
    let selectedModelId = 'hiyori-free'
    const runtimeModels = {
      'hiyori-free': {
        id: 'hiyori-free',
        path: 'hiyori-free/hiyori.model3.json',
        manifest: { displayName: 'Hiyori Free' },
      },
      'hiyori-pro': {
        id: 'hiyori-pro',
        path: 'hiyori-pro/hiyori-pro.model3.json',
        manifest: { displayName: 'Hiyori Pro' },
      },
    }
    const pythonRuntime = createServer(async (request: IncomingMessage, response: ServerResponse) => {
      if (request.method === 'GET' && request.url === '/live2d/config') {
        const model = runtimeModels[selectedModelId as keyof typeof runtimeModels]
        response.writeHead(200, { 'Content-Type': 'application/json; charset=utf-8' })
        response.end(JSON.stringify({
          ok: true,
          model: {
            ...model,
            url: `http://127.0.0.1:8790/live2d/models/${model.path}`,
          },
        }))
        return
      }

      if (request.method === 'GET' && request.url === '/live2d/models') {
        response.writeHead(200, { 'Content-Type': 'application/json; charset=utf-8' })
        response.end(JSON.stringify({
          ok: true,
          models: Object.values(runtimeModels).map((model) => ({
            ...model,
            url: `http://127.0.0.1:8790/live2d/models/${model.path}`,
            active: model.id === selectedModelId,
          })),
          activeModel: {
            ...runtimeModels[selectedModelId as keyof typeof runtimeModels],
            url: `http://127.0.0.1:8790/live2d/models/${runtimeModels[selectedModelId as keyof typeof runtimeModels].path}`,
          },
        }))
        return
      }

      if (request.method === 'POST' && request.url === '/live2d/select') {
        const payload = JSON.parse(await readBody(request)) as { modelId?: string }
        if (!payload.modelId || !(payload.modelId in runtimeModels)) {
          response.writeHead(400, { 'Content-Type': 'application/json; charset=utf-8' })
          response.end(JSON.stringify({ ok: false, error: 'live2d_model_not_found' }))
          return
        }
        selectedModelId = payload.modelId
        const model = runtimeModels[selectedModelId as keyof typeof runtimeModels]
        response.writeHead(200, { 'Content-Type': 'application/json; charset=utf-8' })
        response.end(JSON.stringify({
          ok: true,
          model: {
            ...model,
            url: `http://127.0.0.1:8790/live2d/models/${model.path}`,
          },
        }))
        return
      }

      if (request.method === 'GET' && request.url?.startsWith('/live2d/models/')) {
        response.writeHead(200, {
          'Content-Type': 'application/json; charset=utf-8',
          'Access-Control-Allow-Origin': '*',
        })
        response.end('{"Version":3}')
        return
      }

      response.writeHead(404, { 'Content-Type': 'application/json; charset=utf-8' })
      response.end(JSON.stringify({ ok: false, error: 'not_found' }))
    })
    const runtimePort = await listen(pythonRuntime)
    t.after(() => {
      void closeServer(pythonRuntime)
    })

    let bridgePort = 0
    const bridge = createAmadeusBridgeServer({
      model: 'test-model',
      defaultSessionId: 'default',
      getMemoryMessageCount: () => 0,
      getToolPermissions: () => [],
      resetSession: () => {},
      forwardToolPermissionToPython: () => {},
      handleLive2DHttpRequest(request, response, requestUrl) {
        return proxyPythonLive2DRequest(request, response, requestUrl, {
          runtimeUrl: `http://127.0.0.1:${runtimePort}`,
          publicBaseUrl: `http://127.0.0.1:${bridgePort}`,
        })
      },
      streamChat: () => {},
    })
    bridgePort = await listen(bridge.httpServer)
    t.after(() => {
      bridge.wss.close()
      void closeServer(bridge.httpServer)
    })

    const configResponse = await fetch(`http://127.0.0.1:${bridgePort}/live2d/config`)
    assert.equal(configResponse.status, 200)
    const configPayload = await configResponse.json() as {
      ok: boolean
      model: { id: string; path: string; url: string; manifest?: { displayName?: string } }
    }
    assert.equal(configPayload.ok, true)
    assert.equal(configPayload.model.id, 'hiyori-free')
    assert.equal(
      configPayload.model.url,
      `http://127.0.0.1:${bridgePort}/live2d/models/hiyori-free/hiyori.model3.json`,
    )
    assert.equal(configPayload.model.manifest?.displayName, 'Hiyori Free')

    const modelsResponse = await fetch(`http://127.0.0.1:${bridgePort}/live2d/models`)
    assert.equal(modelsResponse.status, 200)
    const modelsPayload = await modelsResponse.json() as {
      ok: boolean
      models: Array<{ id: string; url: string; active: boolean }>
      activeModel: { id: string; url: string }
    }
    assert.equal(modelsPayload.ok, true)
    assert.equal(modelsPayload.models.length, 2)
    assert.equal(
      modelsPayload.models[0]?.url.startsWith(`http://127.0.0.1:${bridgePort}/live2d/models/`),
      true,
    )
    assert.equal(modelsPayload.activeModel.id, 'hiyori-free')

    const modelResponse = await fetch(`http://127.0.0.1:${bridgePort}/live2d/models/hiyori-free/hiyori.model3.json`)
    assert.equal(modelResponse.status, 200)
    assert.equal(modelResponse.headers.get('access-control-allow-origin'), '*')
    assert.deepEqual(await modelResponse.json(), { Version: 3 })

    const selectResponse = await fetch(`http://127.0.0.1:${bridgePort}/live2d/select`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ modelId: 'hiyori-pro' }),
    })
    assert.equal(selectResponse.status, 200)
    const selectPayload = await selectResponse.json() as {
      ok: boolean
      model: { id: string; url: string }
    }
    assert.equal(selectPayload.ok, true)
    assert.equal(selectPayload.model.id, 'hiyori-pro')
    assert.equal(
      selectPayload.model.url,
      `http://127.0.0.1:${bridgePort}/live2d/models/hiyori-pro/hiyori-pro.model3.json`,
    )
  })

  it('proxies read-only skills HTTP requests through the Python runtime', async (t) => {
    const pythonRuntime = createServer((request: IncomingMessage, response: ServerResponse) => {
      if (request.method === 'GET' && request.url === '/skills/list') {
        response.writeHead(200, { 'Content-Type': 'application/json; charset=utf-8' })
        response.end(JSON.stringify({
          ok: true,
          skills: [
            {
              name: 'runtime-debug',
              identifier: 'development/runtime-debug',
              description: 'Debug runtime behavior.',
            },
            {
              name: 'desktop-e2e',
              identifier: 'development/desktop-e2e',
              description: 'Exercise desktop E2E workflows.',
            },
          ],
        }))
        return
      }

      if (request.method === 'GET' && request.url === '/skills/view?name=development%2Fruntime-debug') {
        response.writeHead(200, { 'Content-Type': 'application/json; charset=utf-8' })
        response.end(JSON.stringify({
          ok: true,
          skill: {
            name: 'runtime-debug',
            identifier: 'development/runtime-debug',
            description: 'Debug runtime behavior.',
            instructions: 'Use evidence.',
          },
        }))
        return
      }

      response.writeHead(404, { 'Content-Type': 'application/json; charset=utf-8' })
      response.end(JSON.stringify({ ok: false, error: 'not_found' }))
    })
    const runtimePort = await listen(pythonRuntime)
    t.after(() => {
      void closeServer(pythonRuntime)
    })

    const bridge = createAmadeusBridgeServer({
      model: 'test-model',
      defaultSessionId: 'default',
      getMemoryMessageCount: () => 0,
      getToolPermissions: () => [],
      resetSession: () => {},
      forwardToolPermissionToPython: () => {},
      handleSkillsHttpRequest(request, response, requestUrl) {
        return proxyPythonSkillsRequest(request, response, requestUrl, {
          runtimeUrl: `http://127.0.0.1:${runtimePort}`,
        })
      },
      streamChat: () => {},
    })
    const bridgePort = await listen(bridge.httpServer)
    t.after(() => {
      bridge.wss.close()
      void closeServer(bridge.httpServer)
    })

    const listResponse = await fetch(`http://127.0.0.1:${bridgePort}/skills/list`)
    assert.equal(listResponse.status, 200)
    const listPayload = await listResponse.json() as {
      ok: boolean
      skills: Array<{ identifier: string }>
    }
    assert.equal(listPayload.ok, true)
    assert.deepEqual(
      listPayload.skills.map((skill) => skill.identifier),
      ['development/runtime-debug', 'development/desktop-e2e'],
    )

    const viewResponse = await fetch(`http://127.0.0.1:${bridgePort}/skills/view?name=development%2Fruntime-debug`)
    assert.equal(viewResponse.status, 200)
    const viewPayload = await viewResponse.json() as {
      ok: boolean
      skill: { identifier: string; instructions: string }
    }
    assert.equal(viewPayload.ok, true)
    assert.equal(viewPayload.skill.identifier, 'development/runtime-debug')
    assert.equal(viewPayload.skill.instructions, 'Use evidence.')
  })

  it('allows browser CORS preflight for Live2D model switching', async (t) => {
    const bridge = createAmadeusBridgeServer({
      model: 'test-model',
      defaultSessionId: 'default',
      getMemoryMessageCount: () => 0,
      getToolPermissions: () => [],
      resetSession: () => {},
      forwardToolPermissionToPython: () => {},
      streamChat: () => {},
    })
    const bridgePort = await listen(bridge.httpServer)
    t.after(() => {
      bridge.wss.close()
      void closeServer(bridge.httpServer)
    })

    const response = await fetch(`http://127.0.0.1:${bridgePort}/live2d/select`, {
      method: 'OPTIONS',
      headers: {
        Origin: 'http://localhost:5173',
        'Access-Control-Request-Method': 'POST',
        'Access-Control-Request-Headers': 'content-type',
      },
    })

    assert.equal(response.status, 204)
    assert.equal(response.headers.get('access-control-allow-origin'), '*')
    assert.match(response.headers.get('access-control-allow-methods') ?? '', /POST/)
    assert.match(response.headers.get('access-control-allow-headers') ?? '', /Content-Type/i)
  })

  it('sends server.hello with async Python tool permissions', async (t) => {
    const bridge = createAmadeusBridgeServer({
      model: 'test-model',
      defaultSessionId: 'default',
      async getMemoryMessageCount() {
        await new Promise((resolve) => setTimeout(resolve, 5))
        return 3
      },
      async getToolPermissions() {
        await new Promise((resolve) => setTimeout(resolve, 10))
        return [
          { name: 'write_file', displayName: 'Writing local file', enabled: true, permission: 'ask' },
        ]
      },
      resetSession: () => {},
      forwardToolPermissionToPython: () => {},
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

    const hello = await waitForEvent(socket, (event) => event.type === 'server.hello')
    assert.deepEqual(hello.payload, {
      name: 'amadeus-agent-server',
      model: 'test-model',
      memoryMessages: 3,
      toolPermissions: [
        { name: 'write_file', displayName: 'Writing local file', enabled: true, permission: 'ask' },
      ],
    })
  })

  it('refreshes memory count from the async reset path after session.reset', async (t) => {
    let memoryMessages = 2
    const receivedEvents: Array<RuntimeEvent<string, unknown>> = []
    const bridge = createAmadeusBridgeServer({
      model: 'test-model',
      defaultSessionId: 'default',
      getMemoryMessageCount: () => memoryMessages,
      getToolPermissions: () => [],
      async resetSession() {
        await delay(10)
        memoryMessages = 0
      },
      forwardToolPermissionToPython: () => {},
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

    socket.on('message', (raw: Buffer) => {
      receivedEvents.push(JSON.parse(raw.toString()) as RuntimeEvent<string, unknown>)
    })
    await delay(30)

    socket.send(JSON.stringify({
      id: 'client-event-reset',
      type: 'session.reset',
      sessionId: 'default',
      timestamp: '2026-06-24T00:00:00.000Z',
      payload: {},
    }))
    await delay(40)

    assert.ok(receivedEvents.some((event) =>
      event.type === 'server.hello'
      && (event.payload as { memoryMessages?: number }).memoryMessages === 0,
    ))
  })

  it('reports an error when the async reset path fails', async (t) => {
    const receivedEvents: Array<RuntimeEvent<string, unknown>> = []
    const bridge = createAmadeusBridgeServer({
      model: 'test-model',
      defaultSessionId: 'default',
      getMemoryMessageCount: () => 2,
      getToolPermissions: () => [],
      async resetSession() {
        throw new Error('reset offline')
      },
      forwardToolPermissionToPython: () => {},
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

    socket.on('message', (raw: Buffer) => {
      receivedEvents.push(JSON.parse(raw.toString()) as RuntimeEvent<string, unknown>)
    })
    await delay(30)

    socket.send(JSON.stringify({
      id: 'client-event-reset-failure',
      type: 'session.reset',
      sessionId: 'default',
      timestamp: '2026-06-24T00:00:00.000Z',
      payload: {},
    }))
    await delay(40)

    const errorEvent = receivedEvents.find((event) =>
      event.type === 'error'
      && (event.payload as { code?: string }).code === 'memory_reset_failed',
    )
    assert.ok(errorEvent)
    assert.deepEqual(errorEvent.payload, {
      code: 'memory_reset_failed',
      message: 'reset offline',
    })
  })

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
      getMemoryMessageCount: () => 0,
      getToolPermissions: () => [],
      resetSession: () => {},
      forwardToolPermissionToPython: () => {},
      async streamChat(socket, sessionId, text, skills) {
        await relayPythonTurn(socket, sessionId, text, skills, {
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
        skills: ['runtime-debug'],
      },
    }))

    const event = await assistantMessage

    assert.deepEqual(receivedTurnBody, {
      sessionId: 'default',
      text: 'hello',
      inputMode: 'text',
      skills: ['runtime-debug'],
    })
    assert.equal(event.id, 'python-event-2')
    assert.equal(event.type, 'assistant.message')
    assert.deepEqual(event.payload, { text: 'hello' })
  })

  it('forwards desktop permission responses to Python', async (t) => {
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
      getMemoryMessageCount: () => 0,
      getToolPermissions: () => [],
      resetSession: () => {},
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

  it('handles memory review list, run, accept, and reject events', async (t) => {
    const calls: string[] = []
    const bridge = createAmadeusBridgeServer({
      model: 'test-model',
      defaultSessionId: 'default',
      getMemoryMessageCount: () => 2,
      getToolPermissions: () => [],
      resetSession: () => {},
      forwardToolPermissionToPython: () => {},
      listMemoryReviewCandidates(sessionId, status = 'pending') {
        calls.push(`list:${sessionId}:${status}`)
        return {
          status,
          candidateCount: 1,
          candidates: [{
            candidateId: 7,
            sessionId,
            scope: 'project',
            content: 'Memory candidates require human approval.',
            confidence: 0.9,
            status: 'pending',
            memoryItemId: 0,
          }],
        }
      },
      listMemoryReviewJobs(sessionId, status = 'all') {
        calls.push(`jobs:${sessionId}:${status}`)
        return {
          status,
          jobCount: 1,
          jobs: [{
            jobId: 9,
            sessionId,
            trigger: 'manual',
            status: 'completed',
            reason: '',
            error: '',
            sourceMessageStartId: 1,
            sourceMessageEndId: 2,
            sourceMessageCount: 2,
            proposedCandidateCount: 1,
            savedCandidateCount: 1,
            suppressedCandidateCount: 0,
            startedAt: '2026-06-21T00:00:00.000Z',
            finishedAt: '2026-06-21T00:00:00.100Z',
            durationMs: 100,
          }],
        }
      },
      runMemoryReview(sessionId, force) {
        calls.push(`run:${sessionId}:${force}`)
        return { reviewed: true, sessionId, candidateCount: 1, candidates: [] }
      },
      acceptMemoryReviewCandidate(candidateId) {
        calls.push(`accept:${candidateId}`)
        return { accepted: true }
      },
      rejectMemoryReviewCandidate(candidateId) {
        calls.push(`reject:${candidateId}`)
        return { rejected: true }
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
    const receivedEvents: Array<RuntimeEvent<string, unknown>> = []
    socket.on('message', (raw: Buffer) => {
      receivedEvents.push(JSON.parse(raw.toString()) as RuntimeEvent<string, unknown>)
    })
    socket.send(JSON.stringify({
      id: 'client-event-review-list',
      type: 'memory.review.list',
      sessionId: 'default',
      timestamp: '2026-06-19T00:00:00.000Z',
      payload: { status: 'pending' },
    }))
    await delay(25)
    const candidatesEvent = receivedEvents.find((event) => event.type === 'memory.review.candidates')
    const jobsEvent = receivedEvents.find((event) => event.type === 'memory.review.jobs')
    assert.ok(candidatesEvent)
    assert.ok(jobsEvent)
    assert.equal((candidatesEvent.payload as { candidateCount: number }).candidateCount, 1)
    assert.equal((jobsEvent.payload as { jobCount: number }).jobCount, 1)

    socket.send(JSON.stringify({
      id: 'client-event-review-run',
      type: 'memory.review.run',
      sessionId: 'default',
      timestamp: '2026-06-19T00:00:00.000Z',
      payload: { force: true },
    }))
    await delay(25)

    socket.send(JSON.stringify({
      id: 'client-event-review-accept',
      type: 'memory.review.accept',
      sessionId: 'default',
      timestamp: '2026-06-19T00:00:00.000Z',
      payload: { candidateId: 7 },
    }))
    await delay(25)

    socket.send(JSON.stringify({
      id: 'client-event-review-reject',
      type: 'memory.review.reject',
      sessionId: 'default',
      timestamp: '2026-06-19T00:00:00.000Z',
      payload: { candidateId: 7 },
    }))
    await delay(25)

    assert.ok(calls.includes('list:default:pending'))
    assert.ok(calls.includes('jobs:default:all'))
    assert.ok(calls.includes('run:default:true'))
    assert.ok(calls.includes('accept:7'))
    assert.ok(calls.includes('reject:7'))
    assert.ok(receivedEvents.some((event) => event.type === 'memory.review.updated' && (event.payload as { reviewed?: boolean }).reviewed === true))
    assert.ok(receivedEvents.some((event) => event.type === 'memory.review.updated' && (event.payload as { accepted?: boolean }).accepted === true))
    assert.ok(receivedEvents.some((event) => event.type === 'memory.review.updated' && (event.payload as { rejected?: boolean }).rejected === true))
  })

  it('observes desktop capabilities and audio playback feedback events', async (t) => {
    const observed: Array<RuntimeEvent<string, unknown>> = []
    const receivedEvents: Array<RuntimeEvent<string, unknown>> = []
    const bridge = createAmadeusBridgeServer({
      model: 'test-model',
      defaultSessionId: 'default',
      getMemoryMessageCount: () => 0,
      getToolPermissions: () => [],
      resetSession: () => {},
      forwardToolPermissionToPython: () => {},
      observeDesktopFeedback(event) {
        observed.push(event)
        if (event.type !== 'audio.playback-started') {
          return []
        }
        return [{
          id: 'feedback-character-event',
          type: 'character.behavior',
          sessionId: event.sessionId,
          timestamp: '2026-06-22T00:00:01.050Z',
          payload: {
            emotion: 'neutral',
            expression: 'smile',
            motion: 'talk',
            intensity: 0.65,
          },
        }]
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
    socket.on('message', (raw: Buffer) => {
      receivedEvents.push(JSON.parse(raw.toString()) as RuntimeEvent<string, unknown>)
    })

    socket.send(JSON.stringify({
      id: 'client-capabilities',
      type: 'desktop.capabilities',
      sessionId: 'default',
      timestamp: '2026-06-22T00:00:00.000Z',
      payload: {
        desktop: {
          runtime: 'electron',
          protocolVersion: 1,
        },
        live2d: {
          available: true,
          modelId: 'hiyori-free',
          expressions: ['smile'],
          motions: ['Idle'],
        },
        audio: {
          runtimeAudio: true,
          speechSynthesis: true,
          voiceCount: 1,
        },
      },
    }))
    socket.send(JSON.stringify({
      id: 'client-audio-started',
      type: 'audio.playback-started',
      sessionId: 'default',
      timestamp: '2026-06-22T00:00:01.000Z',
      payload: {
        source: 'runtime_audio',
        audioUrl: 'http://runtime/audio.wav',
      },
    }))
    socket.send(JSON.stringify({
      id: 'client-audio-ended',
      type: 'audio.playback-ended',
      sessionId: 'default',
      timestamp: '2026-06-22T00:00:02.000Z',
      payload: {
        source: 'runtime_audio',
        audioUrl: 'http://runtime/audio.wav',
      },
    }))
    socket.send(JSON.stringify({
      id: 'client-audio-error',
      type: 'audio.playback-error',
      sessionId: 'default',
      timestamp: '2026-06-22T00:00:03.000Z',
      payload: {
        source: 'runtime_audio',
        audioUrl: 'http://runtime/broken.wav',
        reason: 'audio_element_error',
      },
    }))

    await delay(25)

    assert.deepEqual(observed.map((event) => event.type), [
      'desktop.capabilities',
      'audio.playback-started',
      'audio.playback-ended',
      'audio.playback-error',
    ])
    assert.ok(receivedEvents.some((event) => (
      event.type === 'character.behavior'
      && (event.payload as { motion?: string }).motion === 'talk'
    )))
  })
})
