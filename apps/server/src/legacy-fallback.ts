import type {
  AssistantState,
  RuntimeEvent,
  ServerRuntimeEvent,
} from '@amadeus-agent/amadeus/events'
import type {
  RegisteredTool,
  ToolCall,
  ToolSchema,
} from '@amadeus-agent/amadeus/tools'

import { randomUUID } from 'node:crypto'
import { type WebSocket } from 'ws'

export interface LegacyChatMessage {
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

export interface LegacyFallbackOptions {
  baseUrl: string
  apiKey: string
  model: string
  pythonRuntimeUrl: string
  tools: ToolSchema[]
  toolRegistry: Record<string, RegisteredTool>
  sessions: Map<string, LegacyChatMessage[]>
  pendingToolPermissions: Map<string, (approved: boolean) => void>
  saveMessage(sessionId: string, role: 'user' | 'assistant', content: string): void
  loadMessages(sessionId: string, limit?: number): LegacyChatMessage[]
  countPersistedMessages(sessionId: string): number
}

const systemPrompt: LegacyChatMessage = {
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

function parseToolArguments(raw: string): Record<string, unknown> {
  try {
    const parsed = JSON.parse(raw) as Record<string, unknown>
    return parsed && typeof parsed === 'object' ? parsed : {}
  }
  catch {
    return {}
  }
}

function getHistory(options: LegacyFallbackOptions, sessionId: string): LegacyChatMessage[] {
  const existing = options.sessions.get(sessionId)
  if (existing) {
    return existing
  }

  const created: LegacyChatMessage[] = [systemPrompt, ...options.loadMessages(sessionId)]
  options.sessions.set(sessionId, created)
  return created
}

function sendMemoryUpdated(options: LegacyFallbackOptions, socket: WebSocket, sessionId: string): void {
  send(socket, 'memory.updated', sessionId, {
    memoryMessages: options.countPersistedMessages(sessionId),
  })
}

async function requestToolPermission(
  options: LegacyFallbackOptions,
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
      options.pendingToolPermissions.delete(requestId)
      resolve(false)
    }, 30000)

    options.pendingToolPermissions.set(requestId, (approved) => {
      clearTimeout(timeout)
      options.pendingToolPermissions.delete(requestId)
      resolve(approved)
    })
  })
}

async function executeToolCall(
  options: LegacyFallbackOptions,
  socket: WebSocket,
  sessionId: string,
  toolCall: ToolCall,
): Promise<LegacyChatMessage> {
  const toolName = toolCall.function.name
  const tool = options.toolRegistry[toolName]
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

    const approved = await requestToolPermission(options, socket, sessionId, tool, args)
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

async function requestToolDecision(options: LegacyFallbackOptions, messages: LegacyChatMessage[]): Promise<ChatChoiceMessage> {
  const response = await fetch(`${options.baseUrl.replace(/\/$/, '')}/chat/completions`, {
    method: 'POST',
    headers: {
      Authorization: `Bearer ${options.apiKey}`,
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({
      model: options.model,
      messages,
      tools: options.tools,
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

async function requestAudioOutput(options: LegacyFallbackOptions, text: string): Promise<AudioSpeakResponse | undefined> {
  const normalizedText = text.trim()
  if (!normalizedText) {
    return undefined
  }

  try {
    const response = await fetch(`${options.pythonRuntimeUrl.replace(/\/$/, '')}/audio/speak`, {
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

export function createLegacyFallbackStreamChat(options: LegacyFallbackOptions) {
  return async function streamChat(socket: WebSocket, sessionId: string, userText: string): Promise<void> {
    if (!options.apiKey) {
      send(socket, 'error', sessionId, {
        code: 'missing_api_key',
        message: 'OPENAI_API_KEY is not configured.',
      })
      return
    }

    const history = getHistory(options, sessionId)
    history.push({ role: 'user', content: userText })
    options.saveMessage(sessionId, 'user', userText)
    sendMemoryUpdated(options, socket, sessionId)

    sendState(socket, sessionId, 'thinking')
    send(socket, 'character.behavior', sessionId, {
      emotion: 'focused',
      expression: 'serious',
      motion: 'think',
      intensity: 0.6,
    })

    const toolDecision = await requestToolDecision(options, history)
    const toolCalls = toolDecision.tool_calls ?? []

    if (toolCalls.length > 0) {
      history.push({
        role: 'assistant',
        content: toolDecision.content ?? '',
        tool_calls: toolCalls,
      })

      for (const toolCall of toolCalls) {
        history.push(await executeToolCall(options, socket, sessionId, toolCall))
      }
    }

    const response = await fetch(`${options.baseUrl.replace(/\/$/, '')}/chat/completions`, {
      method: 'POST',
      headers: {
        Authorization: `Bearer ${options.apiKey}`,
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        model: options.model,
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
    options.saveMessage(sessionId, 'assistant', assistantText)
    sendMemoryUpdated(options, socket, sessionId)
    send(socket, 'assistant.message', sessionId, { text: assistantText })
    const audio = await requestAudioOutput(options, assistantText)
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
}
