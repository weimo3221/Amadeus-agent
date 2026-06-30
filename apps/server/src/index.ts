import {
  fetchPythonToolPermissions,
  type ToolPermissionState,
} from '@amadeus-agent/amadeus/tools'

import {
  acceptPythonMemoryReviewCandidate,
  fetchPythonMemoryCount,
  forwardToolPermissionToPython,
  forwardRuntimeFeedbackToPython,
  listPythonMemoryReviewCandidates,
  listPythonMemoryReviewJobs,
  proxyPythonSkillsRequest,
  proxyPythonSessionRequest,
  proxyPythonTaskRequest,
  proxyPythonLive2DRequest,
  rejectPythonMemoryReviewCandidate,
  relayPythonTurn,
  resetPythonMemory,
  runPythonMemoryReview,
} from './bridge.js'
import { createAmadeusBridgeServer, type BridgeSocket } from './websocket-server.js'
import { randomUUID } from 'node:crypto'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

import { config } from 'dotenv'

const serverDir = dirname(fileURLToPath(import.meta.url))
const rootDir = resolve(serverDir, '../../..')

config({ path: resolve(rootDir, '.env') })

const host = process.env.AMADEUS_SERVER_HOST || '127.0.0.1'
const port = Number(process.env.AMADEUS_SERVER_PORT || 8788)
const serverBaseUrl = process.env.AMADEUS_SERVER_URL || `http://${host}:${port}`
const model = process.env.OPENAI_MODEL || 'deepseek-v4-flash'
const pythonRuntimeUrl = process.env.AMADEUS_PYTHON_RUNTIME_URL || process.env.AMADEUS_PYTHON_TOOLS_URL || 'http://127.0.0.1:8790'
const defaultSessionId = 'default'

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

async function streamChat(socket: BridgeSocket, sessionId: string, userText: string, skills?: string[]): Promise<void> {
  const handledByPython = await relayPythonTurn(socket, sessionId, userText, skills, { runtimeUrl: pythonRuntimeUrl })
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
  getMemoryMessageCount(sessionId) {
    return fetchPythonMemoryCount(sessionId, { runtimeUrl: pythonRuntimeUrl })
  },
  getToolPermissions: getPythonToolPermissions,
  async resetSession(sessionId) {
    const result = await resetPythonMemory(sessionId, { runtimeUrl: pythonRuntimeUrl })
    if (!result.ok) {
      throw new Error(result.error)
    }
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
  handleLive2DHttpRequest(request, response, requestUrl) {
    return proxyPythonLive2DRequest(request, response, requestUrl, {
      runtimeUrl: pythonRuntimeUrl,
      publicBaseUrl: serverBaseUrl,
    })
  },
  handleSkillsHttpRequest(request, response, requestUrl) {
    return proxyPythonSkillsRequest(request, response, requestUrl, {
      runtimeUrl: pythonRuntimeUrl,
    })
  },
  handleSessionHttpRequest(request, response, requestUrl) {
    return proxyPythonSessionRequest(request, response, requestUrl, {
      runtimeUrl: pythonRuntimeUrl,
    })
  },
  handleTaskHttpRequest(request, response, requestUrl) {
    return proxyPythonTaskRequest(request, response, requestUrl, {
      runtimeUrl: pythonRuntimeUrl,
    })
  },
  streamChat,
})

httpServer.listen(port, host, () => {
  console.log(`Amadeus server listening on http://${host}:${port}`)
  console.log(`WebSocket endpoint ws://${host}:${port}/ws`)
  console.log(`Model ${model}`)
  console.log(`Python runtime ${pythonRuntimeUrl}`)
})
