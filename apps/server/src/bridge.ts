import type { RuntimeEvent, ServerRuntimeEvent } from '@amadeus-agent/amadeus/events'

import { randomUUID } from 'node:crypto'

export interface SocketLike {
  send(data: string): void
}

export interface PythonBridgeOptions {
  runtimeUrl: string
  fetchImpl?: typeof fetch
}

function runtimeEndpoint(runtimeUrl: string, path: string): string {
  return `${runtimeUrl.replace(/\/$/, '')}${path}`
}

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

function sendError(socket: SocketLike, sessionId: string, code: string, message: string): void {
  socket.send(JSON.stringify(makeEvent('error', sessionId, { code, message })))
}

export async function relayPythonTurn(
  socket: SocketLike,
  sessionId: string,
  userText: string,
  options: PythonBridgeOptions,
): Promise<boolean> {
  const fetchImpl = options.fetchImpl ?? fetch
  let response: Response
  try {
    response = await fetchImpl(runtimeEndpoint(options.runtimeUrl, '/agent/turn'), {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        sessionId,
        text: userText,
        inputMode: 'text',
      }),
    })
  }
  catch {
    return false
  }

  if (!response.ok || !response.body) {
    return false
  }

  const decoder = new TextDecoder()
  let buffer = ''

  for await (const chunk of response.body) {
    buffer += decoder.decode(chunk, { stream: true })
    const lines = buffer.split('\n')
    buffer = lines.pop() || ''

    for (const line of lines) {
      const trimmed = line.trim()
      if (!trimmed) {
        continue
      }

      try {
        const event = JSON.parse(trimmed) as RuntimeEvent<string, unknown>
        socket.send(JSON.stringify(event))
      }
      catch {
        sendError(socket, sessionId, 'bad_python_event', 'Python runtime emitted an invalid event.')
      }
    }
  }

  const tail = buffer.trim()
  if (tail) {
    try {
      const event = JSON.parse(tail) as RuntimeEvent<string, unknown>
      socket.send(JSON.stringify(event))
    }
    catch {
      sendError(socket, sessionId, 'bad_python_event', 'Python runtime emitted an invalid trailing event.')
    }
  }

  return true
}

export async function forwardToolPermissionToPython(
  requestId: string,
  approved: boolean,
  options: PythonBridgeOptions,
): Promise<void> {
  const fetchImpl = options.fetchImpl ?? fetch
  try {
    await fetchImpl(runtimeEndpoint(options.runtimeUrl, '/tools/permission'), {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({ requestId, approved }),
    })
  }
  catch {
    // The legacy TypeScript tool loop may own this request, or the Python turn
    // may have already timed out. Either way, there is nothing else to do here.
  }
}
