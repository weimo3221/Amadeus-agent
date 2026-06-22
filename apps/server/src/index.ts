import {
  fetchPythonToolPermissions,
  type ToolPermissionState,
} from '@amadeus-agent/amadeus/tools'

import {
  acceptPythonMemoryReviewCandidate,
  forwardToolPermissionToPython,
  forwardRuntimeFeedbackToPython,
  listPythonMemoryReviewCandidates,
  listPythonMemoryReviewJobs,
  rejectPythonMemoryReviewCandidate,
  relayPythonTurn,
  runPythonMemoryReview,
} from './bridge.js'
import { LocalLive2DModelLibrary } from './live2d.js'
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
const serverBaseUrl = process.env.AMADEUS_SERVER_URL || `http://${host}:${port}`
const model = process.env.OPENAI_MODEL || 'deepseek-v4-flash'
const pythonRuntimeUrl = process.env.AMADEUS_PYTHON_RUNTIME_URL || process.env.AMADEUS_PYTHON_TOOLS_URL || 'http://127.0.0.1:8790'
const defaultSessionId = 'default'
const dataDir = resolve(rootDir, 'data')
const databasePath = resolve(dataDir, 'amadeus.sqlite')
const live2dRoot = process.env.AMADEUS_LIVE2D_ROOT || resolve(rootDir, 'models/live2d')
const harnessesConfigPath = resolve(rootDir, 'configs/harnesses.yaml')
const pendingToolPermissions = new Map<string, (approved: boolean) => void>()
const live2dLibrary = new LocalLive2DModelLibrary(live2dRoot, serverBaseUrl, harnessesConfigPath)

const pythonToolsUnavailable: ToolPermissionState[] = [{
  name: 'python_runtime_unavailable',
  displayName: 'Python tools unavailable',
  enabled: false,
  permission: 'deny',
}]

async function getPythonToolPermissions(): Promise<ToolPermissionState[]> {
  const permissions = await fetchPythonToolPermissions({
    baseUrl: pythonRuntimeUrl,
    timeoutMs: 1500,
  })
  return permissions ?? pythonToolsUnavailable
}

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

const deleteMessages = db.prepare(`
DELETE FROM messages
WHERE session_id = ?
`)

const countMessages = db.prepare(`
SELECT COUNT(*) as count
FROM messages
WHERE session_id = ?
`)

function countPersistedMessages(sessionId: string): number {
  const row = countMessages.get(sessionId) as { count: number } | undefined
  return row?.count ?? 0
}

async function streamChat(socket: WebSocket, sessionId: string, userText: string): Promise<void> {
  const handledByPython = await relayPythonTurn(socket, sessionId, userText, { runtimeUrl: pythonRuntimeUrl })
  if (handledByPython) {
    return
  }

  socket.send(JSON.stringify({
    id: randomUUID(),
    type: 'error',
    sessionId,
    timestamp: new Date().toISOString(),
    payload: {
      code: 'python_runtime_unavailable',
      message: 'Python runtime did not accept the turn. Start the Python runtime and try again.',
    },
  }))
}
const { httpServer } = createAmadeusBridgeServer({
  model,
  defaultSessionId,
  countPersistedMessages,
  getToolPermissions: getPythonToolPermissions,
  resetSession(sessionId) {
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
  listMemoryReviewCandidates(sessionId, status) {
    return listPythonMemoryReviewCandidates(sessionId, status, { runtimeUrl: pythonRuntimeUrl })
  },
  listMemoryReviewJobs(sessionId, status) {
    return listPythonMemoryReviewJobs(sessionId, status, { runtimeUrl: pythonRuntimeUrl })
  },
  runMemoryReview(sessionId, force) {
    return runPythonMemoryReview(sessionId, force, { runtimeUrl: pythonRuntimeUrl })
  },
  acceptMemoryReviewCandidate(candidateId) {
    return acceptPythonMemoryReviewCandidate(candidateId, { runtimeUrl: pythonRuntimeUrl })
  },
  rejectMemoryReviewCandidate(candidateId) {
    return rejectPythonMemoryReviewCandidate(candidateId, { runtimeUrl: pythonRuntimeUrl })
  },
  observeDesktopFeedback(event) {
    return forwardRuntimeFeedbackToPython(event, { runtimeUrl: pythonRuntimeUrl })
  },
  live2dLibrary,
  streamChat,
})

httpServer.listen(port, host, () => {
  console.log(`Amadeus server listening on http://${host}:${port}`)
  console.log(`WebSocket endpoint ws://${host}:${port}/ws`)
  console.log(`Model ${model}`)
  console.log(`SQLite memory ${databasePath}`)
})
