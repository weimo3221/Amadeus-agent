import { describe, it } from 'node:test'
import assert from 'node:assert/strict'

import { fetchPythonToolPermissions } from '@amadeus-agent/amadeus/tools'

import {
  fetchPythonMemoryCount,
  forwardRuntimeFeedbackToPython,
  forwardToolPermissionToPython,
  listPythonMemoryReviewJobs,
  resetPythonMemory,
  relayPythonTurn,
  type SocketLike,
} from './bridge.js'

function streamResponse(chunks: string[], status = 200): Response {
  const encoder = new TextEncoder()
  return new Response(
    new ReadableStream<Uint8Array>({
      start(controller) {
        for (const chunk of chunks) {
          controller.enqueue(encoder.encode(chunk))
        }
        controller.close()
      },
    }),
    { status },
  )
}

function captureSocket(): { socket: SocketLike; sent: Array<Record<string, unknown>> } {
  const sent: Array<Record<string, unknown>> = []
  return {
    socket: {
      send(data: string): void {
        sent.push(JSON.parse(data) as Record<string, unknown>)
      },
    },
    sent,
  }
}

describe('Python bridge relay', () => {
  it('posts the user turn to Python and relays streamed NDJSON events', async () => {
    const { socket, sent } = captureSocket()
    const calls: Array<{ url: string; init?: RequestInit }> = []
    const events = [
      {
        id: 'event-1',
        type: 'assistant.delta',
        sessionId: 'session-1',
        timestamp: '2026-06-19T00:00:00.000Z',
        payload: { text: 'hel' },
      },
      {
        id: 'event-2',
        type: 'assistant.message',
        sessionId: 'session-1',
        timestamp: '2026-06-19T00:00:01.000Z',
        payload: { text: 'hello' },
      },
    ]
    const fetchImpl: typeof fetch = async (input, init) => {
      calls.push({ url: String(input), init })
      return streamResponse([
        `${JSON.stringify(events[0])}\n${JSON.stringify(events[1]).slice(0, 30)}`,
        `${JSON.stringify(events[1]).slice(30)}`,
      ])
    }

    const handled = await relayPythonTurn(socket, 'session-1', 'hello', undefined, {
      runtimeUrl: 'http://127.0.0.1:8790/',
      fetchImpl,
    })

    assert.equal(handled, true)
    assert.equal(calls[0].url, 'http://127.0.0.1:8790/agent/turn')
    assert.equal(calls[0].init?.method, 'POST')
    assert.deepEqual(JSON.parse(String(calls[0].init?.body)), {
      sessionId: 'session-1',
      text: 'hello',
      inputMode: 'text',
    })
    assert.deepEqual(sent, events)
  })

  it('returns false when Python cannot be reached or rejects the request', async () => {
    const { socket } = captureSocket()
    const failingFetch: typeof fetch = async () => {
      throw new Error('offline')
    }
    const rejectedFetch: typeof fetch = async () => streamResponse([], 503)

    assert.equal(await relayPythonTurn(socket, 'session-1', 'hello', undefined, {
      runtimeUrl: 'http://runtime',
      fetchImpl: failingFetch,
    }), false)
    assert.equal(await relayPythonTurn(socket, 'session-1', 'hello', undefined, {
      runtimeUrl: 'http://runtime',
      fetchImpl: rejectedFetch,
    }), false)
  })

  it('emits an error event for invalid Python NDJSON without dropping later events', async () => {
    const { socket, sent } = captureSocket()
    const validEvent = {
      id: 'event-2',
      type: 'assistant.message',
      sessionId: 'session-1',
      timestamp: '2026-06-19T00:00:01.000Z',
      payload: { text: 'done' },
    }
    const fetchImpl: typeof fetch = async () => streamResponse([
      `not-json\n${JSON.stringify(validEvent)}\n`,
    ])

    const handled = await relayPythonTurn(socket, 'session-1', 'hello', undefined, {
      runtimeUrl: 'http://runtime',
      fetchImpl,
    })

    assert.equal(handled, true)
    assert.equal(sent[0].type, 'error')
    assert.deepEqual(sent[0].payload, {
      code: 'bad_python_event',
      message: 'Python runtime emitted an invalid event.',
    })
    assert.deepEqual(sent[1], validEvent)
  })

  it('forwards explicit skill selections to Python when provided', async () => {
    const { socket } = captureSocket()
    const calls: Array<{ url: string; init?: RequestInit }> = []
    const fetchImpl: typeof fetch = async (input, init) => {
      calls.push({ url: String(input), init })
      return streamResponse([])
    }

    await relayPythonTurn(socket, 'session-1', 'hello', ['runtime-debug', 'desktop-e2e'], {
      runtimeUrl: 'http://127.0.0.1:8790/',
      fetchImpl,
    })

    assert.deepEqual(JSON.parse(String(calls[0].init?.body)), {
      sessionId: 'session-1',
      text: 'hello',
      inputMode: 'text',
      skills: ['runtime-debug', 'desktop-e2e'],
    })
  })
})

describe('Python tool permission forwarding', () => {
  it('forwards unresolved permission responses to Python', async () => {
    const calls: Array<{ url: string; init?: RequestInit }> = []
    const fetchImpl: typeof fetch = async (input, init) => {
      calls.push({ url: String(input), init })
      return new Response(JSON.stringify({ ok: true }), { status: 200 })
    }

    await forwardToolPermissionToPython('request-1', true, {
      runtimeUrl: 'http://127.0.0.1:8790/',
      fetchImpl,
    })

    assert.equal(calls[0].url, 'http://127.0.0.1:8790/tools/permission')
    assert.equal(calls[0].init?.method, 'POST')
    assert.deepEqual(JSON.parse(String(calls[0].init?.body)), {
      requestId: 'request-1',
      approved: true,
    })
  })

  it('swallows Python forwarding failures because permission requests may already be resolved or timed out', async () => {
    const fetchImpl: typeof fetch = async () => {
      throw new Error('offline')
    }

    await assert.doesNotReject(() => forwardToolPermissionToPython('request-1', false, {
      runtimeUrl: 'http://runtime',
      fetchImpl,
    }))
  })
})

describe('Python memory count bridge', () => {
  it('reads session message count from Python /memory/count', async () => {
    const calls: Array<{ url: string; init?: RequestInit }> = []
    const fetchImpl: typeof fetch = async (input, init) => {
      calls.push({ url: String(input), init })
      return new Response(JSON.stringify({
        ok: true,
        memoryMessages: 7,
      }), { status: 200 })
    }

    const count = await fetchPythonMemoryCount('session-1', {
      runtimeUrl: 'http://127.0.0.1:8790/',
      fetchImpl,
    })

    assert.equal(calls[0].url, 'http://127.0.0.1:8790/memory/count?sessionId=session-1')
    assert.equal(calls[0].init?.method, 'GET')
    assert.equal(count, 7)
  })

  it('returns zero when Python memory count is unavailable or malformed', async () => {
    const failingFetch: typeof fetch = async () => {
      throw new Error('offline')
    }
    const malformedFetch: typeof fetch = async () => new Response(JSON.stringify({
      ok: true,
      memoryMessages: 'bad',
    }), { status: 200 })

    assert.equal(await fetchPythonMemoryCount('session-1', {
      runtimeUrl: 'http://runtime',
      fetchImpl: failingFetch,
    }), 0)
    assert.equal(await fetchPythonMemoryCount('session-1', {
      runtimeUrl: 'http://runtime',
      fetchImpl: malformedFetch,
    }), 0)
  })
})

describe('Python memory reset bridge', () => {
  it('posts session reset to Python /memory/reset', async () => {
    const calls: Array<{ url: string; init?: RequestInit }> = []
    const fetchImpl: typeof fetch = async (input, init) => {
      calls.push({ url: String(input), init })
      return new Response(JSON.stringify({
        ok: true,
        memoryMessages: 0,
      }), { status: 200 })
    }

    const result = await resetPythonMemory('session-1', {
      runtimeUrl: 'http://127.0.0.1:8790/',
      fetchImpl,
    })

    assert.equal(calls[0].url, 'http://127.0.0.1:8790/memory/reset')
    assert.equal(calls[0].init?.method, 'POST')
    assert.deepEqual(JSON.parse(String(calls[0].init?.body)), {
      sessionId: 'session-1',
    })
    assert.deepEqual(result, { ok: true, memoryMessages: 0 })
  })

  it('returns an error payload when Python memory reset fails', async () => {
    const failingFetch: typeof fetch = async () => {
      throw new Error('offline')
    }
    const rejectedFetch: typeof fetch = async () => new Response(JSON.stringify({
      ok: false,
      error: 'reset failed',
    }), { status: 500 })

    assert.deepEqual(await resetPythonMemory('session-1', {
      runtimeUrl: 'http://runtime',
      fetchImpl: failingFetch,
    }), {
      ok: false,
      error: 'offline',
    })
    assert.deepEqual(await resetPythonMemory('session-1', {
      runtimeUrl: 'http://runtime',
      fetchImpl: rejectedFetch,
    }), {
      ok: false,
      error: 'reset failed',
    })
  })
})

describe('Python runtime feedback forwarding', () => {
  it('forwards desktop feedback events to Python /runtime/feedback', async () => {
    const calls: Array<{ url: string; init?: RequestInit }> = []
    const fetchImpl: typeof fetch = async (input, init) => {
      calls.push({ url: String(input), init })
      return new Response(JSON.stringify({
        ok: true,
        events: [
          {
            id: 'python-feedback-event',
            type: 'character.behavior',
            sessionId: 'session-1',
            timestamp: '2026-06-22T00:00:00.050Z',
            payload: {
              emotion: 'neutral',
              expression: 'smile',
              motion: 'talk',
              intensity: 0.65,
            },
          },
          {
            id: 'python-lipsync-event',
            type: 'audio.lipsync-cues',
            sessionId: 'session-1',
            timestamp: '2026-06-22T00:00:00.060Z',
            payload: {
              source: 'runtime_audio',
              audioUrl: 'http://runtime/audio.wav',
              durationMs: 480,
              cues: [
                { offsetMs: 0, mouthOpen: 0.2 },
                { offsetMs: 90, mouthOpen: 0.8 },
              ],
            },
          },
        ],
      }), { status: 200 })
    }

    const events = await forwardRuntimeFeedbackToPython({
      id: 'feedback-1',
      type: 'audio.playback-started',
      sessionId: 'session-1',
      timestamp: '2026-06-22T00:00:00.000Z',
      payload: {
        source: 'runtime_audio',
        audioUrl: 'http://runtime/audio.wav',
        durationMs: 480,
      },
    }, {
      runtimeUrl: 'http://127.0.0.1:8790/',
      fetchImpl,
    })

    assert.equal(calls[0].url, 'http://127.0.0.1:8790/runtime/feedback')
    assert.equal(calls[0].init?.method, 'POST')
    assert.deepEqual(JSON.parse(String(calls[0].init?.body)), {
      sessionId: 'session-1',
      type: 'audio.playback-started',
      timestamp: '2026-06-22T00:00:00.000Z',
      payload: {
        source: 'runtime_audio',
        audioUrl: 'http://runtime/audio.wav',
        durationMs: 480,
      },
    })
    assert.equal(events.length, 2)
    assert.equal(events[0].type, 'character.behavior')
    assert.deepEqual(events[0].payload, {
      emotion: 'neutral',
      expression: 'smile',
      motion: 'talk',
      intensity: 0.65,
    })
    assert.equal(events[1].type, 'audio.lipsync-cues')
    assert.equal((events[1].payload as { durationMs?: number }).durationMs, 480)
  })

  it('swallows Python runtime feedback failures', async () => {
    const fetchImpl: typeof fetch = async () => {
      throw new Error('offline')
    }

    await assert.doesNotReject(() => forwardRuntimeFeedbackToPython({
      id: 'feedback-1',
      type: 'desktop.capabilities',
      sessionId: 'session-1',
      timestamp: '2026-06-22T00:00:00.000Z',
      payload: {
        desktop: {
          runtime: 'electron',
          protocolVersion: 1,
        },
        live2d: {
          available: false,
          expressions: [],
          motions: [],
        },
        audio: {
          runtimeAudio: true,
          speechSynthesis: true,
          voiceCount: 0,
        },
      },
    }, {
      runtimeUrl: 'http://runtime',
      fetchImpl,
    }))
  })
})

describe('Python tool list bridge', () => {
  it('reads tool permissions from Python /tools/list', async () => {
    const calls: Array<{ url: string; init?: RequestInit }> = []
    const fetchImpl: typeof fetch = async (input, init) => {
      calls.push({ url: String(input), init })
      return new Response(JSON.stringify({
        ok: true,
        tools: [
          { name: 'read_file', displayName: 'Reading local file', enabled: true, permission: 'allow' },
        ],
        schemas: [
          {
            type: 'function',
            function: {
              name: 'read_file',
              description: 'Read a file',
              parameters: { type: 'object' },
            },
          },
        ],
      }), { status: 200 })
    }

    const permissions = await fetchPythonToolPermissions({
      baseUrl: 'http://127.0.0.1:8790/',
      fetchImpl,
    })

    assert.equal(calls[0].url, 'http://127.0.0.1:8790/tools/list')
    assert.equal(calls[0].init?.method, 'GET')
    assert.deepEqual(permissions, [
      { name: 'read_file', displayName: 'Reading local file', enabled: true, permission: 'allow' },
    ])
  })

  it('returns undefined when Python tool list is unavailable or malformed', async () => {
    const failingFetch: typeof fetch = async () => {
      throw new Error('offline')
    }
    const malformedFetch: typeof fetch = async () => new Response(JSON.stringify({
      ok: true,
      tools: [{ name: 'bad' }],
      schemas: [],
    }), { status: 200 })

    assert.equal(await fetchPythonToolPermissions({
      baseUrl: 'http://runtime',
      fetchImpl: failingFetch,
    }), undefined)
    assert.equal(await fetchPythonToolPermissions({
      baseUrl: 'http://runtime',
      fetchImpl: malformedFetch,
    }), undefined)
  })
})

describe('Python memory review jobs bridge', () => {
  it('lists memory review jobs from Python', async () => {
    const calls: Array<{ url: string; init?: RequestInit }> = []
    const fetchImpl: typeof fetch = async (input, init) => {
      calls.push({ url: String(input), init })
      return new Response(JSON.stringify({
        ok: true,
        jobs: [{
          jobId: 3,
          sessionId: 'session-1',
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
      }), { status: 200 })
    }

    const payload = await listPythonMemoryReviewJobs('session-1', 'completed', {
      runtimeUrl: 'http://127.0.0.1:8790/',
      fetchImpl,
    })

    assert.equal(calls[0].url, 'http://127.0.0.1:8790/memory/review/jobs?sessionId=session-1&limit=10&status=completed')
    assert.equal(calls[0].init?.method, 'GET')
    assert.equal(payload.status, 'completed')
    assert.equal(payload.jobCount, 1)
    assert.equal(payload.jobs[0].jobId, 3)
  })

  it('returns an empty job list when Python jobs payload is unavailable or malformed', async () => {
    const failingFetch: typeof fetch = async () => {
      throw new Error('offline')
    }
    const malformedFetch: typeof fetch = async () => new Response(JSON.stringify({
      ok: true,
      jobs: [{ jobId: 'bad' }],
    }), { status: 200 })

    assert.deepEqual(await listPythonMemoryReviewJobs('session-1', 'all', {
      runtimeUrl: 'http://runtime',
      fetchImpl: failingFetch,
    }), { status: 'all', jobCount: 0, jobs: [] })
    assert.deepEqual(await listPythonMemoryReviewJobs('session-1', 'all', {
      runtimeUrl: 'http://runtime',
      fetchImpl: malformedFetch,
    }), { status: 'all', jobCount: 0, jobs: [] })
  })
})
