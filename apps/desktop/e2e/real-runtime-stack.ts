import { spawn, type ChildProcess } from 'node:child_process'
import { createServer, type Server } from 'node:http'
import type { AddressInfo } from 'node:net'
import { createServer as createNetServer } from 'node:net'
import { mkdtemp, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'

interface ManagedChild {
  name: string
  process: ChildProcess
  output: () => string
}

export interface RealRuntimeStack {
  runtimeUrl: string
  bridgeUrl: string
  wsUrl: string
  databasePath: string
  secondarySessionId: string
  reviewTaskId: string
  reviewTaskTitle: string
  providerRequestCount: () => number
  close: () => Promise<void>
}

const repoRoot = resolve(import.meta.dirname, '../../..')
const reviewTaskTitle = 'Real runtime review task'

export async function startRealRuntimeStack(): Promise<RealRuntimeStack> {
  const stateRoot = await mkdtemp(join(tmpdir(), 'amadeus-real-e2e-'))
  const databasePath = join(stateRoot, 'amadeus.sqlite')
  const children: ManagedChild[] = []
  let providerServer: Server | null = null

  try {
    const provider = await startModelFixture()
    providerServer = provider.server
    const runtimePort = await reservePort()
    const bridgePort = await reservePort()
    const runtimeUrl = `http://127.0.0.1:${runtimePort}`
    const bridgeUrl = `http://127.0.0.1:${bridgePort}`

    const runtime = startChild('python-runtime', process.env.AMADEUS_E2E_PYTHON || 'python', [
      'packages/amadeus/server.py',
    ], {
      AMADEUS_PYTHON_RUNTIME_HOST: '127.0.0.1',
      AMADEUS_PYTHON_RUNTIME_PORT: String(runtimePort),
      AMADEUS_PYTHON_RUNTIME_URL: runtimeUrl,
      AMADEUS_MEMORY_DB: databasePath,
      AMADEUS_AUDIO_ROOT: join(stateRoot, 'audio'),
      AMADEUS_TASK_RUNNER: 'in_process',
      AMADEUS_TASK_WORKSPACE_ISOLATION: 'none',
      AMADEUS_TASK_RECOVERY_INTERVAL_SECONDS: '1',
      AMADEUS_LLM_PROVIDER: 'custom',
      AMADEUS_CUSTOM_BASE_URL: `${provider.baseUrl}/v1`,
      AMADEUS_CUSTOM_MODEL: 'amadeus-e2e-model',
      AMADEUS_CUSTOM_API_KEY: 'e2e-local',
      AMADEUS_MEMORY_VECTOR_RETRIEVAL: 'false',
      AMADEUS_MEMORY_GLOBAL_RETRIEVAL_FALLBACK: 'false',
      AMADEUS_LOG_LEVEL: 'WARNING',
    })
    children.push(runtime)
    await waitForHealth(`${runtimeUrl}/runtime/health`, runtime)

    const bridge = startChild('node-bridge', process.execPath, [
      '--import',
      'tsx',
      'apps/server/src/index.ts',
    ], {
      AMADEUS_SERVER_HOST: '127.0.0.1',
      AMADEUS_SERVER_PORT: String(bridgePort),
      AMADEUS_SERVER_URL: bridgeUrl,
      AMADEUS_PYTHON_RUNTIME_URL: runtimeUrl,
    })
    children.push(bridge)
    await waitForHealth(`${bridgeUrl}/health`, bridge)

    const roles = await getJson<{ roles: Array<{ id: string }> }>(`${runtimeUrl}/roles`)
    const roleId = roles.roles[0]?.id
    if (!roleId) {
      throw new Error('Real runtime E2E could not find the default role')
    }
    const secondary = await postJson<{ session: { id: string } }>(`${runtimeUrl}/sessions`, {
      roleId,
      title: 'E2E secondary session',
    })
    const task = await postJson<{ task: { id: string } }>(`${runtimeUrl}/tasks`, {
      sessionId: 'companion:default',
      title: reviewTaskTitle,
      body: 'Produce a deterministic review-gated result.',
      source: 'api',
      reviewRequired: true,
      maxAttempts: 1,
    })
    await waitForTaskStatus(runtimeUrl, task.task.id, 'blocked')

    return {
      runtimeUrl,
      bridgeUrl,
      wsUrl: `ws://127.0.0.1:${bridgePort}/ws`,
      databasePath,
      secondarySessionId: secondary.session.id,
      reviewTaskId: task.task.id,
      reviewTaskTitle,
      providerRequestCount: provider.requestCount,
      close: async () => {
        await stopChildren(children)
        await closeServer(provider.server)
        await rm(stateRoot, { recursive: true, force: true })
      },
    }
  }
  catch (error) {
    await stopChildren(children)
    if (providerServer) {
      await closeServer(providerServer)
    }
    await rm(stateRoot, { recursive: true, force: true })
    throw error
  }
}

async function startModelFixture(): Promise<{
  server: Server
  baseUrl: string
  requestCount: () => number
}> {
  let requests = 0
  const server = createServer(async (request, response) => {
    if (request.method !== 'POST' || request.url !== '/v1/chat/completions') {
      response.writeHead(404, { 'Content-Type': 'application/json' })
      response.end(JSON.stringify({ error: 'not_found' }))
      return
    }
    requests += 1
    await readBody(request)
    response.writeHead(200, { 'Content-Type': 'application/json' })
    response.end(JSON.stringify({
      id: `e2e-completion-${requests}`,
      object: 'chat.completion',
      created: Math.floor(Date.now() / 1000),
      model: 'amadeus-e2e-model',
      choices: [{
        index: 0,
        finish_reason: 'stop',
        message: {
          role: 'assistant',
          content: 'Real runtime E2E reply',
          tool_calls: [],
        },
      }],
    }))
  })
  await listen(server)
  const address = server.address() as AddressInfo
  return {
    server,
    baseUrl: `http://127.0.0.1:${address.port}`,
    requestCount: () => requests,
  }
}

function startChild(
  name: string,
  command: string,
  args: string[],
  env: Record<string, string>,
): ManagedChild {
  const child = spawn(command, args, {
    cwd: repoRoot,
    env: {
      ...process.env,
      ...env,
    },
    stdio: ['ignore', 'pipe', 'pipe'],
  })
  let output = ''
  child.stdout?.setEncoding('utf8')
  child.stderr?.setEncoding('utf8')
  child.stdout?.on('data', (chunk: string) => {
    output += chunk
  })
  child.stderr?.on('data', (chunk: string) => {
    output += chunk
  })
  return {
    name,
    process: child,
    output: () => output,
  }
}

async function waitForHealth(url: string, child: ManagedChild, timeoutMs = 20000): Promise<void> {
  const deadline = Date.now() + timeoutMs
  while (Date.now() < deadline) {
    if (child.process.exitCode !== null) {
      throw new Error(`${child.name} exited with ${child.process.exitCode}\n${child.output()}`)
    }
    try {
      const response = await fetch(url)
      if (response.ok) {
        return
      }
    }
    catch {
      // Process is still starting.
    }
    await delay(100)
  }
  throw new Error(`${child.name} did not become healthy at ${url}\n${child.output()}`)
}

async function waitForTaskStatus(
  runtimeUrl: string,
  taskId: string,
  status: string,
  timeoutMs = 20000,
): Promise<void> {
  const deadline = Date.now() + timeoutMs
  while (Date.now() < deadline) {
    const payload = await getJson<{ tasks: Array<{ id: string; status: string }> }>(
      `${runtimeUrl}/tasks?sessionId=companion%3Adefault&activeOnly=false&limit=20`,
    )
    const task = payload.tasks.find((candidate) => candidate.id === taskId)
    if (task?.status === status) {
      return
    }
    if (task && ['failed', 'cancelled', 'succeeded'].includes(task.status)) {
      throw new Error(`Review task reached unexpected terminal status ${task.status}`)
    }
    await delay(100)
  }
  throw new Error(`Timed out waiting for task ${taskId} to reach ${status}`)
}

async function getJson<T>(url: string): Promise<T> {
  const response = await fetch(url)
  if (!response.ok) {
    throw new Error(`GET ${url} failed with ${response.status}: ${await response.text()}`)
  }
  return await response.json() as T
}

async function postJson<T>(url: string, body: Record<string, unknown>): Promise<T> {
  const response = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!response.ok) {
    throw new Error(`POST ${url} failed with ${response.status}: ${await response.text()}`)
  }
  return await response.json() as T
}

async function reservePort(): Promise<number> {
  const server = createNetServer()
  await new Promise<void>((resolveListen, rejectListen) => {
    server.once('error', rejectListen)
    server.listen(0, '127.0.0.1', resolveListen)
  })
  const address = server.address() as AddressInfo
  await new Promise<void>((resolveClose, rejectClose) => {
    server.close((error) => error ? rejectClose(error) : resolveClose())
  })
  return address.port
}

function listen(server: Server): Promise<void> {
  return new Promise((resolveListen, rejectListen) => {
    server.once('error', rejectListen)
    server.listen(0, '127.0.0.1', resolveListen)
  })
}

function readBody(request: import('node:http').IncomingMessage): Promise<string> {
  return new Promise((resolveBody, rejectBody) => {
    let body = ''
    request.setEncoding('utf8')
    request.on('data', (chunk: string) => {
      body += chunk
    })
    request.on('end', () => resolveBody(body))
    request.on('error', rejectBody)
  })
}

async function stopChildren(children: ManagedChild[]): Promise<void> {
  for (const child of [...children].reverse()) {
    await stopChild(child)
  }
}

async function stopChild(child: ManagedChild): Promise<void> {
  if (child.process.exitCode !== null) {
    return
  }
  child.process.kill('SIGTERM')
  const closed = await Promise.race([
    new Promise<boolean>((resolveClose) => {
      child.process.once('close', () => resolveClose(true))
    }),
    delay(5000).then(() => false),
  ])
  if (!closed && child.process.exitCode === null) {
    child.process.kill('SIGKILL')
    await new Promise<void>((resolveClose) => {
      child.process.once('close', () => resolveClose())
    })
  }
}

function closeServer(server: Server): Promise<void> {
  return new Promise((resolveClose, rejectClose) => {
    server.close((error) => error ? rejectClose(error) : resolveClose())
  })
}

function delay(ms: number): Promise<void> {
  return new Promise((resolveDelay) => setTimeout(resolveDelay, ms))
}
