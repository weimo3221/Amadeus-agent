import type {
  AssistantState,
  ClientRuntimeEvent,
  RuntimeEvent,
  ServerRuntimeEvent,
} from '@amadeus-agent/shared'

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
const defaultSessionId = 'default'
const dataDir = resolve(rootDir, 'data')
const databasePath = resolve(dataDir, 'amadeus.sqlite')

interface ChatMessage {
  role: 'system' | 'user' | 'assistant' | 'tool'
  content: string
  tool_call_id?: string
  tool_calls?: ToolCall[]
}

interface ToolCall {
  id: string
  type: 'function'
  function: {
    name: string
    arguments: string
  }
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

interface ToolSchema {
  type: 'function'
  function: {
    name: string
    description: string
    parameters: Record<string, unknown>
  }
}

interface ToolContext {
  socket: WebSocket
  sessionId: string
}

type ToolPermission = 'allow' | 'ask' | 'deny'

interface RegisteredTool {
  name: string
  displayName: string
  permission: ToolPermission
  enabled: boolean
  schema: ToolSchema
  describeRequest?: (args: Record<string, unknown>) => string
  execute: (args: Record<string, unknown>, context: ToolContext) => string | Promise<string>
}

const sessions = new Map<string, ChatMessage[]>()
const pendingToolPermissions = new Map<string, (approved: boolean) => void>()

function normalizePositiveInteger(value: unknown, fallback: number, min: number, max: number): number {
  const number = typeof value === 'number' ? value : Number(value)
  if (!Number.isFinite(number)) {
    return fallback
  }

  return Math.max(min, Math.min(max, Math.floor(number)))
}
const systemPrompt: ChatMessage = {
  role: 'system',
  content: [
    'You are Amadeus, a desktop Live2D companion agent.',
    'Reply in the same language as the user unless they ask otherwise.',
    'Be concise, practical, and calm.',
    'You can use a safe local current-time tool. For other tools, say they are not implemented yet.',
    'When the user asks for the current time, current date, today, now, or scheduling context, you must call get_current_time before answering.',
    'When the user asks to roll dice or generate a dice result, call roll_dice.',
    'Do not answer current time or date questions from memory or estimation.',
  ].join('\n'),
}

const toolRegistry: Record<string, RegisteredTool> = {
  get_current_time: {
    name: 'get_current_time',
    displayName: 'Reading current time',
    permission: 'allow',
    enabled: true,
    schema: {
      type: 'function',
      function: {
        name: 'get_current_time',
        description: 'Get the current local date and time. Use this when the user asks about current time, date, today, now, or scheduling context.',
        parameters: {
          type: 'object',
          properties: {
            timeZone: {
              type: 'string',
              description: 'IANA timezone. Defaults to Asia/Shanghai.',
            },
          },
          additionalProperties: false,
        },
      },
    },
    execute: (args) => {
      const timeZone = typeof args.timeZone === 'string' && args.timeZone ? args.timeZone : 'Asia/Shanghai'
      const now = new Date()
      const formatter = new Intl.DateTimeFormat('zh-CN', {
        timeZone,
        dateStyle: 'full',
        timeStyle: 'medium',
      })
      return JSON.stringify({
        iso: now.toISOString(),
        timeZone,
        formatted: formatter.format(now),
      })
    },
  },
  roll_dice: {
    name: 'roll_dice',
    displayName: 'Rolling dice',
    permission: 'ask',
    enabled: true,
    schema: {
      type: 'function',
      function: {
        name: 'roll_dice',
        description: 'Roll dice and return the random results. Use this when the user asks to roll dice.',
        parameters: {
          type: 'object',
          properties: {
            sides: {
              type: 'number',
              description: 'Number of sides per die. Defaults to 6.',
            },
            count: {
              type: 'number',
              description: 'Number of dice to roll. Defaults to 1 and is capped at 20.',
            },
          },
          additionalProperties: false,
        },
      },
    },
    describeRequest: (args) => {
      const sides = normalizePositiveInteger(args.sides, 6, 2, 1000)
      const count = normalizePositiveInteger(args.count, 1, 1, 20)
      return `Allow Amadeus to roll ${count} d${sides}?`
    },
    execute: (args) => {
      const sides = normalizePositiveInteger(args.sides, 6, 2, 1000)
      const count = normalizePositiveInteger(args.count, 1, 1, 20)
      const rolls = Array.from({ length: count }, () => Math.floor(Math.random() * sides) + 1)
      return JSON.stringify({
        sides,
        count,
        rolls,
        total: rolls.reduce((sum, value) => sum + value, 0),
      })
    },
  },
}

const tools = Object.values(toolRegistry)
  .filter((tool) => tool.enabled && tool.permission !== 'deny')
  .map((tool) => tool.schema)

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

    const result = await tool.execute(args, { socket, sessionId })
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

