import assert from 'node:assert/strict'
import { spawn } from 'node:child_process'
import { createRequire } from 'node:module'
import type { AddressInfo } from 'node:net'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, it } from 'node:test'
import { randomUUID } from 'node:crypto'
import { createAmadeusBridgeServer } from '../../server/src/websocket-server'

const require = createRequire(import.meta.url)
const electronBinary = require('electron') as string
const desktopRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..')

describe('Electron desktop smoke', () => {
  it('starts the packaged main process and loads the renderer', async () => {
    const output = await runElectronSmoke()

    assert.equal(output.code, 0, output.stderr || output.stdout)
    assert.match(output.stdout, /AMADEUS_E2E_SMOKE renderer-ready/)
  })

  it('connects to a local runtime, submits chat, and renders streamed assistant output', async () => {
    const runtime = await startRuntimeStub()

    try {
      const output = await runElectronSmoke({
        AMADEUS_E2E_RUNTIME_UI: '1',
        AMADEUS_E2E_SKIP_LIVE2D: '1',
        AMADEUS_E2E_AGENT_HTTP_URL: runtime.httpUrl,
        AMADEUS_E2E_AGENT_WS_URL: runtime.wsUrl,
      })

      assert.equal(output.code, 0, output.stderr || output.stdout)
      assert.match(output.stdout, /AMADEUS_E2E_RUNTIME_UI/)
      assert.match(output.stdout, /E2E runtime reply/)
      assert.equal(runtime.receivedUserText(), 'e2e runtime ping')
      assert.deepEqual(runtime.receivedSkills(), [])
    }
    finally {
      await runtime.close()
    }
  })

  it('loads turn skills and sends all selected skills with the user message', async () => {
    const runtime = await startRuntimeStub()

    try {
      const output = await runElectronSmoke({
        AMADEUS_E2E_RUNTIME_UI: '1',
        AMADEUS_E2E_MULTI_SKILL_SELECT: '1',
        AMADEUS_E2E_SKIP_LIVE2D: '1',
        AMADEUS_E2E_AGENT_HTTP_URL: runtime.httpUrl,
        AMADEUS_E2E_AGENT_WS_URL: runtime.wsUrl,
      })

      assert.equal(output.code, 0, output.stderr || output.stdout)
      assert.match(output.stdout, /AMADEUS_E2E_RUNTIME_UI/)
      assert.deepEqual(runtime.receivedSkills(), [
        'development/runtime-debug',
        'development/desktop-e2e',
      ])
      assert.match(output.stdout, /development\/desktop-e2e/)
      assert.match(output.stdout, /Exercise desktop E2E workflows\./)
    }
    finally {
      await runtime.close()
    }
  })

  it('loads the configured Live2D model and switches models through the renderer controls', async () => {
    const runtime = await startLive2DRuntimeStub()

    try {
      const output = await runElectronSmoke({
        AMADEUS_E2E_LIVE2D_SWITCH: '1',
        AMADEUS_E2E_MOCK_LIVE2D: '1',
        AMADEUS_E2E_AGENT_HTTP_URL: runtime.httpUrl,
        AMADEUS_E2E_AGENT_WS_URL: runtime.wsUrl,
      })

      assert.equal(output.code, 0, output.stderr || output.stdout)
      assert.match(output.stdout, /AMADEUS_E2E_LIVE2D_SWITCH/)
      assert.match(output.stdout, /hiyori-pro/)
      assert.equal(runtime.readConfiguredModel(), 'hiyori-pro')
    }
    finally {
      await runtime.close()
    }
  })

  it('plays runtime audio and reports playback feedback to the bridge', async () => {
    const runtime = await startAudioFeedbackRuntimeStub()

    try {
      const output = await runElectronSmoke({
        AMADEUS_E2E_AUDIO_FEEDBACK: '1',
        AMADEUS_E2E_SKIP_LIVE2D: '1',
        AMADEUS_E2E_MOCK_AUDIO: 'ended',
        AMADEUS_E2E_AGENT_HTTP_URL: runtime.httpUrl,
        AMADEUS_E2E_AGENT_WS_URL: runtime.wsUrl,
      })

      assert.equal(output.code, 0, output.stderr || output.stdout)
      assert.match(output.stdout, /AMADEUS_E2E_AUDIO_FEEDBACK/)
      assert.deepEqual(runtime.audioFeedbackTypes(), [
        'audio.playback-started',
        'audio.playback-ended',
      ])
    }
    finally {
      await runtime.close()
    }
  })

  it('reports runtime audio playback errors to the bridge', async () => {
    const runtime = await startAudioFeedbackRuntimeStub()

    try {
      const output = await runElectronSmoke({
        AMADEUS_E2E_AUDIO_FEEDBACK: '1',
        AMADEUS_E2E_EXPECT_AUDIO_ERROR: '1',
        AMADEUS_E2E_SKIP_LIVE2D: '1',
        AMADEUS_E2E_MOCK_AUDIO: 'error',
        AMADEUS_E2E_AGENT_HTTP_URL: runtime.httpUrl,
        AMADEUS_E2E_AGENT_WS_URL: runtime.wsUrl,
      })

      assert.equal(output.code, 0, output.stderr || output.stdout)
      assert.match(output.stdout, /AMADEUS_E2E_AUDIO_FEEDBACK/)
      assert.deepEqual(runtime.audioFeedbackTypes(), [
        'audio.playback-started',
        'audio.playback-error',
      ])
    }
    finally {
      await runtime.close()
    }
  })

  it('shows the tool permission prompt and reports Allow to the bridge', async () => {
    const runtime = await startPermissionPromptRuntimeStub()

    try {
      const output = await runElectronSmoke({
        AMADEUS_E2E_PERMISSION_PROMPT: '1',
        AMADEUS_E2E_EXPECT_PERMISSION_ALLOW: '1',
        AMADEUS_E2E_SKIP_LIVE2D: '1',
        AMADEUS_E2E_AGENT_HTTP_URL: runtime.httpUrl,
        AMADEUS_E2E_AGENT_WS_URL: runtime.wsUrl,
      })

      assert.equal(output.code, 0, output.stderr || output.stdout)
      assert.match(output.stdout, /AMADEUS_E2E_PERMISSION_PROMPT/)
      assert.deepEqual(runtime.permissionResponses(), [{
        requestId: 'e2e-permission-request',
        approved: true,
      }])
    }
    finally {
      await runtime.close()
    }
  })

  it('shows the tool permission prompt and reports Deny to the bridge', async () => {
    const runtime = await startPermissionPromptRuntimeStub()

    try {
      const output = await runElectronSmoke({
        AMADEUS_E2E_PERMISSION_PROMPT: '1',
        AMADEUS_E2E_SKIP_LIVE2D: '1',
        AMADEUS_E2E_AGENT_HTTP_URL: runtime.httpUrl,
        AMADEUS_E2E_AGENT_WS_URL: runtime.wsUrl,
      })

      assert.equal(output.code, 0, output.stderr || output.stdout)
      assert.match(output.stdout, /AMADEUS_E2E_PERMISSION_PROMPT/)
      assert.deepEqual(runtime.permissionResponses(), [{
        requestId: 'e2e-permission-request',
        approved: false,
      }])
    }
    finally {
      await runtime.close()
    }
  })
})

function runElectronSmoke(env: Record<string, string> = {}): Promise<{ code: number | null, stdout: string, stderr: string }> {
  return new Promise((resolvePromise, reject) => {
    const child = spawn(electronBinary, ['--no-sandbox', '.'], {
      cwd: desktopRoot,
      env: {
        ...process.env,
        AMADEUS_E2E_SMOKE: env.AMADEUS_E2E_RUNTIME_UI === '1'
          || env.AMADEUS_E2E_LIVE2D_SWITCH === '1'
          || env.AMADEUS_E2E_AUDIO_FEEDBACK === '1'
          || env.AMADEUS_E2E_PERMISSION_PROMPT === '1'
          || env.AMADEUS_E2E_MULTI_SKILL_SELECT === '1'
          ? '0'
          : '1',
        ELECTRON_ENABLE_LOGGING: '1',
        ...env,
      },
      stdio: ['ignore', 'pipe', 'pipe'],
    })

    let stdout = ''
    let stderr = ''
    const timeout = setTimeout(() => {
      child.kill('SIGTERM')
      reject(new Error(`Electron smoke timed out\nstdout:\n${stdout}\nstderr:\n${stderr}`))
    }, 20000)

    child.stdout.setEncoding('utf8')
    child.stderr.setEncoding('utf8')
    child.stdout.on('data', (chunk: string) => {
      stdout += chunk
    })
    child.stderr.on('data', (chunk: string) => {
      stderr += chunk
    })
    child.on('error', (error) => {
      clearTimeout(timeout)
      reject(error)
    })
    child.on('close', (code) => {
      clearTimeout(timeout)
      resolvePromise({ code, stdout, stderr })
    })
  })
}

async function startPermissionPromptRuntimeStub(): Promise<{
  httpUrl: string
  wsUrl: string
  permissionResponses: () => Array<{ requestId: string, approved: boolean }>
  close: () => Promise<void>
}> {
  const responses: Array<{ requestId: string, approved: boolean }> = []
  const sessionId = 'e2e-session'
  const { httpServer, wss } = createAmadeusBridgeServer({
    model: 'e2e-model',
    defaultSessionId: sessionId,
    getMemoryMessageCount: () => 0,
    getToolPermissions: () => [],
    resetSession() {},
    forwardToolPermissionToPython(requestId, approved) {
      responses.push({ requestId, approved })
    },
    streamChat(socket: { send(data: string): void }, activeSessionId: string) {
      sendRuntimeEvent(socket, 'tool.permission.request', activeSessionId, {
        requestId: 'e2e-permission-request',
        toolName: 'patch',
        displayName: 'Editing local file',
        reason: 'Allow editing README.md?',
      })
    },
  })

  await new Promise<void>((resolveListen) => {
    httpServer.listen(0, '127.0.0.1', resolveListen)
  })

  const address = httpServer.address() as AddressInfo
  return {
    httpUrl: `http://127.0.0.1:${address.port}`,
    wsUrl: `ws://127.0.0.1:${address.port}/ws`,
    permissionResponses: () => [...responses],
    close: () => new Promise((resolveClose, rejectClose) => {
      wss.close((wssError) => {
        if (wssError) {
          rejectClose(wssError)
          return
        }

        httpServer.close((httpError) => {
          if (httpError) {
            rejectClose(httpError)
            return
          }
          resolveClose()
        })
      })
    }),
  }
}

async function startAudioFeedbackRuntimeStub(): Promise<{
  httpUrl: string
  wsUrl: string
  audioFeedbackTypes: () => string[]
  close: () => Promise<void>
}> {
  const audioFeedback: string[] = []
  const sessionId = 'e2e-session'
  const { httpServer, wss } = createAmadeusBridgeServer({
    model: 'e2e-model',
    defaultSessionId: sessionId,
    getMemoryMessageCount: () => 0,
    getToolPermissions: () => [],
    resetSession() {},
    forwardToolPermissionToPython() {},
    observeDesktopFeedback(event) {
      if (event.type === 'audio.playback-started' || event.type === 'audio.playback-ended' || event.type === 'audio.playback-error') {
        audioFeedback.push(event.type)
      }
      return []
    },
    streamChat(socket: { send(data: string): void }, activeSessionId: string) {
      sendRuntimeEvent(socket, 'assistant.message', activeSessionId, { text: 'E2E audio reply' })
      sendRuntimeEvent(socket, 'audio.tts-ready', activeSessionId, {
        audioUrl: 'http://127.0.0.1/e2e-audio.wav',
        mimeType: 'audio/wav',
        provider: 'e2e',
      })
    },
  })

  await new Promise<void>((resolveListen) => {
    httpServer.listen(0, '127.0.0.1', resolveListen)
  })

  const address = httpServer.address() as AddressInfo
  return {
    httpUrl: `http://127.0.0.1:${address.port}`,
    wsUrl: `ws://127.0.0.1:${address.port}/ws`,
    audioFeedbackTypes: () => [...audioFeedback],
    close: () => new Promise((resolveClose, rejectClose) => {
      wss.close((wssError) => {
        if (wssError) {
          rejectClose(wssError)
          return
        }

        httpServer.close((httpError) => {
          if (httpError) {
            rejectClose(httpError)
            return
          }
          resolveClose()
        })
      })
    }),
  }
}

async function startLive2DRuntimeStub(): Promise<{
  httpUrl: string
  wsUrl: string
  readConfiguredModel: () => string
  close: () => Promise<void>
}> {
  const models = {
    'hiyori-free': {
      id: 'hiyori-free',
      path: 'hiyori-free/hiyori-free.model3.json',
      manifest: { displayName: 'Hiyori Free' },
      body: '{"Version":3}',
    },
    'hiyori-pro': {
      id: 'hiyori-pro',
      path: 'hiyori-pro/hiyori-pro.model3.json',
      manifest: { displayName: 'Hiyori Pro' },
      body: '{"Version":3}',
    },
  } as const
  let configuredModelId: keyof typeof models = 'hiyori-free'
  let httpUrl = 'http://127.0.0.1:0'
  const { httpServer, wss } = createAmadeusBridgeServer({
    model: 'e2e-model',
    defaultSessionId: 'e2e-session',
    getMemoryMessageCount: () => 0,
    getToolPermissions: () => [],
    resetSession() {},
    forwardToolPermissionToPython() {},
    async handleLive2DHttpRequest(request, response, requestUrl) {
      const writeJson = (status: number, payload: Record<string, unknown>) => {
        response.writeHead(status, {
          'Content-Type': 'application/json; charset=utf-8',
          'Access-Control-Allow-Origin': '*',
        })
        response.end(JSON.stringify(payload))
      }
      const toModelPayload = (modelId: keyof typeof models) => ({
        ...models[modelId],
        url: `${httpUrl}/live2d/models/${models[modelId].path}`,
      })

      if (request.method === 'GET' && requestUrl === '/live2d/config') {
        writeJson(200, { ok: true, model: toModelPayload(configuredModelId) })
        return
      }

      if (request.method === 'GET' && requestUrl === '/live2d/models') {
        writeJson(200, {
          ok: true,
          models: Object.keys(models).map((modelId) => ({
            ...toModelPayload(modelId as keyof typeof models),
            active: modelId === configuredModelId,
          })),
          activeModel: toModelPayload(configuredModelId),
        })
        return
      }

      if (request.method === 'POST' && requestUrl === '/live2d/select') {
        let body = ''
        for await (const chunk of request) {
          body += String(chunk)
        }
        const payload = JSON.parse(body || '{}') as { modelId?: string }
        if (!payload.modelId || !(payload.modelId in models)) {
          writeJson(400, { ok: false, error: 'live2d_model_not_found' })
          return
        }
        configuredModelId = payload.modelId as keyof typeof models
        writeJson(200, { ok: true, model: toModelPayload(configuredModelId) })
        return
      }

      if (request.method === 'GET' && requestUrl.startsWith('/live2d/models/')) {
        const matched = Object.values(models).find((model) => requestUrl === `/live2d/models/${model.path}`)
        if (!matched) {
          writeJson(404, { ok: false, error: 'not_found' })
          return
        }
        response.writeHead(200, {
          'Content-Type': 'application/json; charset=utf-8',
          'Access-Control-Allow-Origin': '*',
        })
        response.end(matched.body)
        return
      }

      writeJson(404, { ok: false, error: 'not_found' })
    },
    streamChat() {},
  })

  await new Promise<void>((resolveListen) => {
    httpServer.listen(0, '127.0.0.1', resolveListen)
  })

  const address = httpServer.address() as AddressInfo
  httpUrl = `http://127.0.0.1:${address.port}`
  return {
    httpUrl,
    wsUrl: `ws://127.0.0.1:${address.port}/ws`,
    readConfiguredModel: () => configuredModelId,
    close: () => new Promise((resolveClose, rejectClose) => {
      wss.close((wssError) => {
        if (wssError) {
          rejectClose(wssError)
          return
        }

        httpServer.close((httpError) => {
          if (httpError) {
            rejectClose(httpError)
            return
          }
          resolveClose()
        })
      })
    }),
  }
}

async function startRuntimeStub(): Promise<{
  httpUrl: string
  wsUrl: string
  receivedUserText: () => string | undefined
  receivedSkills: () => string[]
  close: () => Promise<void>
}> {
  let receivedUserText: string | undefined
  let receivedSkills: string[] = []
  const sessionId = 'e2e-session'
  const { httpServer, wss } = createAmadeusBridgeServer({
    model: 'e2e-model',
    defaultSessionId: sessionId,
    getMemoryMessageCount: () => 2,
    getToolPermissions: () => [{
      name: 'get_current_time',
      displayName: 'Current time',
      enabled: true,
      permission: 'allow',
    }],
    resetSession() {},
    forwardToolPermissionToPython() {},
    handleSkillsHttpRequest(_request, response, requestUrl) {
      if (requestUrl === '/skills/list') {
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

      if (requestUrl === '/skills/view?name=development%2Fruntime-debug') {
        response.writeHead(200, { 'Content-Type': 'application/json; charset=utf-8' })
        response.end(JSON.stringify({
          ok: true,
          skill: {
            name: 'runtime-debug',
            identifier: 'development/runtime-debug',
            description: 'Debug runtime behavior.',
            instructions: 'Inspect runtime logs.\nCollect evidence narrowly.\nPatch the smallest surface.',
            preferredTools: ['read_file', 'search_files'],
            allowedTools: ['read_file', 'search_files', 'patch'],
            resourceDirs: ['scripts'],
            platforms: ['macos'],
          },
        }))
        return
      }

      if (requestUrl === '/skills/view?name=development%2Fdesktop-e2e') {
        response.writeHead(200, { 'Content-Type': 'application/json; charset=utf-8' })
        response.end(JSON.stringify({
          ok: true,
          skill: {
            name: 'desktop-e2e',
            identifier: 'development/desktop-e2e',
            description: 'Exercise desktop E2E workflows.',
            instructions: 'Use packaged Electron paths.',
            preferredTools: ['read_file'],
            allowedTools: ['read_file'],
            resourceDirs: ['references'],
            platforms: ['macos'],
          },
        }))
        return
      }

      response.writeHead(404, { 'Content-Type': 'application/json; charset=utf-8' })
      response.end(JSON.stringify({ ok: false, error: 'not_found' }))
    },
    streamChat(socket: { send(data: string): void }, activeSessionId: string, text: string, skills?: string[]) {
      receivedUserText = text
      receivedSkills = [...(skills ?? [])]
      sendRuntimeEvent(socket, 'assistant.state', activeSessionId, { state: 'thinking' })
      sendRuntimeEvent(socket, 'assistant.delta', activeSessionId, { text: 'E2E runtime ' })
      sendRuntimeEvent(socket, 'assistant.delta', activeSessionId, { text: 'reply' })
      sendRuntimeEvent(socket, 'assistant.message', activeSessionId, { text: 'E2E runtime reply' })
      sendRuntimeEvent(socket, 'assistant.state', activeSessionId, { state: 'idle' })
    },
  })

  await new Promise<void>((resolveListen) => {
    httpServer.listen(0, '127.0.0.1', resolveListen)
  })

  const address = httpServer.address() as AddressInfo
  return {
    httpUrl: `http://127.0.0.1:${address.port}`,
    wsUrl: `ws://127.0.0.1:${address.port}/ws`,
    receivedUserText: () => receivedUserText,
    receivedSkills: () => [...receivedSkills],
    close: () => new Promise((resolveClose, rejectClose) => {
      wss.close((wssError) => {
        if (wssError) {
          rejectClose(wssError)
          return
        }

        httpServer.close((httpError) => {
          if (httpError) {
            rejectClose(httpError)
            return
          }
          resolveClose()
        })
      })
    }),
  }
}

function sendRuntimeEvent<TPayload>(
  socket: { send(data: string): void },
  type: string,
  sessionId: string,
  payload: TPayload,
): void {
  socket.send(JSON.stringify({
    id: randomUUID(),
    type,
    sessionId,
    timestamp: new Date().toISOString(),
    payload,
  }))
}
