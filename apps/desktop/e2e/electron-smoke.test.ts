import assert from 'node:assert/strict'
import { spawn } from 'node:child_process'
import { createRequire } from 'node:module'
import type { AddressInfo } from 'node:net'
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'
import { tmpdir } from 'node:os'
import { fileURLToPath } from 'node:url'
import { describe, it } from 'node:test'
import { randomUUID } from 'node:crypto'
import { createAmadeusBridgeServer } from '../../server/src/websocket-server'
import { LocalLive2DModelLibrary } from '../../server/src/live2d'

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
    countPersistedMessages: () => 0,
    getToolPermissions: () => [],
    resetSession() {},
    resolvePendingToolPermission: () => false,
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
    countPersistedMessages: () => 0,
    getToolPermissions: () => [],
    resetSession() {},
    resolvePendingToolPermission: () => false,
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
  const fixtureRoot = mkdtempSync(join(tmpdir(), 'amadeus-electron-live2d-'))
  const modelsRoot = join(fixtureRoot, 'models')
  const freeDir = join(modelsRoot, 'hiyori-free')
  const proDir = join(modelsRoot, 'hiyori-pro')
  mkdirSync(freeDir, { recursive: true })
  mkdirSync(proDir, { recursive: true })
  writeFileSync(join(freeDir, 'hiyori-free.model3.json'), '{"Version":3}', 'utf8')
  writeFileSync(join(proDir, 'hiyori-pro.model3.json'), '{"Version":3}', 'utf8')
  writeFileSync(join(freeDir, 'manifest.yaml'), 'displayName: Hiyori Free\n', 'utf8')
  writeFileSync(join(proDir, 'manifest.yaml'), 'displayName: Hiyori Pro\n', 'utf8')

  const harnessesConfigPath = join(fixtureRoot, 'harnesses.yaml')
  writeFileSync(harnessesConfigPath, [
    'harnesses:',
    '  live2d:',
    '    enabled: true',
    '    adapter: desktop-live2d',
    '    model:',
    '      id: hiyori-free',
    '      path: hiyori-free/hiyori-free.model3.json',
  ].join('\n'), 'utf8')

  const live2dLibrary = new LocalLive2DModelLibrary(modelsRoot, 'http://127.0.0.1:0', harnessesConfigPath)
  const { httpServer, wss } = createAmadeusBridgeServer({
    model: 'e2e-model',
    defaultSessionId: 'e2e-session',
    countPersistedMessages: () => 0,
    getToolPermissions: () => [],
    resetSession() {},
    resolvePendingToolPermission: () => false,
    forwardToolPermissionToPython() {},
    live2dLibrary,
    streamChat() {},
  })

  await new Promise<void>((resolveListen) => {
    httpServer.listen(0, '127.0.0.1', resolveListen)
  })

  const address = httpServer.address() as AddressInfo
  return {
    httpUrl: `http://127.0.0.1:${address.port}`,
    wsUrl: `ws://127.0.0.1:${address.port}/ws`,
    readConfiguredModel: () => live2dLibrary.configuredModel()?.id ?? '',
    close: () => new Promise((resolveClose, rejectClose) => {
      wss.close((wssError) => {
        if (wssError) {
          rejectClose(wssError)
          return
        }

        httpServer.close((httpError) => {
          rmSync(fixtureRoot, { recursive: true, force: true })
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
  close: () => Promise<void>
}> {
  let receivedUserText: string | undefined
  const sessionId = 'e2e-session'
  const { httpServer, wss } = createAmadeusBridgeServer({
    model: 'e2e-model',
    defaultSessionId: sessionId,
    countPersistedMessages: () => 2,
    getToolPermissions: () => [{
      name: 'get_current_time',
      displayName: 'Current time',
      enabled: true,
      permission: 'allow',
    }],
    resetSession() {},
    resolvePendingToolPermission: () => false,
    forwardToolPermissionToPython() {},
    live2dLibrary: undefined,
    streamChat(socket: { send(data: string): void }, activeSessionId: string, text: string) {
      receivedUserText = text
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
