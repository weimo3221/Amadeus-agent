export type AssistantState =
  | 'idle'
  | 'listening'
  | 'thinking'
  | 'speaking'
  | 'tool-running'
  | 'error'

export interface RuntimeEvent<TType extends string = string, TPayload = unknown> {
  id: string
  type: TType
  sessionId: string
  timestamp: string
  payload: TPayload
}

export interface ErrorPayload {
  code: string
  message: string
}

export interface HelloPayload {
  name: string
  model: string
  memoryMessages: number
}

export type ClientRuntimeEvent =
  | RuntimeEvent<'user.message', UserMessagePayload>
  | RuntimeEvent<'session.reset', Record<string, never>>
  | RuntimeEvent<'tool.permission.response', ToolPermissionResponsePayload>

export type ServerRuntimeEvent =
  | RuntimeEvent<'server.hello', HelloPayload>
  | RuntimeEvent<'memory.updated', MemoryUpdatedPayload>
  | RuntimeEvent<'assistant.delta', AssistantDeltaPayload>
  | RuntimeEvent<'assistant.message', AssistantMessagePayload>
  | RuntimeEvent<'assistant.state', AssistantStatePayload>
  | RuntimeEvent<'character.behavior', CharacterBehaviorPayload>
  | RuntimeEvent<'tool.started', ToolStartedPayload>
  | RuntimeEvent<'tool.finished', ToolFinishedPayload>
  | RuntimeEvent<'tool.permission.request', ToolPermissionRequestPayload>
  | RuntimeEvent<'error', ErrorPayload>

export interface UserMessagePayload {
  text: string
  inputMode: 'text' | 'voice'
}

export interface AssistantDeltaPayload {
  text: string
}

export interface AssistantMessagePayload {
  text: string
}

export interface AssistantStatePayload {
  state: AssistantState
}

export interface CharacterBehaviorPayload {
  emotion: string
  expression: string
  motion: string
  intensity?: number
}

export interface ToolStartedPayload {
  toolName: string
  displayName: string
}

export interface ToolFinishedPayload {
  toolName: string
  ok: boolean
}

export interface ToolPermissionRequestPayload {
  requestId: string
  toolName: string
  displayName: string
  reason: string
}

export interface ToolPermissionResponsePayload {
  requestId: string
  approved: boolean
}

export interface MemoryUpdatedPayload {
  memoryMessages: number
}
