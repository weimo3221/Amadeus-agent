import type {
  AssistantState,
  ClientRuntimeEvent,
  RuntimeEvent,
  ServerRuntimeEvent,
} from '@amadeus-agent/amadeus/events'
import {
  applyToolConfig,
  createDefaultToolRegistry,
  getEnabledToolSchemas,
  getToolPermissionState,
  type RegisteredTool,
  type ToolCall,
} from '@amadeus-agent/amadeus/tools'

import { createServer } from 'node:http'
import { randomUUID } from 'node:crypto'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { existsSync, mkdirSync } from 'node:fs'
import { DatabaseSync } from 'node:sqlite'

import { config } from 'dotenv'
import { WebSocketServer, type WebSocket } from 'ws'

const serverDir = dirname(fileURLToPath(import.meta.url))
const rootDir = resolve(serverDir, '../../..')

config({ path: resolve(rootDir, '.env') })

const host = process.env.AMADEUS_SERVER_HOST || '127.0.0.1'
const port = Number(process.env.AMADEUS_SERVER_PORT || 8788)
const baseUrl = process.env.OPENAI_BASE_URL || 'https://api.deepseek.com'
const apiKey = process.env.OPENAI_API_KEY || ''
const model = process.env.OPENAI_MODEL || 'deepseek-v4-flash'
const pythonRuntimeUrl = process.env.AMADEUS_PYTHON_RUNTIME_URL || process.env.AMADEUS_PYTHON_TOOLS_URL || 'http://127.0.0.1:8790'
const defaultSessionId = 'default'
const dataDir = resolve(rootDir, 'data')
const databasePath = resolve(dataDir, 'amadeus.sqlite')
const toolsConfigPath = resolve(rootDir, 'configs/tools.yaml')

interface ChatMessage {
  role: 'system' | 'user' | 'assistant' | 'tool'
  content: string
  tool_call_id?: string
  tool_calls?: ToolCall[]
}

interface ChatChoiceMessage {
  role?: 'assistant'
  content?: string | null
  tool_calls?: ToolCall[]
}

interface ChatCompletionResponse {
  choices?: Array<{
    message?: ChatChoiceMessage
    delta?: {
      content?: string
    }
  }>
}

interface AudioSpeakResponse {
  ok?: boolean
  audioUrl?: string | null
  durationMs?: number | null
}

const sessions = new Map<string, ChatMessage[]>()
const pendingToolPermissions = new Map<string, (approved: boolean) => void>()

const systemPrompt: ChatMessage = {
  role: 'system',
  content: [
    'You are Amadeus, a desktop Live2D companion agent.',
    'Reply in the same language as the user unless they ask otherwise.',
    'Be concise, practical, and calm.',
    'You can use safe local tools for current time, dice rolls, and searching project files.',
    'When the user asks for the current time, current date, today, now, or scheduling context, you must call get_current_time before answering.',
    'When the user asks to roll dice or generate a dice result, call roll_dice.',
    'When the user asks to find local project files, docs, code, configuration, or notes, call local_file_search.',
    'Do not answer current time or date questions from memory or estimation.',
  ].join('\n'),
}

const toolRegistry = createDefaultToolRegistry({
  pythonBackend: pythonRuntimeUrl ? { baseUrl: pythonRuntimeUrl } : undefined,
})
applyToolConfig(toolRegistry, toolsConfigPath)
const tools = getEnabledToolSchemas(toolRegistry)

if (!existsSync(dataDir)) {
  mkdirSync(dataDir, { recursive: true })
}

const db = new DatabaseSync(databasePath)
db.exec(`
CREATE TABLE IF NOT EXISTS messages (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id TEXT NOT NULL,
  role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
  content TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_session_created
ON messages(session_id, created_at);
`)

const insertMessage = db.prepare(`
INSERT INTO messages (session_id, role, content, created_at)
VALUES (?, ?, ?, ?)
`)

const selectMessages = db.prepare(`
SELECT role, content
FROM messages
WHERE session_id = ?
ORDER BY id DESC
LIMIT ?
`)

const deleteMessages = db.prepare(`
DELETE FROM messages
WHERE session_id = ?
`)

const countMessages = db.prepare(`
SELECT COUNT(*) as count
FROM messages
WHERE session_id = ?
`)

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

function sendMemoryUpdated(socket: WebSocket, sessionId: string): void {
  send(socket, 'memory.updated', sessionId, {
    memoryMessages: countPersistedMessages(sessionId),
  })
}

function saveMessage(sessionId: string, role: 'user' | 'assistant', content: string): void {
  insertMessage.run(sessionId, role, content, new Date().toISOString())
}

function loadMessages(sessionId: string, limit = 40): ChatMessage[] {
  const rows = selectMessages.all(sessionId, limit) as Array<{ role: 'user' | 'assistant'; content: string }>
  return rows.reverse().map((row) => ({
    role: row.role,
    content: row.content,
  }))
}

function countPersistedMessages(sessionId: string): number {
  const row = countMessages.get(sessionId) as { count: number } | undefined
  return row?.count ?? 0
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

function getHistory(sessionId: string): ChatMessage[] {
  const existing = sessions.get(sessionId)
  if (existing) {
    return existing
  }

  const created: ChatMessage[] = [systemPrompt, ...loadMessages(sessionId)]
  sessions.set(sessionId, created)
  return created
}
function parseToolArguments(raw: string): Record<string, unknown> {
  try {
    const parsed = JSON.parse(raw) as Record<string, unknown>
    return parsed && typeof parsed === 'object' ? parsed : {}
  }
  catch {
    return {}
  }
}

async function requestToolPermission(
  socket: WebSocket,
  sessionId: string,
  tool: RegisteredTool,
  args: Record<string, unknown>,
): Promise<boolean> {
  if (tool.permission === 'allow') {
    return true
  }

  if (tool.permission === 'deny') {
    return false
  }

  const requestId = randomUUID()
  const reason = tool.describeRequest?.(args) ?? `Allow Amadeus to run ${tool.displayName}?`

  send(socket, 'tool.permission.request', sessionId, {
    requestId,
    toolName: tool.name,
    displayName: tool.displayName,
    reason,
  })

  return new Promise((resolve) => {
    const timeout = setTimeout(() => {
      pendingToolPermissions.delete(requestId)
      resolve(false)
    }, 30000)

    pendingToolPermissions.set(requestId, (approved) => {
      clearTimeout(timeout)
      pendingToolPermissions.delete(requestId)
      resolve(approved)
    })
  })
}

async function executeToolCall(socket: WebSocket, sessionId: string, toolCall: ToolCall): Promise<ChatMessage> {
  const toolName = toolCall.function.name
  const tool = toolRegistry[toolName]
  sendState(socket, sessionId, 'tool-running')
  send(socket, 'tool.started', sessionId, {
    toolName,
    displayName: tool?.displayName ?? `Running ${toolName}`,
  })

  if (tool) {
    const args = parseToolArguments(toolCall.function.arguments)
    if (!tool.enabled) {
      send(socket, 'tool.finished', sessionId, {
        toolName,
        ok: false,
      })
      return {
        role: 'tool',
        tool_call_id: toolCall.id,
        content: JSON.stringify({ error: `Tool is disabled: ${toolName}` }),
      }
    }

    const approved = await requestToolPermission(socket, sessionId, tool, args)
    if (!approved) {
      send(socket, 'tool.finished', sessionId, {
        toolName,
        ok: false,
      })
      return {
        role: 'tool',
        tool_call_id: toolCall.id,
        content: JSON.stringify({ error: `Permission denied for tool: ${toolName}` }),
      }
    }

    const result = await tool.execute(args, { sessionId })
    send(socket, 'tool.finished', sessionId, {
      toolName,
      ok: true,
    })
    return {
      role: 'tool',
      tool_call_id: toolCall.id,
      content: result,
    }
  }

  send(socket, 'tool.finished', sessionId, {
    toolName,
    ok: false,
  })
  return {
    role: 'tool',
    tool_call_id: toolCall.id,
    content: JSON.stringify({ error: `Unknown tool: ${toolName}` }),
  }
}

async function requestToolDecision(messages: ChatMessage[]): Promise<ChatChoiceMessage> {
  const response = await fetch(`${baseUrl.replace(/\/$/, '')}/chat/completions`, {
    method: 'POST',
    headers: {
      Authorization: `Bearer ${apiKey}`,
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({
      model,
      messages,
      tools,
      tool_choice: 'auto',
      stream: false,
      temperature: 0,
    }),
  })

  if (!response.ok) {
    const body = await response.text().catch(() => '')
    throw new Error(`Provider returned ${response.status}: ${body || response.statusText}`)
  }

  const data = await response.json() as ChatCompletionResponse
  return data.choices?.[0]?.message ?? { role: 'assistant', content: '' }
}

async function requestAudioOutput(text: string): Promise<AudioSpeakResponse | undefined> {
  const normalizedText = text.trim()
  if (!normalizedText) {
    return undefined
  }

  try {
    const response = await fetch(`${pythonRuntimeUrl.replace(/\/$/, '')}/audio/speak`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        text: normalizedText,
        format: 'wav',
      }),
    })

    if (!response.ok) {
      return undefined
    }

    return await response.json() as AudioSpeakResponse
  }
  catch {
    return undefined
  }
}

async function streamChat(socket: WebSocket, sessionId: string, userText: string): Promise<void> {
  if (!apiKey) {
    send(socket, 'error', sessionId, {
      code: 'missing_api_key',
      message: 'OPENAI_API_KEY is not configured.',
    })
    return
  }

  const history = getHistory(sessionId)
  history.push({ role: 'user', content: userText })
  saveMessage(sessionId, 'user', userText)
  sendMemoryUpdated(socket, sessionId)

  sendState(socket, sessionId, 'thinking')
  send(socket, 'character.behavior', sessionId, {
    emotion: 'focused',
    expression: 'serious',
    motion: 'think',
    intensity: 0.6,
  })

  const toolDecision = await requestToolDecision(history)
  const toolCalls = toolDecision.tool_calls ?? []

  if (toolCalls.length > 0) {
    history.push({
      role: 'assistant',
      content: toolDecision.content ?? '',
      tool_calls: toolCalls,
    })

    for (const toolCall of toolCalls) {
      history.push(await executeToolCall(socket, sessionId, toolCall))
    }
  }

  const response = await fetch(`${baseUrl.replace(/\/$/, '')}/chat/completions`, {
    method: 'POST',
    headers: {
      Authorization: `Bearer ${apiKey}`,
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({
      model,
      messages: history,
      stream: true,
      temperature: 0.7,
    }),
  })

  if (!response.ok || !response.body) {
    const body = await response.text().catch(() => '')
    sendState(socket, sessionId, 'error')
    send(socket, 'error', sessionId, {
      code: 'provider_error',
      message: `Provider returned ${response.status}: ${body || response.statusText}`,
    })
    return
  }

  sendState(socket, sessionId, 'speaking')
  send(socket, 'character.behavior', sessionId, {
    emotion: 'neutral',
    expression: 'smile',
    motion: 'talk',
    intensity: 0.5,
  })

  const decoder = new TextDecoder()
  let buffer = ''
  let assistantText = ''

  for await (const chunk of response.body) {
    buffer += decoder.decode(chunk, { stream: true })
    const lines = buffer.split('\n')
    buffer = lines.pop() || ''

    for (const line of lines) {
      const trimmed = line.trim()
      if (!trimmed.startsWith('data:')) {
        continue
      }

      const payload = trimmed.slice(5).trim()
      if (payload === '[DONE]') {
        continue
      }

      try {
        const data = JSON.parse(payload) as {
          choices?: Array<{ delta?: { content?: string } }>
        }
        const delta = data.choices?.[0]?.delta?.content
        if (!delta) {
          continue
        }

        assistantText += delta
        send(socket, 'assistant.delta', sessionId, { text: delta })
      }
      catch {
        // Ignore malformed provider chunks and continue streaming.
      }
    }
  }

  history.push({ role: 'assistant', content: assistantText })
  saveMessage(sessionId, 'assistant', assistantText)
  sendMemoryUpdated(socket, sessionId)
  send(socket, 'assistant.message', sessionId, { text: assistantText })
  const audio = await requestAudioOutput(assistantText)
  if (audio?.ok && audio.audioUrl) {
    send(socket, 'audio.tts-ready', sessionId, {
      audioUrl: audio.audioUrl,
      durationMs: audio.durationMs ?? null,
    })
  }
  sendState(socket, sessionId, 'idle')
  send(socket, 'character.behavior', sessionId, {
    emotion: 'neutral',
    expression: 'neutral',
    motion: 'idle',
    intensity: 0.4,
  })
}

const httpServer = createServer((request, response) => {
  if (request.url === '/health') {
    response.writeHead(200, { 'Content-Type': 'application/json' })
    response.end(JSON.stringify({ ok: true, model }))
    return
  }

  response.writeHead(404, { 'Content-Type': 'application/json' })
  response.end(JSON.stringify({ error: 'not_found' }))
})

const wss = new WebSocketServer({ server: httpServer, path: '/ws' })

wss.on('connection', (socket) => {
  const sessionId = defaultSessionId
  send(socket, 'server.hello', sessionId, {
    name: 'amadeus-agent-server',
    model,
    memoryMessages: countPersistedMessages(sessionId),
    toolPermissions: getToolPermissionState(toolRegistry),
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
      sessions.delete(event.sessionId)
      deleteMessages.run(event.sessionId)
      send(socket, 'server.hello', event.sessionId, {
        name: 'amadeus-agent-server',
        model,
        memoryMessages: 0,
        toolPermissions: getToolPermissionState(toolRegistry),
      })
      sendState(socket, event.sessionId, 'idle')
      return
    }

    if (event.type === 'tool.permission.response') {
      pendingToolPermissions.get(event.payload.requestId)?.(event.payload.approved)
      return
    }

    if (event.type === 'user.message') {
      void streamChat(socket, event.sessionId, event.payload.text).catch((error: unknown) => {
        sendState(socket, event.sessionId, 'error')
        send(socket, 'error', event.sessionId, {
          code: 'runtime_error',
          message: error instanceof Error ? error.message : 'Unknown runtime error.',
        })
      })
    }
  })
})

httpServer.listen(port, host, () => {
  console.log(`Amadeus server listening on http://${host}:${port}`)
  console.log(`WebSocket endpoint ws://${host}:${port}/ws`)
  console.log(`Model ${model}`)
  console.log(`SQLite memory ${databasePath}`)
})

