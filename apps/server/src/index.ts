import {
  applyToolConfig,
  createDefaultToolRegistry,
  getEnabledToolSchemas,
  getToolPermissionState,
} from '@amadeus-agent/amadeus/tools'

import { forwardToolPermissionToPython, relayPythonTurn } from './bridge.js'
import { createLegacyFallbackStreamChat, type LegacyChatMessage } from './legacy-fallback.js'
import { createAmadeusBridgeServer } from './websocket-server.js'
import { randomUUID } from 'node:crypto'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { existsSync, mkdirSync } from 'node:fs'
import { DatabaseSync } from 'node:sqlite'

import { config } from 'dotenv'
import { type WebSocket } from 'ws'

const serverDir = dirname(fileURLToPath(import.meta.url))
const rootDir = resolve(serverDir, '../../..')

config({ path: resolve(rootDir, '.env') })

const host = process.env.AMADEUS_SERVER_HOST || '127.0.0.1'
const port = Number(process.env.AMADEUS_SERVER_PORT || 8788)
const baseUrl = process.env.OPENAI_BASE_URL || 'https://api.deepseek.com'
const apiKey = process.env.OPENAI_API_KEY || ''
const model = process.env.OPENAI_MODEL || 'deepseek-v4-flash'
const pythonRuntimeUrl = process.env.AMADEUS_PYTHON_RUNTIME_URL || process.env.AMADEUS_PYTHON_TOOLS_URL || 'http://127.0.0.1:8790'
const enableTypeScriptFallback = process.env.AMADEUS_ENABLE_TS_FALLBACK === 'true'
const defaultSessionId = 'default'
const dataDir = resolve(rootDir, 'data')
const databasePath = resolve(dataDir, 'amadeus.sqlite')
const toolsConfigPath = resolve(rootDir, 'configs/tools.yaml')

const sessions = new Map<string, LegacyChatMessage[]>()
const pendingToolPermissions = new Map<string, (approved: boolean) => void>()

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

function saveMessage(sessionId: string, role: 'user' | 'assistant', content: string): void {
  insertMessage.run(sessionId, role, content, new Date().toISOString())
}

function loadMessages(sessionId: string, limit = 40): LegacyChatMessage[] {
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

const legacyFallbackStreamChat = createLegacyFallbackStreamChat({
  baseUrl,
  apiKey,
  model,
  pythonRuntimeUrl,
  tools,
  toolRegistry,
  sessions,
  pendingToolPermissions,
  saveMessage,
  loadMessages,
  countPersistedMessages,
})
async function streamChat(socket: WebSocket, sessionId: string, userText: string): Promise<void> {
  const handledByPython = await relayPythonTurn(socket, sessionId, userText, { runtimeUrl: pythonRuntimeUrl })
  if (handledByPython) {
    return
  }

  if (enableTypeScriptFallback) {
    await legacyFallbackStreamChat(socket, sessionId, userText)
    return
  }

  socket.send(JSON.stringify({
    id: randomUUID(),
    type: 'error',
    sessionId,
    timestamp: new Date().toISOString(),
    payload: {
      code: 'python_runtime_unavailable',
      message: 'Python runtime did not accept the turn. Start the Python runtime or set AMADEUS_ENABLE_TS_FALLBACK=true to use the legacy TypeScript fallback.',
    },
  }))
}
const { httpServer } = createAmadeusBridgeServer({
  model,
  defaultSessionId,
  countPersistedMessages,
  getToolPermissions: () => getToolPermissionState(toolRegistry),
  resetSession(sessionId) {
    sessions.delete(sessionId)
    deleteMessages.run(sessionId)
  },
  resolvePendingToolPermission(requestId, approved) {
    const pending = pendingToolPermissions.get(requestId)
    if (!pending) {
      return false
    }

    pending(approved)
    return true
  },
  forwardToolPermissionToPython(requestId, approved) {
    return forwardToolPermissionToPython(requestId, approved, { runtimeUrl: pythonRuntimeUrl })
  },
  streamChat,
})

httpServer.listen(port, host, () => {
  console.log(`Amadeus server listening on http://${host}:${port}`)
  console.log(`WebSocket endpoint ws://${host}:${port}/ws`)
  console.log(`Model ${model}`)
  console.log(`SQLite memory ${databasePath}`)
  console.log(`Legacy TypeScript fallback ${enableTypeScriptFallback ? 'enabled' : 'disabled'}`)
})

